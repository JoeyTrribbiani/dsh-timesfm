"""dsh-timesfm 预测核心：TimesFM 2.5 封装，供三层接入（cordis service / HTTP API / agent tool）共用。"""

from __future__ import annotations

import threading
from typing import Sequence

import numpy as np

DEFAULT_MODEL_ID = "google/timesfm-2.5-200m-pytorch"
DEFAULT_MAX_CONTEXT = 1024
DEFAULT_MAX_HORIZON = 256

# TimesFM 连续 quantile head 的固定分位档（低→高）
QUANTILE_LADDER = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class ForecastError(ValueError):
    """输入序列不合法。"""


class TimesFMCore:
    """单例懒加载的 TimesFM 推理核。MPS 优先，CPU 兜底。"""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        max_context: int = DEFAULT_MAX_CONTEXT,
        max_horizon: int = DEFAULT_MAX_HORIZON,
    ):
        self.model_id = model_id
        self.max_context = max_context
        self.max_horizon = max_horizon
        self._model = None
        self._lock = threading.Lock()

    # ── 内部 ──

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch  # 延迟导入，缩小非推理调用方的依赖面

            torch.set_float32_matmul_precision("high")
            import timesfm

            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(self.model_id)
            model.compile(
                timesfm.ForecastConfig(
                    max_context=self.max_context,
                    max_horizon=self.max_horizon,
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                    force_flip_invariance=True,
                    infer_is_positive=True,
                    fix_quantile_crossing=True,
                )
            )
            self._model = model

    @staticmethod
    def _validate(series: Sequence[float], horizon: int) -> np.ndarray:
        arr = np.asarray(series, dtype=np.float32)
        if arr.ndim != 1:
            raise ForecastError(f"series 必须是一维时序，收到 shape={arr.shape}")
        if arr.size < 10:
            raise ForecastError(f"序列太短（{arr.size} 点），至少需要 10 个观测值")
        if not np.isfinite(arr).all():
            raise ForecastError("序列含 NaN/Inf，请先清洗")
        if not 1 <= horizon <= DEFAULT_MAX_HORIZON:
            raise ForecastError(f"horizon 须在 1..{DEFAULT_MAX_HORIZON}")
        return arr

    # ── 对外 ──

    def forecast(
        self,
        series: Sequence[float],
        horizon: int = 24,
        quantiles: Sequence[float] = (0.1, 0.5, 0.9),
    ) -> dict:
        """零样本预测。

        返回:
            {
              "point": [...],                # 中位数预测，长度 horizon
              "quantiles": {"0.1": [...], …} # 请求的分位带
              "model": model_id,
              "n_obs": 输入长度,
            }
        """
        arr = self._validate(series, int(horizon))
        requested = sorted({float(q) for q in quantiles})
        for q in requested:
            if q not in QUANTILE_LADDER:
                raise ForecastError(
                    f"分位 {q} 不在支持档位 {QUANTILE_LADDER}"
                )

        self._ensure_loaded()

        # 超长序列截尾：只保留最近 max_context 个点
        if arr.size > self.max_context:
            arr = arr[-self.max_context:]

        point, quant = self._model.forecast(horizon=int(horizon), inputs=[arr])
        point = np.asarray(point)[0]          # (horizon,)
        quant = np.asarray(quant)[0]          # (horizon, n_quantiles)

        ladder = list(QUANTILE_LADDER)
        if quant.shape[-1] != len(ladder):
            # 模型分位档与预期不符时，退化只给中位点预测
            bands = {"0.5": point.tolist()}
        else:
            bands = {
                f"{q:g}": quant[:, ladder.index(q)].tolist() for q in requested
            }

        return {
            "point": point.tolist(),
            "quantiles": bands,
            "model": self.model_id,
            "n_obs": int(arr.size),
        }

    def anomaly_score(self, series: Sequence[float], actual: float) -> dict:
        """异常判分：actual 落在预测分布外的程度。

        返回 {"in_band": bool, "quantile_pos": 位置描述, "forecast": point[0]}
        以 horizon=1 预测下一步，看 actual 相对 10/90 分位带的位置。
        """
        out = self.forecast(series, horizon=1, quantiles=(0.1, 0.5, 0.9))
        lo = out["quantiles"]["0.1"][0]
        hi = out["quantiles"]["0.9"][0]
        mid = out["point"][0]
        if actual < lo:
            pos = "below_0.1"
        elif actual > hi:
            pos = "above_0.9"
        else:
            pos = "in_band"
        return {"in_band": pos == "in_band", "quantile_pos": pos,
                "forecast": mid, "band": [lo, hi], "actual": actual}


_core: TimesFMCore | None = None
_core_lock = threading.Lock()


def get_core() -> TimesFMCore:
    """进程级单例。"""
    global _core
    if _core is None:
        with _core_lock:
            if _core is None:
                _core = TimesFMCore()
    return _core
