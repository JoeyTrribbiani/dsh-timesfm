"""dsh-timesfm Python 预测服务：FastAPI 壳，包装 core.forecast，供 dsh 插件与外部项目走 HTTP 接入。

启动: .venv/bin/python -m core.server  (默认 127.0.0.1:8920)
"""

from __future__ import annotations

import threading
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .forecast import ForecastError, TimesFMCore, get_core

app = FastAPI(title="dsh-timesfm", version="0.1.0")

_state_lock = threading.Lock()
_state = {"loaded": False, "error": None}


class ForecastRequest(BaseModel):
    series: List[float] = Field(..., description="一维时序观测值")
    horizon: int = Field(24, ge=1, le=256)
    quantiles: List[float] = Field(default_factory=lambda: [0.1, 0.5, 0.9])


class BatchForecastRequest(BaseModel):
    series: List[List[float]] = Field(..., description="多条同长度一维时序")
    horizon: int = Field(24, ge=1, le=256)
    quantiles: List[float] = Field(default_factory=lambda: [0.1, 0.5, 0.9])


class AnomalyRequest(BaseModel):
    series: List[float]
    actual: float


class WatchRequest(BaseModel):
    """注册监控目标（第一版：由插件 JS 侧拉数，本服务只做预测判定）。"""

    name: str
    series: List[float]
    horizon: int = 6
    band: List[float] = Field(default_factory=lambda: [0.1, 0.9])


@app.get("/health")
def health():
    out = {"status": "ok", "service": "dsh-timesfm", "version": "0.1.0"}
    out.update(_state)
    return out


@app.post("/api/v1/forecast")
def forecast(req: ForecastRequest):
    try:
        return get_core().forecast(req.series, horizon=req.horizon, quantiles=req.quantiles)
    except ForecastError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # 模型加载失败等
        with _state_lock:
            _state["error"] = str(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/forecast/batch")
def forecast_batch(req: BatchForecastRequest):
    """批量预测：一次模型调用预测多条同长度序列。返回 {results: [...], count: n}"""
    try:
        results = get_core().forecast_batch(req.series, horizon=req.horizon, quantiles=req.quantiles)
        return {"results": results, "count": len(results)}
    except ForecastError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        with _state_lock:
            _state["error"] = str(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/anomaly")
def anomaly(req: AnomalyRequest):
    try:
        return get_core().anomaly_score(req.series, actual=req.actual)
    except ForecastError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/v1/watch/check")
def watch_check(req: WatchRequest):
    """watch 闭环的判定端点：JS 侧按 interval 拉数后调这里，返回是否出带。"""
    try:
        out = get_core().forecast(req.series, horizon=max(req.horizon, 1), quantiles=req.band)
        lo_k = f"{min(req.band):g}"
        hi_k = f"{max(req.band):g}"
        lo = out["quantiles"][lo_k][0]
        hi = out["quantiles"][hi_k][0]
        latest = req.series[-1]
        return {
            "name": req.name,
            "forecast_next": out["point"][0],
            "band": [lo, hi],
            "latest": latest,
            "alert": latest < lo or latest > hi,
        }
    except ForecastError as e:
        raise HTTPException(status_code=422, detail=str(e))


def warmup():
    """预加载模型（后台线程调用，不阻塞启动）。"""
    try:
        get_core().forecast([0.0] * 32, horizon=1)
        with _state_lock:
            _state["loaded"] = True
    except Exception as e:
        with _state_lock:
            _state["error"] = str(e)


@app.on_event("startup")
def _startup():
    threading.Thread(target=warmup, daemon=True).start()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8920, log_level="info")


if __name__ == "__main__":
    main()
