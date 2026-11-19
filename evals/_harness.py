"""Shared bits for the gates: load golden data, run work in parallel, write a
report row. Kept deliberately small — a test harness that needs its own tests
is not a harness."""
from __future__ import annotations
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
GOLDEN = ROOT / "evals" / "golden"
REPORTS = ROOT / "evals" / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (GOLDEN / name).open() if l.strip()]


def parallel(fn, items, workers: int = 6, label: str = ""):
    """On-demand Bedrock on this account is capped at 10-20 requests a
    minute, so a 1,300-ticket sweep takes over an hour. Progress is printed
    because a silent hour is indistinguishable from a hung process."""
    import sys as _sys, time as _time
    done = 0
    total = len(items)
    t0 = _time.time()
    out = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(fn, items):
            out.append(r)
            done += 1
            if done % 25 == 0 or done == total:
                rate = done / max(1e-9, _time.time() - t0) * 60
                eta = (total - done) / max(1e-9, rate) 
                print(f"    {label}{done}/{total}  {rate:.0f}/min  "
                      f"eta {eta:.0f} min", flush=True)
                _sys.stdout.flush()
    return out


def record(gate: str, target: str, result: str, passed: bool, **extra) -> None:
    path = REPORTS / "gates.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[gate] = {"target": target, "result": result, "passed": passed,
                  "at": time.strftime("%Y-%m-%d %H:%M:%S"), **extra}
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    flag = "PASS" if passed else "FAIL"
    print(f"\n[{flag}] {gate}: target {target} -> measured {result}")
