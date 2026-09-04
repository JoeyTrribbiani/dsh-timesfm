# dsh-timesfm

> 公共 dsh 插件：给整个 dsh 生态提供零样本时序预测能力（Google TimesFM，本地推理）。
> 后面任何项目都能用，标准接入，不绑定 qa-platform。

## 定位

像 dsh-better-sidebar 一样的基础设施型公共插件：装一次，所有 dsh profile 可用；
对外暴露**标准预测服务**，其他插件和外部项目按接口接入，各自不碰 timesfm 细节。

```
dsh plugin --profile <name> add dsh-timesfm   # 一行装好
```

## 标准接入（三层）

| 层 | 面向 | 方式 |
|----|------|------|
| cordis service | dsh 插件间 | `ctx` 获取 forecast service，进程内直调 |
| HTTP API | 非 dsh 进程（RAG 服务、qa-platform、seekdb 侧脚本） | webServer 挂 REST 路由 |
| agent tool | dsh agent 会话 | `defineTool` 注册，AI 直接调用 |

### 1. cordis service（插件间）

```js
// 其他插件内
const timesfm = ctx.dsh.timesfm;            // 预测服务
const out = await timesfm.forecast({
  series: [...],                             // 一维时序
  horizon: 24,                               // 预测步数
  quantiles: [0.1, 0.9],                     // 置信区间
});
```

### 2. HTTP API（外部项目）

```
POST /timesfm/api/v1/forecast                # 单次预测：series + horizon → point + quantiles
POST /timesfm/api/v1/watch                   # 注册监控目标：{name, source, metric, interval}
GET  /timesfm/api/v1/watch                   # 列出已注册目标 + 最新预测/告警状态
```

- `watch` 注册后插件按 interval 拉数、预测、值冲出分位区间 → 生成告警事件（cordis 广播 + HTTP 可查）
- 后续项目只管注册自己的数据源，异常检测逻辑全在插件里

### 3. agent tool

`timesfm_forecast` 工具注册进 dsh agent：会话里说"预测一下这个序列"就能调。

## 内置场景默认值

| 场景 | 数据源 | 用途 |
|------|--------|------|
| 服务健康 | 3080/8900/2881 响应时间、错误率 | 异常检测告警 |
| 测试质量 | qa-platform 通过率、执行时长 | 跌破区间预警 |
| 容量 | seekdb/RAG 负载趋势 | 扩容预判 |

## 模型

- `google/timesfm-2.5-200m-pytorch`（~800MB，下载走 `HF_ENDPOINT=https://hf-mirror.com`）
- Apple Silicon MPS 加速，RAM ~1.5-3GB，100% 本地推理零外部 API

## 接入标准实现依据

按官方插件规范（实证知识已入 RAG 知识库 127.0.0.1:8900，检索 "dsh 插件接入标准"，domain=dsh）：

- 宿主包 `peerDependencies`（`@deepseek-ai/cordis` / `dsh-settings` / `schemastery`），不打包
- `dsh.bundle.patch` → `cordis.patch.yml`；`dsh.client.inject` 声明 web 侧注入
- 设置用 schemastery schema + settings namespace（`dsh-timesfm`）
- npm 公开发版 → dshmarket 市场收录（awesome-dsh-plugin.com）

## 质量达标标准（市场收录门槛，自设）

1. 插件在真实 dsh profile 稳定加载运行 ≥ 1 周
2. ≥ 2 个真实项目接入（如 qa-platform 监控 + RAG 服务监控）且告警准确
3. 测试套件（非合成数据 smoke）+ typecheck 通过
4. 双语 README + LICENSE + CI
5. 与 dshmarket/better-sidebar 同级工程完整度

## 状态

- [x] 可行性研究（TimesFM 能力/资源/用法）
- [x] 官方插件标准逆向（dshmarket + dsh-better-sidebar 解包实证，知识已入 RAG 库）
- [x] 公共插件定位 + 三层标准接入设计
- [x] Python 预测核心（TimesFMCore：forecast/anomaly_score，smoke 全绿）
- [x] Python 服务壳（FastAPI :8920：/health /api/v1/forecast /api/v1/anomaly /api/v1/watch/check）
- [x] dsh 插件骨架 v0.1（package.json + cordis.patch.yml + LICENSE + lib/index.js：Python 子进程守护 + cordis service + HTTP 代理）
- [ ] 插件接入 profile 实测（dsh plugin add 本地路径 + boot 验证）
- [ ] watch 监控/告警闭环（JS 侧定时拉数）
- [ ] 真实场景接入验证（qa-platform / RAG 监控目标）
- [ ] 工程件：测试套件 / typecheck / 双语 README / CI
- [ ] npm 发版 + 市场收录（远期，达标上述门槛后）
