"""真实场景数据采集：定时探测 qa-platform / RAG / seekdb / timesfm 自身，记录响应耗时。

输出: data/metrics.jsonl（每行一个观测：{"ts": ..., "name": ..., "ms": ..., "ok": ...}）
用法: .venv/bin/python scripts/collector.py [--interval 5] [--rounds 60]
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

import urllib.request

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

TARGETS = {
    "qa-platform": ("http", "http://127.0.0.1:3080/platform-api/health"),
    "rag": ("http", "http://127.0.0.1:8900/api/health"),
    "timesfm": ("http", "http://127.0.0.1:8920/health"),
    "seekdb": ("tcp", ("127.0.0.1", 2881)),
}


def probe_http(url: str) -> tuple[float, bool]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            resp.read()
        return (time.perf_counter() - t0) * 1000, True
    except Exception:
        return (time.perf_counter() - t0) * 1000, False


def probe_tcp(host: str, port: int) -> tuple[float, bool]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=5):
            return (time.perf_counter() - t0) * 1000, True
    except Exception:
        return (time.perf_counter() - t0) * 1000, False


def probe_once(name: str) -> dict:
    kind, target = TARGETS[name]
    if kind == "http":
        ms, ok = probe_http(target)
    else:
        ms, ok = probe_tcp(*target)
    return {"ts": round(time.time(), 3), "name": name, "ms": round(ms, 2), "ok": ok}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--rounds", type=int, default=60)
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    out = DATA_DIR / "metrics.jsonl"
    print(f"采集开始 → {out}（{args.rounds} 轮 × {args.interval}s）")
    with out.open("a", encoding="utf-8") as f:
        for r in range(args.rounds):
            row = {}
            for name in TARGETS:
                obs = probe_once(name)
                row[name] = obs
                f.write(json.dumps(obs, ensure_ascii=False) + "\n")
            f.flush()
            def fmt(n: str, o: dict) -> str:
                return f"{n}=DOWN" if not o["ok"] else f"{n}={o['ms']:.0f}ms"
            summary = " ".join(fmt(n, row[n]) for n in TARGETS)
            print(f"[{r + 1}/{args.rounds}] {summary}", flush=True)
            if r < args.rounds - 1:
                time.sleep(args.interval)
    print("采集完成")


if __name__ == "__main__":
    main()
