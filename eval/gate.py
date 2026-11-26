#!/usr/bin/env python3
"""Part C: Release gate comparing candidate metrics against baseline and ConfigMap thresholds.

Exit code 0 = GATE PASS, exit code 1 = GATE FAIL.
Env overrides (from k8s/base/gate-configmap.yaml):
    RECALL5_MAX_DROP, FAITHFULNESS_MIN, P95_MS_MAX, ABSTENTION_MIN_BASELINE
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def load_thresholds(path: Path | None) -> dict[str, float]:
    defaults = {
        "RECALL5_MAX_DROP": 0.02,
        "FAITHFULNESS_MIN": 0.80,
        "P95_MS_MAX": 15000.0,
        "ABSTENTION_MIN_BASELINE": 1.0,
        "COVERAGE_MIN": 0.85,
    }
    if path and path.exists():
        defaults.update({k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()})
    for key in list(defaults.keys()):
        if key in os.environ and os.environ[key] != "":
            defaults[key] = float(os.environ[key])
    return defaults


def evaluate_gate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    thresholds: dict[str, float],
) -> tuple[bool, list[dict[str, Any]]]:
    bm = baseline.get("metrics") or baseline
    cm = candidate.get("metrics") or candidate

    b_r5 = float(bm.get("recall@5", 0.0))
    c_r5 = float(cm.get("recall@5", 0.0))
    r5_min = round(b_r5 - thresholds["RECALL5_MAX_DROP"], 4)

    c_faith = float(cm.get("faithfulness", 0.0))
    faith_min = thresholds["FAITHFULNESS_MIN"]

    b_abs = float(bm.get("abstention", 1.0))
    c_abs = float(cm.get("abstention", 0.0))
    abs_min = min(b_abs, thresholds["ABSTENTION_MIN_BASELINE"])
    # Never allow dropping below configured ABSTENTION_MIN_BASELINE
    abs_req = max(abs_min, thresholds["ABSTENTION_MIN_BASELINE"])

    c_cit = float(cm.get("citation_validity", 0.0))
    cit_min = 0.95

    c_p95 = float(cm.get("p95_ms", 0.0))
    p95_max = thresholds["P95_MS_MAX"]

    checks = [
        {
            "metric": "recall@5",
            "baseline": round(b_r5, 4),
            "candidate": round(c_r5, 4),
            "rule": f">= {r5_min:.4f} (drop <= {thresholds['RECALL5_MAX_DROP']:.2f})",
            "pass": c_r5 >= r5_min,
        },
        {
            "metric": "faithfulness",
            "baseline": round(float(bm.get("faithfulness", 0.0)), 4),
            "candidate": round(c_faith, 4),
            "rule": f">= {faith_min:.2f}",
            "pass": c_faith >= faith_min,
        },
        {
            "metric": "abstention",
            "baseline": round(b_abs, 4),
            "candidate": round(c_abs, 4),
            "rule": f">= {abs_req:.2f}",
            "pass": c_abs >= abs_req,
        },
        {
            "metric": "citation_validity",
            "baseline": round(float(bm.get("citation_validity", 1.0)), 4),
            "candidate": round(c_cit, 4),
            "rule": f">= {cit_min:.2f}",
            "pass": c_cit >= cit_min,
        },
        {
            "metric": "p95_ms",
            "baseline": round(float(bm.get("p95_ms", 0.0)), 2),
            "candidate": round(c_p95, 2),
            "rule": f"<= {p95_max:.1f} ms",
            "pass": c_p95 <= p95_max,
        },
    ]
    passed = all(c["pass"] for c in checks)
    return passed, checks


def print_gate(passed: bool, checks: list[dict[str, Any]], label: str = "candidate") -> None:
    print("=" * 84)
    print(f"RAHBAR RELEASE GATE  ({label})")
    print("-" * 84)
    print(f"{'METRIC':<20} {'BASELINE':>10} {'CANDIDATE':>11} {'THRESHOLD':>26} {'STATUS':>10}")
    print("-" * 84)
    for c in checks:
        st = "PASS" if c["pass"] else "FAIL"
        print(f"{c['metric']:<20} {str(c['baseline']):>10} {str(c['candidate']):>11} {c['rule']:>26} {st:>10}")
    print("-" * 84)
    print(f"DECISION: {'GATE PASS — eligible for release' if passed else 'GATE FAIL — release BLOCKED'}")
    print("=" * 84)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate release gate against baseline.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, default=HERE / "gate-thresholds.json")
    parser.add_argument("--label", default="candidate")
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    thresholds = load_thresholds(args.thresholds)
    passed, checks = evaluate_gate(baseline, candidate, thresholds)
    print_gate(passed, checks, label=args.label)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
