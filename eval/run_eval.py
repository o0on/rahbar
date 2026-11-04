#!/usr/bin/env python3
"""Score a running RAHBAR against a golden set, and gate CI on the result (section 5).

    python eval/run_eval.py --api http://<ingress>                 # your corpus
    python eval/run_eval.py --api ... --golden eval/fixtures/golden.fixture.yaml
    python eval/run_eval.py --api ... --update-baseline            # after a deliberate improvement

Exit code 0 when every gate passes, 1 when any fails. CI relies on that exit code.

Given: talking to the API, reading the golden set, writing results.json, the exit
code. The four scoring functions (recall@k, citation validity, summarise, gate)
are the evaluation itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent

# The gate from section 5.4. Change these only with a reason written in docs/PIPELINE.md.
MAX_RECALL5_DROP = 0.05        # recall@5 may fall at most 5 points below the baseline
MIN_CITATION_VALIDITY = 0.95   # at least 95% of citations must match retrieved passages
REQUIRED_ABSTENTION = 1.0      # every must-abstain question must be declined


def load_golden(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    if not questions:
        sys.exit(f"no questions in {path}")
    return questions


def ask(client: httpx.Client, api: str, question: str) -> dict:
    """POST one question and return the AskResponse as a dict (see api/app/schemas.py)."""
    response = client.post(f"{api.rstrip('/')}/api/ask", json={"question": question})
    response.raise_for_status()
    return response.json()


def _normalise(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _section_matches(claimed: object, actual: object) -> bool:
    """The same rule api/app/services/grounding.py uses: "5.4" matches
    "5.4 Academic Warning" in either direction, because golden entries and
    models both shorten headings."""
    left, right = _normalise(claimed), _normalise(actual)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _relevant_hits(relevant: list[dict], retrieved: list[dict]) -> int:
    found = 0
    for want in relevant:
        chunk_id = want.get("chunk_id")
        source = want.get("source")
        section = want.get("section")
        for got in retrieved:
            if chunk_id and got.get("chunk_id") == chunk_id:
                found += 1
                break
            if source and got.get("source") != source:
                continue
            if section and not _section_matches(section, got.get("section")):
                continue
            if source or section:
                found += 1
                break
    return found


def recall_at_k(relevant: list[dict], retrieved: list[dict], k: int) -> float | None:
    """Fraction of the relevant (source, section) pairs found in the top k retrieved.
    Return None when the question has no relevant passages (must-abstain questions)."""
    if not relevant:
        return None
    return round(_relevant_hits(relevant, retrieved[:k]) / len(relevant), 4)


def citation_validity(response: dict) -> float | None:
    """Fraction of the response's citations that match a passage in response['retrieved'].
    Return None when there are no citations."""
    citations = response.get("citations") or []
    if not citations:
        return None
    retrieved = response.get("retrieved") or []
    valid = 0
    for citation in citations:
        for passage in retrieved:
            if citation.get("source") == passage.get("source") and \
                    _section_matches(citation.get("section"), passage.get("section")):
                valid += 1
                break
    return round(valid / len(citations), 4)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarise(rows: list[dict]) -> dict:
    """Overall recall@3, recall@5, citation validity and abstention correctness, plus
    recall@5 broken down by question type. This dict becomes results.json."""
    per_question: list[dict] = []
    by_type: dict[str, list[float]] = {}
    citations_total = citations_valid = 0
    required = correct = 0
    abstained = answered = 0

    for row in rows:
        response = row.get("response") or {}
        retrieved = response.get("retrieved") or []
        recall3 = recall_at_k(row.get("relevant") or [], retrieved, 3)
        recall5 = recall_at_k(row.get("relevant") or [], retrieved, 5)
        validity = citation_validity(response)
        is_abstention = not response.get("answered_from_context", True)
        if is_abstention:
            abstained += 1
        else:
            answered += 1
        if row.get("must_abstain"):
            required += 1
            if is_abstention:
                correct += 1
        if validity is not None:
            for citation in response.get("citations") or []:
                citations_total += 1
                if citation.get("source") and any(
                    citation.get("source") == p.get("source")
                    and _section_matches(citation.get("section"), p.get("section"))
                    for p in retrieved
                ):
                    citations_valid += 1
        if recall5 is not None:
            by_type.setdefault(row.get("type", "ordinary"), []).append(recall5)
        per_question.append({
            "id": row.get("id"), "type": row.get("type", "ordinary"),
            "recall_at_3": recall3, "recall_at_5": recall5,
            "citation_validity": validity,
            "must_abstain": bool(row.get("must_abstain")),
            "answered_from_context": response.get("answered_from_context"),
            "cached": bool(response.get("cached")),
            "seconds": row.get("seconds"),
        })

    results: dict = {
        "recall_at_3": _mean([r["recall_at_3"] for r in per_question
                              if r["recall_at_3"] is not None]),
        "recall_at_5": _mean([r["recall_at_5"] for r in per_question
                              if r["recall_at_5"] is not None]),
        "recall_at_5_by_type": {t: _mean(v) for t, v in sorted(by_type.items())},
        "citation_validity": (round(citations_valid / citations_total, 4)
                              if citations_total else None),
        "citations_total": citations_total,
        "citations_valid": citations_valid,
        "abstention_correctness": (round(correct / required, 4) if required else None),
        "abstention_required": required,
        "abstention_correct": correct,
        "answers_from_context": answered,
        "answers_abstained": abstained,
        "per_question": per_question,
    }
    return results


def gate(results: dict, baseline: dict | None) -> list[str]:
    """Return one message per failed gate (section 5.4); an empty list means pass."""
    failures: list[str] = []

    recall5 = results.get("recall_at_5")
    if baseline and baseline.get("recall_at_5") is not None:
        allowed = baseline["recall_at_5"] - MAX_RECALL5_DROP
        if recall5 is None:
            failures.append("recall@5 could not be computed (no answerable questions?)")
        elif recall5 < allowed:
            failures.append(
                f"recall@5 {recall5:.4f} is below the baseline "
                f"{baseline['recall_at_5']:.4f} - more than "
                f"{MAX_RECALL5_DROP:.0%} of ground lost"
            )

    validity = results.get("citation_validity")
    if validity is None:
        failures.append("no citations were produced at all - every answer cited nothing")
    elif validity < MIN_CITATION_VALIDITY:
        failures.append(
            f"citation validity {validity:.4f} is below {MIN_CITATION_VALIDITY:.0%} - "
            f"a citation that does not match a retrieved chunk costs -10"
        )

    correctness = results.get("abstention_correctness")
    if correctness is None:
        failures.append("the golden set has no must-abstain questions - "
                        "abstention correctness cannot be measured")
    elif correctness < REQUIRED_ABSTENTION:
        wrong = [r["id"] for r in results.get("per_question", [])
                 if r.get("must_abstain") and r.get("answered_from_context")]
        failures.append(
            f"must-abstain questions answered from context: {', '.join(map(str, wrong))} "
            f"({correctness:.0%} correct, 100% required)"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAHBAR against a golden set.")
    parser.add_argument("--api", required=True, help="base URL, e.g. http://192.168.49.2")
    parser.add_argument("--host", help="Host header, when your Ingress rule names a host")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.yaml")
    parser.add_argument("--baseline", type=Path, default=HERE / "baseline.json")
    parser.add_argument("--out", type=Path, default=HERE / "results.json")
    parser.add_argument("--update-baseline", action="store_true",
                        help="write these results as the new baseline")
    args = parser.parse_args()

    questions = load_golden(args.golden)
    rows = []
    headers = {"Host": args.host} if args.host else {}
    with httpx.Client(timeout=120.0, headers=headers) as client:
        for q in questions:
            started = time.perf_counter()
            response = ask(client, args.api, q["question"])
            rows.append({
                "id": q["id"], "type": q.get("type", "ordinary"),
                "must_abstain": bool(q.get("must_abstain", False)),
                "relevant": q.get("relevant") or [],
                "expect": q.get("expect"),
                "response": response,
                "seconds": round(time.perf_counter() - started, 2),
            })
            print(f"  {q['id']:<5} {rows[-1]['seconds']:>6.2f}s  {q['question'][:60]}")

    results = summarise(rows)
    baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else None
    failures = gate(results, baseline)

    results["golden"] = str(args.golden)
    results["questions"] = len(rows)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {args.out}")
    if args.update_baseline:
        args.baseline.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"baseline updated: {args.baseline}")

    if failures:
        print("\nGATE FAILED")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\ngate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
