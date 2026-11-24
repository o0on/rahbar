"""Grounding: the relevance floor, honest abstention, citation verification.

Three rules from section 3.4, all enforced here rather than asked for in a
prompt:

* Nothing above the relevance floor -> abstain. The model is never consulted,
  so a question the corpus cannot answer cannot come back confident.
* Every citation must match a passage actually retrieved for this request. A
  citation the retriever never returned is fabricated, so it is pruned.
* If a model claims an answer came from the context but every citation it
  offered was fabricated, the claim itself is not grounded: it is downgraded
  to an abstention.
"""

from __future__ import annotations

import logging
import re

from ..schemas import Answer, Citation, Passage

log = logging.getLogger("rahbar.grounding")

ABSTAIN_ANSWER = (
    "The corpus does not cover this question, so I cannot answer it from the "
    "documents. I would rather say that than guess."
)

_NORMALISE = re.compile(r"[^a-z0-9]+")


def normalise(value: str | None) -> str:
    return _NORMALISE.sub(" ", (value or "").lower()).strip()


def section_matches(claimed: str | None, actual: str | None) -> bool:
    """A citation section matches when it names the same section.

    Golden entries and models both shorten headings: "5.4" must match
    "5.4 Academic Warning" in either direction. The same rule is duplicated in
    eval/run_eval.py so the metric and the guard cannot drift apart.
    """
    left, right = normalise(claimed), normalise(actual)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def resolve_citation(citation: Citation, passages: list[Passage]) -> Citation:
    """If the model cited by passage index e.g. '[1]' or '1', resolve to the passage's actual source."""
    src = (citation.source or "").strip()
    match = re.match(r"^\[?(\d+)\]?$", src)
    if match:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(passages):
            return citation.model_copy(update={"source": passages[idx].source})
    return citation


def citation_is_grounded(citation: Citation, passages: list[Passage]) -> bool:
    resolved = resolve_citation(citation, passages)
    return any(
        resolved.source == passage.source
        and section_matches(resolved.section, passage.section)
        for passage in passages
    )


def below_floor(passages: list[Passage], floor: float) -> bool:
    """True when nothing retrieved clears the relevance floor (section 3.4)."""
    return not passages or max(passage.score for passage in passages) < floor


def abstention() -> Answer:
    """The honest refusal, built without consulting the model."""
    return Answer(
        reasoning="no retrieved passage cleared the relevance floor",
        answer=ABSTAIN_ANSWER,
        citations=[],
        answered_from_context=False,
        confidence="low",
    )


def ground(answer: Answer, passages: list[Passage]) -> tuple[Answer, int]:
    """Drop citations that do not match a retrieved passage. Returns the
    grounded answer and how many citations were pruned."""
    resolved = [resolve_citation(c, passages) for c in answer.citations]
    kept = [c for c in resolved if citation_is_grounded(c, passages)]
    pruned = len(answer.citations) - len(kept)
    if pruned:
        log.warning("pruned fabricated citations",
                    extra={"pruned": pruned,
                           "kept": [f"{c.source}/{c.section}" for c in kept]})
    grounded = answer.model_copy(update={"citations": kept})
    if answer.answered_from_context and answer.citations and not kept:
        grounded = grounded.model_copy(update={"answered_from_context": False,
                                               "confidence": "low"})
        log.warning("answer claimed context but every citation was fabricated; "
                    "downgraded to abstention")
    return grounded, pruned


class GroundingValidator:
    """Validator class for testing and verifying citation grounding."""

    def validate(
        self, citations: list[Citation], passages: list[Passage]
    ) -> tuple[list[Citation], list[Citation]]:
        resolved = [resolve_citation(c, passages) for c in citations]
        kept = [c for c in resolved if citation_is_grounded(c, passages)]
        pruned = [c for c in citations if not citation_is_grounded(c, passages)]
        return kept, pruned
