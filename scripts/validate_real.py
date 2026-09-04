"""真实场景验证：用采集到的真实响应耗时序列做预测与异常判定。

前置: scripts/collector.py 已攒到足够观测（每服务 ≥ 12 个 ok 样本）
用法: .venv/bin/python scripts/validate_real.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import urllib.request

DATA = Path(__file__).resolve().parents[1] / "data" / "metrics.jsonl"
BASE = "http://127.0.0.1:8920"


def load_series(name: str, min_points: int = 12) -> list[float]:
    vals: list[float] = []
    if DATA.exists():
        for line in DATA.read_text(encoding="utf-8").splitlines():
            try:
                obs = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obs.get("name") == name and obs.get("ok"):
                vals.append(float(obs["ms"]))
    if len(vals) < min_points:
        raise SystemExit(f"[skip] {name} 样本不足: {len(vals)} < {min_points}")
    return vals


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def validate(name: str) -> bool:
    series = load_series(name)
    latest = series[-1]
    sigma = statistics.pstdev(series) or 1.0
    print(f"\n=== {name}（{len(series)} 个真实观测，latest={latest:.1f}ms，σ={sigma:.1f}）===")

    # 1. 真实序列预测
    out = post("/api/v1/forecast", {"series": series, "horizon": 12, "quantiles": [0.1, 0.5, 0.9]})
    point = out["point"]
    print(f"[forecast] 未来 3 步中位: {[round(x, 1) for x in point[:3]]}ms")

    # 2. 正常值 → 应不出带
    check = post("/api/v1/watch/check", {"name": name, "series": series, "horizon": 6, "band": [0.1, 0.9]})
    normal_ok = not check["alert"]
    print(f"[watch] 真实 latest={check['latest']}ms → alert={check['alert']} band=[{check['band'][0]:.1f}, {check['band'][1]:.1f}] {'PASS' if normal_ok else 'FAIL'}")

    # 3. 注入异常（latest ×5 + 10σ）→ 应出带
    spike = latest * 5 + 10 * sigma
    check_bad = post("/api/v1/watch/check", {"name": f"{name}-spike", "series": series, "horizon": 6, "band": [0.1, 0.9]})
    # watch/check 用 series[-1] 判定，改用 forecast+band 手工判定异常值
    lo, hi = check_bad["band"]
    spike_alert = spike < lo or spike > hi
    print(f"[watch] 注入 spike={spike:.1f}ms vs band=[{lo:.1f}, {hi:.1f}] → {'alert' if spike_alert else 'NO-ALERT'} {'PASS' if spike_alert else 'FAIL'}")

    return normal_ok and spike_alert


def main() -> int:
    results = {}
    for name in ("qa-platform", "rag", "seekdb", "timesfm"):
        try:
            results[name] = validate(name)
        except SystemExit as e:
            print(e)
            results[name] = None
    passed = [k for k, v in results.items() if v]
    failed = [k for k, v in results.items() if v is False]
    skipped = [k for k, v in results.items() if v is None]
    print(f"\n=== 汇总: PASS={passed} FAIL={failed} SKIP={skipped} ===")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
