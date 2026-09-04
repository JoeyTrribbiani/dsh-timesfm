// dsh-timesfm host 入口 v0.2：
// 1. 守护 Python 预测服务子进程（127.0.0.1:8920）
// 2. webServer 挂 /timesfm 前缀路由，代理到 Python 服务
// 3. cordis service：ctx.provide('timesfm', ...) 供其他插件进程内直调
// 4. agent tool：timesfm_forecast 注册进 dsh agent（defineTool + ctx.tools.register）
// 官方姿势实证（RAG 知识库 "dsh 插件接入标准"）：
//   - 模块级 export inject 声明依赖 / export provide 声明提供（loader 读取）
//   - ctx.provide(name, value) 注册服务（get 侧需 inject，set 侧需 provide）
//   - ctx.effect(() => ctx.webServer.register({kind:'prefix',...}), 'desc')
//   - ctx.tools.register(defineTool({name, description, parameters, output, execute}))

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineTool } from '@deepseek-ai/dsh-tools';

const PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY_SERVICE_URL = 'http://127.0.0.1:8920';
const PY_BIN = path.join(PLUGIN_ROOT, '.venv', 'bin', 'python');

export const name = 'dsh-timesfm';
export const inject = ['webServer', 'tools'];
export const provide = ['timesfm'];

let pyProc = null;

async function pyAlive() {
  try {
    const res = await fetch(`${PY_SERVICE_URL}/health`, { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

/** 拉起 Python 预测服务（fire-and-forget，不阻塞 boot；已在跑则复用）。 */
async function ensurePyService(logger) {
  if (await pyAlive()) {
    logger?.info?.('[dsh-timesfm] 复用已在运行的 Python 预测服务 :8920');
    return;
  }
  logger?.info?.('[dsh-timesfm] 拉起 Python 预测服务');
  pyProc = spawn(PY_BIN, ['-m', 'core.server'], {
    cwd: PLUGIN_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  pyProc.on('exit', (code) => {
    logger?.warn?.(`[dsh-timesfm] Python 服务退出 code=${code}`);
    pyProc = null;
  });
  for (let i = 0; i < 90; i++) {
    if (await pyAlive()) {
      logger?.info?.('[dsh-timesfm] Python 预测服务就绪');
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  logger?.error?.('[dsh-timesfm] Python 服务健康检查超时（90s）');
}

async function pyPost(p, body) {
  const res = await fetch(`${PY_SERVICE_URL}${p}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) throw new Error(`timesfm ${res.status}: ${await res.text()}`);
  return res.json();
}

/** cordis service：其他插件经 inject: ['timesfm'] 进程内直调。 */
const timesfmService = {
  forecast: (series, horizon = 24, quantiles = [0.1, 0.5, 0.9]) =>
    pyPost('/api/v1/forecast', { series, horizon, quantiles }),
  forecastBatch: (seriesList, horizon = 24, quantiles = [0.1, 0.5, 0.9]) =>
    pyPost('/api/v1/forecast/batch', { series: seriesList, horizon, quantiles }),
  anomaly: (series, actual) =>
    pyPost('/api/v1/anomaly', { series, actual }),
  watchCheck: (name, series, horizon = 6, band = [0.1, 0.9]) =>
    pyPost('/api/v1/watch/check', { name, series, horizon, band }),
  health: () => pyAlive(),
};

/** /timesfm/* 代理：剥前缀转发到 Python 服务。 */
async function proxyHandler(req, res) {
  const rest = (req.url ?? '/').replace(/^\/timesfm/, '') || '/health';
  try {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = Buffer.concat(chunks);
    const upstream = await fetch(`${PY_SERVICE_URL}${rest}`, {
      method: req.method ?? 'GET',
      headers: { 'content-type': 'application/json' },
      body: body.length ? body : undefined,
      signal: AbortSignal.timeout(120_000),
    });
    res.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') ?? 'application/json' });
    res.end(Buffer.from(await upstream.arrayBuffer()));
  } catch (e) {
    res.writeHead(502, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: `timesfm upstream: ${String(e)}` }));
  }
}

export function apply(ctx, config) {
  const logger = ctx?.logger ?? console;
  ensurePyService(logger);

  // cordis service：声明在 export provide，值在这里给
  ctx.provide('timesfm', timesfmService);

  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'prefix',
        path: '/timesfm',
        handler: proxyHandler,
      }),
    'dsh-timesfm: http routes',
  );

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'timesfm_forecast',
          description:
            'Zero-shot time-series forecast via TimesFM (local inference). Pass a 1-D series of numeric observations (oldest → newest, ≥10 points) and get median point forecasts plus quantile bands for the next steps. Use it to predict service latency/error-rate trends, test-throughput, or any numeric series; values outside the returned quantile band indicate anomalies.',
          parameters: {
            series: {
              type: 'array',
              required: true,
              description: '一维时序观测值（旧→新），至少 10 个数字',
            },
            horizon: {
              type: 'integer',
              description: '预测步数，默认 24（1..256）。可选参数省略 required 字段（dsh-tools 规则：required 出现时必须为 true）',
            },
            quantiles: {
              type: 'array',
              description: '分位档列表，默认 [0.1, 0.5, 0.9]，仅支持 0.1..0.9 九档',
            },
          },
          output: {
            schema: {
              type: 'object',
              additionalProperties: false,
              properties: {
                point: { type: 'array', description: '中位数预测序列' },
                quantiles: { type: 'object', additionalProperties: true, description: '分位带，键为 "0.1"/"0.5"/"0.9" 等' },
                model: { type: 'string' },
                n_obs: { type: 'integer' },
              },
            },
          },
          execute: async (args) => {
            const out = await timesfmService.forecast(
              args.series,
              args.horizon ?? 24,
              args.quantiles ?? [0.1, 0.5, 0.9],
            );
            return {
              point: out.point,
              quantiles: out.quantiles,
              model: out.model,
              n_obs: out.n_obs,
            };
          },
        }),
      ),
    'dsh-timesfm: agent tool',
  );

  logger?.info?.('[dsh-timesfm] apply 完成 — service: timesfm, http: /timesfm/*, tool: timesfm_forecast');
}
