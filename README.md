# dsh-timesfm

TimesFM 时序预测接入 dsh 生态的插件（规划中）。

## 目标

把 Google TimesFM（零样本时序基础模型，200M 参数本地推理）包装成标准 dsh 插件：

- **服务异常检测** — 监控 dsh 服务（qa-platform :3080 / RAG :8900 / seekdb :2881）响应时间与错误率，冲出 90% 置信区间即告警
- **测试质量预测** — qa-platform 测试通过率、执行时长趋势预测，跌破正常区间提前预警
- **容量规划** — seekdb / RAG 负载趋势预测

## 接入标准

按官方插件规范实现（详见 `~/Workspace/harness/projects/dsh-plugin-standards.md`）：

- 宿主包一律 `peerDependencies`（`@deepseek-ai/cordis` / `dsh-settings` / `schemastery`），不打包
- `package.json` 的 `dsh` 字段：`bundle.patch` → cordis.patch.yml、`client.inject` 声明 web 侧注入
- `cordis.patch.yml`：`- insert: { id: dsh-timesfm, name: dsh-timesfm }`
- host 侧入口 `export const name` + `apply(ctx)`；工具用 `defineTool`，配置用 schemastery schema
- 对外暴露预测服务（cordis service），供其他插件注册监控目标——参考 dsh-better-sidebar 的服务互通模式

## 模型

- `google/timesfm-2.5-200m-pytorch`（~800MB，下载走 `HF_ENDPOINT=https://hf-mirror.com`）
- Apple Silicon MPS 加速，RAM ~1.5-3GB
- 100% 本地推理，零外部 API

## 状态

- [x] 可行性研究（TimesFM 能力/资源/用法）
- [x] 官方插件标准逆向（dshmarket + dsh-better-sidebar 解包实证）
- [ ] Python 预测核心（timesfm 封装 + seekdb 读数）
- [ ] dsh 插件骨架（package.json + cordis.patch.yml + 入口）
- [ ] 市场发版（npm + awesome-dsh-plugin.com 收录）
