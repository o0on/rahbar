#!/usr/bin/env python3
"""Part B: One-command three-level evaluation suite for RAHBAR (Module + Component + E2E).

Outputs a timestamped JSON matching the shared contract in docs/A3-WORK-SPLIT.md:
    eval/results/<UTC-timestamp>.json

Usage:
    python eval/suite.py --api http://127.0.0.1:8080 --collection rahbar --seed 42
    python eval/suite.py --api http://127.0.0.1:8080 --compare-seed 43
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from component_retrieval import inject_noise, load_jsonl, score_retrieval  # noqa: E402
from e2e_answers import score_e2e  # noqa: E402
from module_embedding import run_module_eval  # noqa: E402


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=HERE.parent, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return os.environ.get("GIT_SHA", "unknown")


def run_once(
    *,
    api: str,
    golden_path: Path,
    collection: str,
    embed_model: str,
    ollama_url: str,
    seed: int,
    use_judge: bool,
    run_noise: bool,
    host_header: str | None = None,
) -> dict[str, Any]:
    rng = random.Random(seed)
    questions = load_jsonl(golden_path)

    # Order question evaluation deterministically using the seed
    indices = list(range(len(questions)))
    rng.shuffle(indices)
    ordered = [questions[i] for i in indices]

    # Level 1: Module embedding benchmark
    mod = run_module_eval(ollama_url=ollama_url, embed_model=embed_model)

    headers = {"Host": host_header} if host_header else {}
    e2e_rows: list[dict] = []
    retrieval_records: list[dict] = []

    with httpx.Client(timeout=180.0, headers=headers) as client:
        for q in ordered:
            t0 = time.perf_counter()
            resp = client.post(f"{api.rstrip('/')}/api/ask", json={"question": q["question"]})
            resp.raise_for_status()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            body = resp.json()
            e2e_rows.append({**q, "response": body, "latency_ms": elapsed_ms})
            retrieval_records.append({**q, "retrieved": body.get("retrieved") or []})

        # Noise stress pass on retrieval (perturbed answerable queries)
        noise_records: list[dict] = []
        if run_noise:
            noise_rng = random.Random(seed + 100)
            for q in ordered:
                if not q.get("relevant"):
                    continue
                noisy_q = inject_noise(q["question"], noise_rng)
                resp = client.post(f"{api.rstrip('/')}/api/ask", json={"question": noisy_q})
                resp.raise_for_status()
                noise_records.append({**q, "retrieved": resp.json().get("retrieved") or []})

    # Level 2: Component retrieval metrics
    comp = score_retrieval(retrieval_records)
    noise_comp = score_retrieval(noise_records) if noise_records else None

    # Level 3: E2E answer metrics
    e2e = score_e2e(
        e2e_rows,
        use_llm_judge=use_judge,
        ollama_url=ollama_url,
        judge_model=os.environ.get("LLM_MODEL", "llama3.2:3b"),
    )

    metrics = {
        "recall@3": comp["recall@3"],
        "recall@5": comp["recall@5"],
        "mrr": comp["mrr"],
        "ndcg@5": comp["ndcg@5"],
        "faithfulness": e2e["faithfulness"],
        "abstention": e2e["abstention"],
        "citation_validity": e2e["citation_validity"],
        "p50_ms": e2e["p50_ms"],
        "p95_ms": e2e["p95_ms"],
        "embed_cosine_gap": mod["embed_cosine_gap"],
        "embed_ms_per_chunk": mod["embed_ms_per_chunk"],
        "embed_determinism": mod["embed_determinism"],
    }

    answerable = sum(1 for q in questions if not q.get("must_abstain"))
    unanswerable = sum(1 for q in questions if q.get("must_abstain"))
    hard = sum(1 for q in questions if q.get("difficulty") == "hard")
    hand = sum(1 for q in questions if q.get("provenance") == "hand_written")
    synth = sum(1 for q in questions if q.get("provenance") == "synthetic_hand_reviewed")

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": {
            "seed": seed,
            "collection": collection,
            "embedding_model": embed_model,
            "git_sha": _git_sha(),
        },
        "golden_summary": {
            "total": len(questions),
            "answerable": answerable,
            "unanswerable": unanswerable,
            "hard": hard,
            "hand_written": hand,
            "synthetic_hand_reviewed": synth,
        },
        "metrics": metrics,
        "module": mod,
        "component": comp,
        "noise": noise_comp,
        "e2e": e2e,
    }


def _print_summary(res: dict[str, Any], delta: dict[str, float] | None = None) -> None:
    m = res["metrics"]
    mod = res["module"]
    comp = res["component"]
    noise = res.get("noise") or {}
    e2e = res["e2e"]
    gs = res["golden_summary"]

    print("=" * 84)
    print(f"RAHBAR THREE-LEVEL EVALUATION SUMMARY  (seed={res['run']['seed']}  collection={res['run']['collection']})")
    print(
        f"Golden Set: {gs['total']} total — {gs['answerable']} answerable, "
        f"{gs['unanswerable']} unanswerable, {gs['hard']} hard | "
        f"{gs['hand_written']} hand-written, {gs['synthetic_hand_reviewed']} synthetic"
    )
    print("-" * 84)
    print(f"{'LEVEL':<11} {'METRIC':<36} {'VALUE':>10} {'NOISE / ALT':>14} {'DELTA':>9}")
    print("-" * 84)
    rows = [
        ("Module", "paraphrase mean cosine", f"{mod['paraphrase_mean_cosine']:.4f}", f"{mod['noise_mean_cosine']:.4f} (typo)", "-"),
        ("Module", "unrelated mean cosine / gap", f"{mod['unrelated_mean_cosine']:.4f} / {m['embed_cosine_gap']:.4f}", "-", "-"),
        ("Module", "embed ms/chunk / determinism", f"{m['embed_ms_per_chunk']:.2f}ms / {m['embed_determinism']:.4f}", "-", "-"),
        ("Component", "recall@3 / recall@5", f"{m['recall@3']:.4f} / {m['recall@5']:.4f}",
         f"{noise.get('recall@5', 0):.4f}" if noise else "-",
         f"{(noise.get('recall@5', 0) - m['recall@5']):+.4f}" if noise else "-"),
        ("Component", "MRR / nDCG@5", f"{comp['mrr']:.4f} / {comp['ndcg@5']:.4f}",
         f"{noise.get('mrr', 0):.4f}" if noise else "-",
         f"{(noise.get('mrr', 0) - comp['mrr']):+.4f}" if noise else "-"),
        ("E2E", "faithfulness / abstention", f"{m['faithfulness']:.4f} / {m['abstention']:.4f}", "-", "-"),
        ("E2E", "citation validity", f"{m['citation_validity']:.4f}", "-", "-"),
        ("E2E", "p50 / p95 latency (ms)", f"{m['p50_ms']:.1f} / {m['p95_ms']:.1f}", "-", "-"),
        ("E2E", f"LLM judge vs human ({e2e['judge_human_labels']} labels)",
         f"{e2e['judge_human_agreement_rate']:.4f}" if e2e["judge_human_agreement_rate"] is not None else "n/a", "-", "-"),
    ]
    if delta:
        max_d = max(abs(v) for v in delta.values()) if delta else 0.0
        rows.append(("All", "two fixed-seed runs max diff", f"{max_d:.4f}", "seed2", f"{max_d:+.4f}"))
    for lvl, metric, val, alt, d in rows:
        print(f"{lvl:<11} {metric:<36} {val:>10} {alt:>14} {d:>9}")
    print("=" * 84)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 3-level evaluation suite for RAHBAR.")
    parser.add_argument("--api", default=os.environ.get("API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--host", help="Optional Host header for Ingress")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--collection", default=os.environ.get("CHROMA_COLLECTION", "rahbar"))
    parser.add_argument("--embed-model", default=os.environ.get("EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--compare-seed", type=int, default=None, help="Run a second pass with this seed to measure determinism delta")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM-as-judge calls for fast deterministic checks")
    parser.add_argument("--no-noise", action="store_true", help="Skip typo noise pass")
    parser.add_argument("--out", type=Path, default=None, help="Explicit output JSON path")
    args = parser.parse_args()

    res1 = run_once(
        api=args.api,
        golden_path=args.golden,
        collection=args.collection,
        embed_model=args.embed_model,
        ollama_url=args.ollama_url,
        seed=args.seed,
        use_judge=not args.no_judge,
        run_noise=not args.no_noise,
        host_header=args.host,
    )

    delta: dict[str, float] | None = None
    if args.compare_seed is not None:
        res2 = run_once(
            api=args.api,
            golden_path=args.golden,
            collection=args.collection,
            embed_model=args.embed_model,
            ollama_url=args.ollama_url,
            seed=args.compare_seed,
            use_judge=False,
            run_noise=False,
            host_header=args.host,
        )
        delta = {}
        for k in ("recall@3", "recall@5", "mrr", "ndcg@5", "faithfulness", "abstention", "citation_validity"):
            delta[k] = round(abs(float(res1["metrics"][k]) - float(res2["metrics"][k])), 4)
        res1["seed_comparison"] = {"seed_2": args.compare_seed, "metrics_2": res2["metrics"], "deltas": delta}

    out = args.out or (HERE / "results" / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res1, indent=2) + "\n", encoding="utf-8")

    _print_summary(res1, delta)
    print(f"Result written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
