"""smoke test：合成序列零样本预测 + 异常判分。模型需已下载（HF_ENDPOINT=https://hf-mirror.com）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from core.forecast import TimesFMCore


def main() -> int:
    rng = np.random.default_rng(7)
    t = np.linspace(0, 20, 200)
    series = (np.sin(t) * 3 + t * 0.2 + rng.normal(0, 0.15, t.size)).astype(np.float32)

    core = TimesFMCore()
    out = core.forecast(series.tolist(), horizon=24, quantiles=(0.1, 0.5, 0.9))

    point = np.asarray(out["point"])
    lo = np.asarray(out["quantiles"]["0.1"])
    hi = np.asarray(out["quantiles"]["0.9"])

    assert point.shape == (24,), f"point shape {point.shape}"
    assert (lo <= hi).all(), "分位带交叉"
    assert np.isfinite(point).all(), "预测含非有限值"

    print(f"[ok] forecast: point shape {point.shape}")
    print(f"[ok] 未来 3 步中位数预测: {np.round(point[:3], 3).tolist()}")
    print(f"[ok] 首步 90% 区间: [{lo[0]:.3f}, {hi[0]:.3f}]")

    a = core.anomaly_score(series.tolist(), actual=float(point[0] + 10 * (hi[0] - lo[0] + 1)))
    assert a["quantile_pos"] == "above_0.9", f"异常判分意外: {a}"
    print(f"[ok] anomaly_score: {a['quantile_pos']} band={np.round(a['band'], 3).tolist()}")

    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
