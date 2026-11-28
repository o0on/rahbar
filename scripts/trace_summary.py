"""D3: summarise the trace log over a time window.

    python scripts/trace_summary.py --hours 24 --path traces/trace.jsonl

Writes eval/results/trace-summary.json as well as printing the table.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.tracing import summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise RAGOps trace metrics")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--path", default=os.environ.get("TRACE_PATH", ""),
                        help="trace JSONL file (default: $TRACE_PATH)")
    parser.add_argument("--out", default="eval/results/trace-summary.json")
    args = parser.parse_args()
    if not args.path:
        raise SystemExit("set TRACE_PATH or pass --path traces/trace.jsonl")

    cutoff = time.time() - args.hours * 3600
    entries = []
    with Path(args.path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            entry = json.loads(line)
            try:
                stamp = datetime.strptime(entry["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            except (KeyError, ValueError):
                continue
            if stamp.replace(tzinfo=timezone.utc).timestamp() >= cutoff:
                entries.append(entry)

    result = summarize(entries)
    result["window_hours"] = args.hours
    result["source"] = args.path
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"TRACE SUMMARY  window={args.hours}h  traces={result['traces']}")
    print(f"top-1 similarity   min={result['top1_similarity']['min']} "
          f"mean={result['top1_similarity']['mean']} p50={result['top1_similarity']['p50']} "
          f"p95={result['top1_similarity']['p95']}")
    print(f"abstention rate    {result['abstention_rate']}")
    print(f"cache hit rate     {result['cache_hit_rate']}")
    print("p95 latency per stage (ms):")
    for stage, value in result["p95_latency_ms_per_stage"].items():
        print(f"  {stage:<20} {value}")
    print(f"injection attempts flagged  {result['injection_attempts_flagged']}")
    print(f"result: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
