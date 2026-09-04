// dsh-timesfm host 入口 v0.1：
// 1. 守护 Python 预测服务子进程（127.0.0.1:8920）
// 2. webServer 挂 /timesfm 前缀路由，代理到 Python 服务
// 官方姿势对照（RAG 知识库 "dsh 插件接入标准"）：
//   - 模块级 export inject 声明依赖（better-sidebar 模式）
//   - ctx.effect(() => ctx.webServer.register({kind:'prefix',...}), 'desc')（dshmarket/better-sidebar 模式）
//   - cordis ctx 禁止任意属性赋值/读取（会报 "cannot get property without inject"）

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY_SERVICE_URL = 'http://127.0.0.1:8920';
const PY_BIN = path.join(PLUGIN_ROOT, '.venv', 'bin', 'python');

export const name = 'dsh-timesfm';
export const inject = ['webServer'];

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
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'prefix',
        path: '/timesfm',
        handler: proxyHandler,
      }),
    'dsh-timesfm: http routes',
  );
  logger?.info?.('[dsh-timesfm] apply 完成 — http /timesfm/* → 127.0.0.1:8920');
}
