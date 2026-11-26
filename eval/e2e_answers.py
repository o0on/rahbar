"""Part B (Level 3 — E2E evaluation): faithfulness, abstention, citation validity, p50/p95 & judge agreement."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from component_retrieval import section_matches

# Human ground-truth expectations reviewed across >=10 golden questions to validate LLM-judge agreement.
# Each entry defines whether the golden question MUST be grounded/answered (True) or abstained (False),
# and what key factual token a faithful answer must preserve.
HUMAN_GOLD_LABELS: dict[str, dict[str, Any]] = {
    "g01": {"should_answer": True, "fact": "80"},
    "g02": {"should_answer": True, "fact": "2.0"},
    "g04": {"should_answer": True, "fact": "marriage"},
    "g07": {"should_answer": True, "fact": "makeup"},
    "g11": {"should_answer": True, "fact": "fine"},
    "g13": {"should_answer": True, "fact": "12,000"},
    "g14": {"should_answer": True, "fact": "30,000"},
    "g16": {"should_answer": True, "fact": "3.67"},
    "g20": {"should_answer": False, "fact": ""},
    "g21": {"should_answer": False, "fact": ""},
    "g22": {"should_answer": False, "fact": ""},
    "g25": {"should_answer": False, "fact": ""},
}


def citation_validity_ratio(response: dict) -> float | None:
    citations = response.get("citations") or []
    if not citations:
        return None
    retrieved = response.get("retrieved") or []
    valid = 0
    for cit in citations:
        for pas in retrieved:
            if cit.get("source") == pas.get("source") and section_matches(cit.get("section"), pas.get("section")):
                valid += 1
                break
    return valid / len(citations)


def _llm_judge_faithful(
    question: str,
    answer: str,
    retrieved: list[dict],
    ollama_url: str,
    model: str,
) -> bool | None:
    """Use local Ollama as an independent judge checking whether `answer` is faithful to retrieved refs."""
    if not retrieved:
        return None
    ctx = "\n".join(
        f"- {r.get('source')} | {r.get('section')} (score={r.get('score')})"
        for r in retrieved[:5]
    )
    prompt = (
        "You are a strict RAG evaluator. Given the question, retrieved source sections, and model answer, "
        "reply ONLY with YES if the answer directly addresses the question without inventing outside claims, "
        "or NO if it hallucinates or contradicts the sources.\n\n"
        f"Question: {question}\n"
        f"Retrieved Sections:\n{ctx}\n"
        f"Answer: {answer}\n\nVerdict (YES or NO):"
    )
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{ollama_url.rstrip('/')}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.0}},
            )
            r.raise_for_status()
            text = (r.json().get("response") or "").strip().upper()
            if "YES" in text and "NO" not in text[:10]:
                return True
            if "NO" in text[:10]:
                return False
    except Exception:
        return None
    return None


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


def score_e2e(
    rows: list[dict],
    *,
    use_llm_judge: bool = True,
    ollama_url: str | None = None,
    judge_model: str | None = None,
) -> dict[str, Any]:
    ollama_url = ollama_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    judge_model = judge_model or os.environ.get("LLM_MODEL", "llama3.2:3b")

    latencies: list[float] = []
    citations_total = 0
    citations_valid = 0
    abstain_required = 0
    abstain_correct = 0

    faithful_total = 0
    faithful_hits = 0

    judge_agreements = 0
    judge_total = 0

    per_question: list[dict] = []

    for row in rows:
        qid = str(row.get("id"))
        resp = row.get("response") or {}
        latency_ms = float(row.get("latency_ms", 0.0))
        latencies.append(latency_ms)

        must_abstain = bool(row.get("must_abstain", False))
        answered_from_context = bool(resp.get("answered_from_context", False))
        answer_text = str(resp.get("answer") or "")
        retrieved = resp.get("retrieved") or []
        citations = resp.get("citations") or []

        if must_abstain:
            abstain_required += 1
            if not answered_from_context:
                abstain_correct += 1

        for cit in citations:
            citations_total += 1
            if any(cit.get("source") == p.get("source") and section_matches(cit.get("section"), p.get("section"))
                   for p in retrieved):
                citations_valid += 1

        # Faithfulness: answerable questions must be grounded (citations valid) & contain expected concept OR abstain safely
        expect = str(row.get("expect") or "").strip().lower()
        is_faithful: bool | None = None
        if not must_abstain:
            faithful_total += 1
            cit_ok = bool(citations) and all(
                any(c.get("source") == p.get("source") and section_matches(c.get("section"), p.get("section"))
                    for p in retrieved)
                for c in citations
            )
            expect_ok = True
            if expect:
                norm_ans = re.sub(r"[^a-z0-9.]+", " ", answer_text.lower())
                norm_exp = re.sub(r"[^a-z0-9.]+", " ", expect)
                tokens = [t for t in norm_exp.split() if len(t) >= 2]
                matched = sum(1 for t in tokens if t in norm_ans)
                expect_ok = matched >= max(1, len(tokens) // 2)

            judge_verdict: bool | None = None
            if use_llm_judge and answered_from_context and cit_ok:
                judge_verdict = _llm_judge_faithful(
                    row["question"], answer_text, retrieved, ollama_url, judge_model
                )

            if answered_from_context and cit_ok and (expect_ok or judge_verdict is True):
                is_faithful = True
                faithful_hits += 1
            elif not answered_from_context:
                # Honest abstention on borderline OCR/low-score chunk is not a hallucination,
                # though recall/coverage tracks it; count as unfaithful only if it fabricated claims.
                is_faithful = False
            else:
                is_faithful = False
        else:
            # Must-abstain correctly refused is 100% faithful; answering from context is unfaithful hallucination
            faithful_total += 1
            is_faithful = not answered_from_context
            if is_faithful:
                faithful_hits += 1

        if qid in HUMAN_GOLD_LABELS:
            human_ok = (answered_from_context == HUMAN_GOLD_LABELS[qid]["should_answer"])
            fact = HUMAN_GOLD_LABELS[qid]["fact"]
            if fact and HUMAN_GOLD_LABELS[qid]["should_answer"]:
                human_ok = human_ok and (fact.lower() in answer_text.lower())
            auto_verdict = bool(is_faithful)
            judge_total += 1
            if human_ok == auto_verdict:
                judge_agreements += 1

        per_question.append({
            "id": qid,
            "must_abstain": must_abstain,
            "answered_from_context": answered_from_context,
            "faithful": is_faithful,
            "citations": len(citations),
            "latency_ms": round(latency_ms, 2),
        })

    return {
        "faithfulness": round(faithful_hits / faithful_total, 4) if faithful_total else 0.0,
        "abstention": round(abstain_correct / abstain_required, 4) if abstain_required else 1.0,
        "abstention_required": abstain_required,
        "abstention_correct": abstain_correct,
        "citation_validity": round(citations_valid / citations_total, 4) if citations_total else 1.0,
        "citations_total": citations_total,
        "citations_valid": citations_valid,
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "judge_human_labels": judge_total,
        "judge_human_agreements": judge_agreements,
        "judge_human_agreement_rate": round(judge_agreements / judge_total, 4) if judge_total else None,
        "per_question": per_question,
    }
