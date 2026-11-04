"""Grounding and cache tests: the relevance floor, citation verification,
the never-cache rules, and the Redis round trip - with a fake Redis, so the
rules that protect users are tested without a server (sections 3.3, 3.4)."""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.schemas import Answer, AskResponse, Citation, Passage
from app.services.cache import (CACHE_GENERATION_KEY, ENTRIES_KEY,
                                INDEX_GENERATION_KEY, SemanticCache, cosine,
                                is_followup, is_version_question,
                                should_cache)
from app.services.grounding import (ABSTAIN_ANSWER, abstention, below_floor,
                                    citation_is_grounded, ground,
                                    section_matches)


def passage(**over: object) -> Passage:
    base: dict[str, object] = dict(
        chunk_id="handbook-2026-p004-0002", text="A CGPA below 2.00 triggers "
        "an academic warning.", source="Academic_Rules_Handbook_2026.pdf",
        section="5.4 Academic Warning", page=12, score=0.81)
    base.update(over)
    return Passage(**base)  # type: ignore[arg-type]


def answer(**over: object) -> Answer:
    base: dict[str, object] = dict(
        reasoning="the handbook states the threshold",
        answer="A CGPA below 2.00 triggers an academic warning [Academic_Rules_Handbook_2026 / 5.4].",
        citations=[Citation(source="Academic_Rules_Handbook_2026.pdf",
                            section="5.4 Academic Warning", page=12)],
        answered_from_context=True, confidence="high")
    base.update(over)
    return Answer(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------- grounding

@pytest.mark.parametrize("claimed,actual,expected", [
    ("5.4", "5.4 Academic Warning", True),       # either side may be shortened
    ("5.4 Academic Warning", "5.4", True),
    ("academic warning", "5.4 Academic Warning", True),
    ("5.5 Attendance", "5.4 Academic Warning", False),
    ("", "5.4 Academic Warning", False),
    ("5.4", None, False),
])
def test_section_matching_in_both_directions(claimed: str, actual: str | None,
                                             expected: bool) -> None:
    assert section_matches(claimed, actual) is expected


def test_below_floor_when_nothing_is_close_enough() -> None:
    assert below_floor([], 0.65) is True
    assert below_floor([passage(score=0.42)], 0.65) is True
    assert below_floor([passage(score=0.42), passage(score=0.9)], 0.65) is False


def test_abstention_is_built_without_a_model() -> None:
    refused = abstention()
    assert refused.answered_from_context is False
    assert refused.citations == []
    assert refused.confidence == "low"
    assert refused.answer == ABSTAIN_ANSWER


def test_citation_is_grounded_only_against_retrieved_passages() -> None:
    retrieved = [passage()]
    good = Citation(source="Academic_Rules_Handbook_2026.pdf",
                    section="5.4", page=12)
    ghost = Citation(source="Handbook_2019.pdf", section="5.4", page=12)
    assert citation_is_grounded(good, retrieved) is True
    assert citation_is_grounded(ghost, retrieved) is False


def test_ground_prunes_fabricated_citations() -> None:
    fabricated = Citation(source="Handbook_2019.pdf", section="9.9 Old Rule")
    grounded, pruned = ground(answer(citations=[*answer().citations, fabricated]),
                              [passage()])
    assert pruned == 1
    assert [c.source for c in grounded.citations] == ["Academic_Rules_Handbook_2026.pdf"]


def test_ground_downgrades_an_answer_that_cited_nothing_real() -> None:
    """Claimed context with only fabricated citations is not grounded (3.4)."""
    fake_only = answer(citations=[Citation(source="ghost.pdf", section="nowhere")])
    grounded, pruned = ground(fake_only, [passage()])
    assert pruned == 1
    assert grounded.answered_from_context is False
    assert grounded.confidence == "low"
    assert grounded.citations == []


# ----------------------------------------------------------------------- cache

@pytest.mark.parametrize("question,expected", [
    ("What is the attendance requirement?", False),
    ("what about fees?", True),
    ("And the exam dates?", True),
    ("what else can you tell me?", True),
    ("Why is the lab closed?", True),
    ("How about the previous rule?", True),
])
def test_follow_up_detection(question: str, expected: bool) -> None:
    assert is_followup(question) is expected


def test_never_cache_a_follow_up_an_abstention_or_a_fallback() -> None:
    grounded = answer()
    assert should_cache("What is the attendance requirement?", grounded, False) is True
    assert should_cache("what about fees?", grounded, False) is False          # follow-up
    assert should_cache("Q?", grounded, True) is False                         # fallback
    refused = answer(answered_from_context=False, citations=[])
    assert should_cache("Q?", refused, False) is False                         # abstention
    assert should_cache("Q?", answer(citations=[]), False) is False            # no citation
    old_rules = "Under the August 2023 rules, until when could I withdraw?"    # version
    assert should_cache(old_rules, grounded, False) is False


@pytest.mark.parametrize("question,expected", [
    ("Until when can a student withdraw from a course?", False),   # current rules
    ("What does the university charge per credit hour for 2026-27?", False),
    ("Under the August 2023 academic rules, until when could a "
     "student withdraw from a course?", True),
    ("What does the superseded August 2023 rules document say?", True),
    ("How did the old rules handle academic warning?", True),
])
def test_version_question_detection(question: str, expected: bool) -> None:
    assert is_version_question(question) is expected


def test_cosine_similarity_basics() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0     # length mismatch
    assert cosine([], []) == 0.0


class FakeRedis:
    """Just enough of redis-py for SemanticCache's four commands."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def set(self, key: str, value: str) -> None:
        self.strings[key] = value

    def delete(self, key: str) -> None:
        self.strings.pop(key, None)
        self.hashes.pop(key, None)

    def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def hdel(self, key: str, field: str) -> None:
        self.hashes.get(key, {}).pop(field, None)

    def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds

    def ping(self) -> bool:
        return True


def make_cache(threshold: float = 0.88) -> tuple[SemanticCache, FakeRedis]:
    fake = FakeRedis()
    return SemanticCache(Settings(retriever="chroma", cache_threshold=threshold),
                         client=fake), fake


def test_cache_round_trip_marks_the_hit_as_cached() -> None:
    cache, fake = make_cache()
    response = AskResponse(
        request_id="r1", answer="A CGPA below 2.00 triggers a warning.",
        citations=[Citation(source="Academic_Rules_Handbook_2026.pdf",
                            section="5.4 Academic Warning", page=12)],
        answered_from_context=True, confidence="high", retrieved=[], provider="ollama")
    cache.store("What happens below a 2.00 CGPA?", [1.0, 0.0], response)
    assert fake.hashes[ENTRIES_KEY] and fake.ttls[ENTRIES_KEY] > 0

    hit = cache.lookup("What happens below a 2.00 CGPA?", [1.0, 0.0])
    assert hit is not None and hit.cached is True
    assert hit.answer == response.answer
    assert hit.request_id == ""        # the old id is dropped; the caller re-stamps

    miss = cache.lookup("What happens below a 2.00 CGPA?", [0.0, 1.0])
    assert miss is None                    # orthogonal question: below threshold


def test_cache_never_serves_across_a_reindex() -> None:
    """A generation bump from the indexer Job flushes everything (3.3)."""
    cache, fake = make_cache()
    response = AskResponse(request_id="r", answer="old", citations=[
        Citation(source="s.pdf", section="1", page=1)],
        answered_from_context=True, confidence="high", retrieved=[], provider="ollama")
    cache.store("q", [1.0, 0.0], response)
    fake.strings[INDEX_GENERATION_KEY] = "generation-2"      # the Job re-indexed
    fake.strings[CACHE_GENERATION_KEY] = "generation-1"

    assert cache.lookup("q", [1.0, 0.0]) is None
    assert ENTRIES_KEY not in fake.hashes                    # entries wiped
    assert fake.strings[CACHE_GENERATION_KEY] == "generation-2"


def test_cache_drops_a_malformed_entry_instead_of_serving_it() -> None:
    cache, fake = make_cache()
    from app.services.cache import _key                      # the field store() uses
    fake.hset(ENTRIES_KEY, _key("q"), json.dumps(
        {"question": "q", "vector": [1.0], "response": {"unexpected": True}}))
    assert cache.lookup("q", [1.0]) is None
    assert not fake.hashes.get(ENTRIES_KEY)                   # and it is gone


def test_cache_survives_a_dead_redis() -> None:
    class DeadRedis(FakeRedis):
        def hgetall(self, key: str) -> dict[str, str]:
            raise ConnectionError("redis is down")

        def ping(self) -> bool:
            raise ConnectionError("redis is down")

    cache = SemanticCache(Settings(retriever="chroma"), client=DeadRedis())
    assert cache.lookup("q", [1.0]) is None                   # miss, never a 500
    assert cache.ping() is False
