"""dsh-timesfm watch 常驻守护：定时采集真实服务指标 + 预测判定 + 告警落盘。

循环（默认 5 分钟）：
  1. probe 4 个服务（qa-platform / rag / timesfm / seekdb）→ 追加 data/metrics.jsonl
  2. 每服务取最近 40 个 ok 样本 → POST :8920 /api/v1/watch/check
  3. alert=true → 追加 data/alerts.jsonl + stderr 告警行（LaunchAgent 会收进 err log）

用法: .venv/bin/python scripts/watch_daemon.py [--interval 300]
挂载: LaunchAgent com.qap.timesfm-watcher
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.collector import TARGETS, probe_once  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RAG_BASE = "http://127.0.0.1:8920"
MIN_POINTS = 12
WINDOW = 40


def load_recent(name: str, n: int = WINDOW) -> list[float]:
    """从 metrics.jsonl 取该服务最近 n 个 ok 样本。"""
    vals: list[float] = []
    f = DATA_DIR / "metrics.jsonl"
    if not f.exists():
        return vals
    for line in f.read_text(encoding="utf-8").splitlines()[-4000:]:
        try:
            obs = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obs.get("name") == name and obs.get("ok"):
            vals.append(float(obs["ms"]))
    return vals[-n:]


def watch_check(name: str, series: list[float]) -> dict:
    req = urllib.request.Request(
        f"{RAG_BASE}/api/v1/watch/check",
        data=json.dumps({"name": name, "series": series, "horizon": 6, "band": [0.1, 0.9]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=300.0)
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    metrics_f = (DATA_DIR / "metrics.jsonl").open("a", encoding="utf-8")
    alerts_f = (DATA_DIR / "alerts.jsonl").open("a", encoding="utf-8")
    print(f"[watcher] 启动，间隔 {args.interval}s，判定服务 {RAG_BASE}", flush=True)

    while True:
        row = {}
        for name in TARGETS:
            obs = probe_once(name)
            row[name] = obs
            metrics_f.write(json.dumps(obs, ensure_ascii=False) + "\n")
        metrics_f.flush()

        alerts = []
        for name in TARGETS:
            series = load_recent(name)
            if len(series) < MIN_POINTS or not row[name]["ok"]:
                continue
            try:
                out = watch_check(name, series)
            except Exception as e:
                print(f"[watcher] {name} 判定失败: {e}", flush=True)
                continue
            if out.get("alert"):
                alerts.append(out)

        for a in alerts:
            alerts_f.write(json.dumps({"ts": time.time(), **a}, ensure_ascii=False) + "\n")
        alerts_f.flush()
        if alerts:
            for a in alerts:
                print(
                    f"[ALERT] {a['name']}: latest={a['latest']:.1f}ms 出带 "
                    f"band=[{a['band'][0]:.1f}, {a['band'][1]:.1f}] forecast_next={a['forecast_next']:.1f}",
                    flush=True,
                )
        def fmt(n: str, o: dict) -> str:
            return f"{n}=DOWN" if not o["ok"] else f"{n}={o['ms']:.0f}ms"
        ok_summary = " ".join(fmt(n, row[n]) for n in TARGETS)
        print(f"[watcher] {time.strftime('%H:%M:%S')} {ok_summary} alerts={len(alerts)}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
