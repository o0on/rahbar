"""Unit tests for Assignment 03 Part B, C, E evaluation, gate, and coverage."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))

from component_retrieval import inject_noise, ndcg_at_k, recall_at_k, reciprocal_rank, score_retrieval  # noqa: E402
from e2e_answers import score_e2e  # noqa: E402
from gate import evaluate_gate, load_thresholds  # noqa: E402


def test_component_retrieval_metrics() -> None:
    rel = [{"source": "doc.pdf", "section": "5. Academic Assessment"}]
    retrieved = [
        {"chunk_id": "c1", "source": "other.pdf", "section": "1. Intro", "score": 0.70},
        {"chunk_id": "c2", "source": "doc.pdf", "section": "5. Academic Assessment", "score": 0.85},
    ]
    assert recall_at_k(rel, retrieved, 3) == 1.0
    assert recall_at_k(rel, retrieved, 5) == 1.0
    assert reciprocal_rank(rel, retrieved, 5) == 0.5
    assert round(ndcg_at_k(rel, retrieved, 5) or 0.0, 4) == 0.6309

    summary = score_retrieval([{"id": "q1", "relevant": rel, "retrieved": retrieved}])
    assert summary["recall@5"] == 1.0
    assert summary["mrr"] == 0.5


def test_noise_injector_deterministic() -> None:
    q = "What CGPA must a student maintain?"
    a = inject_noise(q, random.Random(42))
    b = inject_noise(q, random.Random(42))
    assert a == b
    assert len(a.split()) == len(q.split())


def test_e2e_scoring_and_judge_agreement() -> None:
    rows = [
        {
            "id": "g01",
            "question": "What attendance is required?",
            "must_abstain": False,
            "expect": "80%",
            "latency_ms": 120.0,
            "response": {
                "answer": "Students must maintain 80% attendance.",
                "answered_from_context": True,
                "citations": [{"source": "rules.pdf", "section": "1. Medium"}],
                "retrieved": [{"source": "rules.pdf", "section": "1. Medium", "score": 0.9}],
            },
        },
        {
            "id": "g20",
            "question": "Hostel pet rules?",
            "must_abstain": True,
            "expect": "",
            "latency_ms": 40.0,
            "response": {
                "answer": "The corpus does not cover this.",
                "answered_from_context": False,
                "citations": [],
                "retrieved": [],
            },
        },
    ]
    res = score_e2e(rows, use_llm_judge=False)
    assert res["faithfulness"] == 1.0
    assert res["abstention"] == 1.0
    assert res["citation_validity"] == 1.0
    assert res["judge_human_agreement_rate"] == 1.0


def test_release_gate_pass_and_fail() -> None:
    thresholds = load_thresholds(ROOT / "eval" / "gate-thresholds.json")
    baseline = {"metrics": {"recall@5": 0.95, "faithfulness": 0.90, "abstention": 1.0, "citation_validity": 1.0, "p95_ms": 4000.0}}
    good = {"metrics": {"recall@5": 0.95, "faithfulness": 0.92, "abstention": 1.0, "citation_validity": 1.0, "p95_ms": 3800.0}}
    bad = {"metrics": {"recall@5": 0.75, "faithfulness": 0.60, "abstention": 0.5, "citation_validity": 1.0, "p95_ms": 3800.0}}

    ok_pass, _ = evaluate_gate(baseline, good, thresholds)
    ok_fail, checks = evaluate_gate(baseline, bad, thresholds)
    assert ok_pass is True
    assert ok_fail is False
    assert any(not c["pass"] and c["metric"] == "recall@5" for c in checks)


def test_golden_jsonl_valid_format() -> None:
    rows = [json.loads(line) for line in (ROOT / "eval" / "golden.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 25
    assert sum(1 for r in rows if r.get("provenance") == "hand_written") >= 8
    assert sum(1 for r in rows if r.get("provenance") == "synthetic_hand_reviewed") >= 8
