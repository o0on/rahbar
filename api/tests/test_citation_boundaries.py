"""Integration tests verifying citation grounding boundaries and validation edge cases."""

from __future__ import annotations

import pytest
from app.schemas import Citation, Passage
from app.services.grounding import GroundingValidator


def test_grounding_accepts_exact_citations() -> None:
    passages = [
        Passage(
            chunk_id="chk-001",
            text="Minimum attendance required is 80%.",
            source="handbook-2026.pdf",
            section="1.21 Attendance",
            page=12,
            score=0.91,
        )
    ]
    citations = [
        Citation(source="handbook-2026.pdf", section="1.21 Attendance", page=12)
    ]
    validator = GroundingValidator()
    kept, pruned = validator.validate(citations, passages)
    assert len(kept) == 1
    assert len(pruned) == 0
    assert kept[0].source == "handbook-2026.pdf"


def test_grounding_rejects_unretrieved_sources() -> None:
    passages = [
        Passage(
            chunk_id="chk-001",
            text="Tuition refund policy.",
            source="fee-structure.html",
            section="Refund Timeline",
            page=1,
            score=0.88,
        )
    ]
    citations = [
        Citation(source="random-unretrieved-doc.pdf", section="Clause 9", page=99)
    ]
    validator = GroundingValidator()
    kept, pruned = validator.validate(citations, passages)
    assert len(kept) == 0
    assert len(pruned) == 1
    assert pruned[0].source == "random-unretrieved-doc.pdf"


def test_grounding_handles_empty_retrieval_set() -> None:
    citations = [
        Citation(source="handbook-2026.pdf", section="1.1 Overview", page=1)
    ]
    validator = GroundingValidator()
    kept, pruned = validator.validate(citations, [])
    assert len(kept) == 0
    assert len(pruned) == 1
