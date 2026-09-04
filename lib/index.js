// dsh-timesfm host 入口（骨架 v0.1）：
// 1. 守护 Python 预测服务子进程（127.0.0.1:8920）
// 2. 暴露 cordis service：timesfm.forecast() / timesfm.anomaly() / timesfm.watchCheck()
// 姿势对照 dshmarket@1.41.0 / dsh-better-sidebar@0.18.0（见 RAG 知识库 "dsh 插件接入标准"）。

import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const PY_SERVICE_URL = 'http://127.0.0.1:8920';
const PY_BIN = path.join(PLUGIN_ROOT, '.venv', 'bin', 'python');

export const name = 'dsh-timesfm';

let pyProc = null;

async function pyAlive() {
  try {
    const res = await fetch(`${PY_SERVICE_URL}/health`, { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

async function ensurePyService(ctx) {
  if (await pyAlive()) return;
  const logger = ctx?.logger ?? console;
  logger?.info?.('[dsh-timesfm] 拉起 Python 预测服务');
  pyProc = spawn(PY_BIN, ['-m', 'core.server'], {
    cwd: PLUGIN_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  pyProc.on('exit', (code) => {
    logger?.warn?.(`[dsh-timesfm] Python 服务退出 code=${code}`);
    pyProc = null;
  });
  // 首次加载模型可能要数十秒，轮询至 health ok
  for (let i = 0; i < 60; i++) {
    if (await pyAlive()) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
  logger?.error?.('[dsh-timesfm] Python 服务健康检查超时');
}

async function pyPost(p, body) {
  const res = await fetch(`${PY_SERVICE_URL}${p}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`timesfm ${res.status}: ${await res.text()}`);
  return res.json();
}

/** cordis service：其他插件经 ctx 获取后进程内直调 */
const timesfmService = {
  async forecast(series, horizon = 24, quantiles = [0.1, 0.5, 0.9]) {
    return pyPost('/api/v1/forecast', { series, horizon, quantiles });
  },
  async anomaly(series, actual) {
    return pyPost('/api/v1/anomaly', { series, actual });
  },
  async watchCheck(name, series, horizon = 6, band = [0.1, 0.9]) {
    return pyPost('/api/v1/watch/check', { name, series, horizon, band });
  },
  health: () => pyAlive(),
};

/** HTTP 路由挂到 webServer（/timesfm/api/v1/* → Python 服务代理）。webServer 不可得时跳过。 */
async function mountRoutes(ctx) {
  try {
    const webServer = ctx?.dsh?.webServer ?? (await ctx?.dsh?.webServer?.());
    if (!webServer?.mountRoute && !webServer?.use) return;
    const mount = webServer.mountRoute ?? webServer.use;
    const proxy = (sub) => async (req, res) => {
      try {
        const body = await new Promise((resolve) => {
          let raw = '';
          req.on('data', (c) => (raw += c));
          req.on('end', () => { try { resolve(JSON.parse(raw || '{}')); } catch { resolve({}); } });
        });
        const out = await pyPost(`/api/v1/${sub}`, body);
        res.json(out);
      } catch (e) {
        res.status(502).json({ error: String(e) });
      }
    };
    mount('/timesfm/api/v1/forecast', proxy('forecast'));
    mount('/timesfm/api/v1/anomaly', proxy('anomaly'));
    mount('/timesfm/api/v1/watch/check', proxy('watch/check'));
  } catch (e) {
    console.warn('[dsh-timesfm] webServer 路由挂载跳过:', e?.message ?? e);
  }
}

export default async function apply(ctx) {
  await ensurePyService(ctx);
  // cordis service 注册：官方姿势为 ctx.scope / plugin Provide，先挂到 ctx.dsh 命名空间供宿主与插件取用
  ctx.dsh = ctx.dsh ?? {};
  ctx.dsh.timesfm = timesfmService;
  await mountRoutes(ctx);
  console.info('[dsh-timesfm] ready — service: ctx.dsh.timesfm, http: /timesfm/api/v1/*');
}
