#!/usr/bin/env python3
"""Part E: Live query coverage & Batch B drift measurement against the golden set.

Computes how many in-domain live queries fall within cosine similarity threshold of
at least one question in eval/golden.jsonl, lists uncovered topics, and exits 1 if
coverage < COVERAGE_MIN (0.85 default).

Usage:
    python eval/coverage.py --live eval/live_queries.jsonl --golden eval/golden.jsonl \
        --out eval/results/coverage/before.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from component_retrieval import load_jsonl  # noqa: E402
from module_embedding import cosine_similarity, embed_batch  # noqa: E402


def compute_coverage(
    *,
    live_path: Path,
    golden_path: Path,
    ollama_url: str,
    embed_model: str,
    sim_threshold: float,
    coverage_min: float,
) -> dict[str, Any]:
    live_rows = load_jsonl(live_path)
    golden_rows = load_jsonl(golden_path)

    in_domain = [r for r in live_rows if r.get("domain") == "in"]
    out_domain = [r for r in live_rows if r.get("domain") != "in"]

    golden_texts = [g["question"] for g in golden_rows]
    all_live_texts = [r["question"] for r in live_rows]

    golden_vecs = embed_batch(golden_texts, ollama_url, embed_model)
    live_vecs = embed_batch(all_live_texts, ollama_url, embed_model)

    covered = 0
    uncovered_topics: list[dict[str, Any]] = []
    per_query: list[dict[str, Any]] = []

    for row, lvec in zip(live_rows, live_vecs):
        sims = [(cosine_similarity(lvec, gvec), golden_rows[i]["id"], golden_rows[i]["question"])
                for i, gvec in enumerate(golden_vecs)]
        best_sim, best_gid, best_gq = max(sims, key=lambda x: x[0])
        is_covered = best_sim >= sim_threshold
        if row.get("domain") == "in":
            if is_covered:
                covered += 1
            else:
                uncovered_topics.append({
                    "id": row["id"],
                    "topic": row.get("topic", "unknown"),
                    "question": row["question"],
                    "nearest_golden_id": best_gid,
                    "max_similarity": round(best_sim, 4),
                })
        per_query.append({
            "id": row["id"],
            "domain": row.get("domain"),
            "topic": row.get("topic"),
            "nearest_golden_id": best_gid,
            "max_similarity": round(best_sim, 4),
            "covered": is_covered,
        })

    coverage_share = round(covered / len(in_domain), 4) if in_domain else 0.0
    all_share = round(sum(1 for p in per_query if p["covered"]) / len(live_rows), 4) if live_rows else 0.0
    passed = coverage_share >= coverage_min

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "golden_count": len(golden_rows),
        "live_total": len(live_rows),
        "in_domain_count": len(in_domain),
        "out_domain_count": len(out_domain),
        "sim_threshold": sim_threshold,
        "coverage_min": coverage_min,
        "in_domain_covered": covered,
        "in_domain_coverage": coverage_share,
        "overall_coverage": all_share,
        "passed": passed,
        "uncovered_topics": uncovered_topics,
        "per_query": per_query,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure golden-set coverage over live queries.")
    parser.add_argument("--live", type=Path, default=HERE / "live_queries.jsonl")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--embed-model", default=os.environ.get("EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--sim-threshold", type=float, default=float(os.environ.get("COVERAGE_SIM_THRESHOLD", "0.72")))
    parser.add_argument("--coverage-min", type=float, default=float(os.environ.get("COVERAGE_MIN", "0.85")))
    parser.add_argument("--out", type=Path, default=HERE / "results" / "coverage" / "latest.json")
    args = parser.parse_args()

    res = compute_coverage(
        live_path=args.live,
        golden_path=args.golden,
        ollama_url=args.ollama_url,
        embed_model=args.embed_model,
        sim_threshold=args.sim_threshold,
        coverage_min=args.coverage_min,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")

    print("=" * 84)
    print(f"RAHBAR LIVE QUERY COVERAGE  (golden={res['golden_count']}  live={res['live_total']} [in={res['in_domain_count']}, out={res['out_domain_count']}])")
    print("-" * 84)
    print(f"Similarity threshold : {res['sim_threshold']:.2f}")
    print(f"In-domain covered    : {res['in_domain_covered']} / {res['in_domain_count']} ({res['in_domain_coverage']:.2%})")
    print(f"Required COVERAGE_MIN: {res['coverage_min']:.2%}")
    print(f"Decision             : {'PASS' if res['passed'] else 'FAIL (below COVERAGE_MIN)'}")
    if res["uncovered_topics"]:
        print("-" * 84)
        print(f"Uncovered in-domain topics ({len(res['uncovered_topics'])}):")
        for u in res["uncovered_topics"]:
            print(f"  - [{u['id']}] {u['topic']:<20} (max_sim={u['max_similarity']:.4f})  {u['question'][:55]}")
    print("=" * 84)
    print(f"Result written to: {args.out}")
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
