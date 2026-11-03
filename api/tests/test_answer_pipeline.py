"""answer_question orchestration: cache first, relevance floor before any model
call, citation grounding, honest 503 when the index is down - with fake
retriever, provider and cache (sections 3.2-3.4)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.schemas import Answer, AskResponse, Citation, Passage
from app.services.answer import answer_question


def passage(score: float = 0.9, source: str = "Academic_Rules_Handbook_2026.pdf",
            section: str = "5.4 Academic Warning") -> Passage:
    return Passage(chunk_id="handbook-2026-p004-0002",
                   text="A CGPA below 2.00 triggers an academic warning.",
                   source=source, section=section, page=12, score=score)


def grounded_answer(*citations: Citation) -> Answer:
    return Answer(reasoning="the handbook says so",
                  answer="A CGPA below 2.00 triggers a warning "
                         "[Academic_Rules_Handbook_2026 / 5.4].",
                  citations=list(citations),
                  answered_from_context=True, confidence="high")


VALID_CITATION = Citation(source="Academic_Rules_Handbook_2026.pdf",
                          section="5.4 Academic Warning", page=12)
GHOST_CITATION = Citation(source="Handbook_2019.pdf", section="9.9 Old Rule")


class FakeRetriever:
    name = "fake"

    def __init__(self, passages: list[Passage] | None = None,
                 error: Exception | None = None) -> None:
        self.passages = passages if passages is not None else [passage()]
        self.error = error

    def embed(self, question: str) -> list[float]:
        return [1.0, 0.0]

    def retrieve(self, question: str, k: int) -> list[Passage]:
        if self.error is not None:
            raise self.error
        return self.passages[:k]


class FakeProvider:
    name = "fake"

    def __init__(self, reply: Answer | None = None) -> None:
        self.calls = 0
        self.reply = reply

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        self.calls += 1
        if self.reply is not None:
            return self.reply
        return grounded_answer(*([VALID_CITATION] if passages else []))


class FakeCache:
    def __init__(self, hit: AskResponse | None = None) -> None:
        self.hit = hit
        self.lookups: list[str] = []
        self.stores: list[str] = []

    def lookup(self, question: str, vector: list[float]) -> AskResponse | None:
        self.lookups.append(question)
        return self.hit

    def store(self, question: str, vector: list[float], response: AskResponse) -> None:
        self.stores.append(question)


def ask(question: str, retriever: FakeRetriever, provider: FakeProvider,
        cache: FakeCache | None = None) -> AskResponse:
    return answer_question(question, retriever, provider,
                           Settings(retriever="chroma"), cache)


# ------------------------------------------------------------ the relevance floor

def test_floor_abstains_before_the_model_is_consulted() -> None:
    """Nothing close enough means no model call, so no confident guess (3.4)."""
    provider = FakeProvider()
    cache = FakeCache()
    response = ask("What is the tuition fee?", FakeRetriever([passage(score=0.2)]),
                   provider, cache)
    assert provider.calls == 0                     # never consulted
    assert response.answered_from_context is False
    assert response.citations == []
    assert "does not cover" in response.answer
    assert response.retrieved                      # the UI still sees what scored
    assert cache.stores == []                      # abstentions are never cached


def test_index_failure_is_a_503_not_a_500() -> None:
    retriever = FakeRetriever(error=RuntimeError("connection refused"))
    with pytest.raises(HTTPException) as excinfo:
        ask("What is the attendance rule?", retriever, FakeProvider())
    assert excinfo.value.status_code == 503
    assert "indexer" in str(excinfo.value.detail)


# ------------------------------------------------------------- citation grounding

def test_fabricated_citations_are_pruned_but_the_answer_survives() -> None:
    provider = FakeProvider(grounded_answer(VALID_CITATION, GHOST_CITATION))
    response = ask("What happens below a 2.00 CGPA?", FakeRetriever(), provider)
    assert [(c.source, c.section) for c in response.citations] == [
        ("Academic_Rules_Handbook_2026.pdf", "5.4 Academic Warning")]
    assert response.answered_from_context is True    # one real citation remains


def test_an_answer_that_cited_only_fabrications_is_downgraded() -> None:
    provider = FakeProvider(grounded_answer(GHOST_CITATION))
    response = ask("What happens below a 2.00 CGPA?", FakeRetriever(), provider)
    assert response.citations == []
    assert response.answered_from_context is False   # not grounded after all
    assert response.confidence == "low"


def test_a_fallback_reply_is_honest_and_not_cached() -> None:
    from app.providers.llm.common import safe_fallback

    provider = FakeProvider(safe_fallback("model wrote prose"))
    cache = FakeCache()
    response = ask("Q?", FakeRetriever(), provider, cache)
    assert response.answered_from_context is False
    assert response.citations == []
    assert cache.stores == []                        # a fallback is never cached


# ------------------------------------------------------------------------- cache

def test_cache_hit_short_circuits_the_whole_pipeline() -> None:
    hit = AskResponse(request_id="old", answer="cached answer",
                      citations=[VALID_CITATION], answered_from_context=True,
                      confidence="high", retrieved=[], provider="fake")
    provider, cache = FakeProvider(), FakeCache(hit=hit)
    response = ask("What happens below a 2.00 CGPA?", FakeRetriever(), provider, cache)
    assert provider.calls == 0                       # no retrieval, no generation
    assert response.cached is True
    assert response.answer == "cached answer"
    assert response.request_id == "-"                # re-stamped for this request


def test_follow_up_questions_bypass_the_cache_both_ways() -> None:
    hit = AskResponse(request_id="old", answer="cached answer",
                      citations=[VALID_CITATION], answered_from_context=True,
                      confidence="high", retrieved=[], provider="fake")
    provider, cache = FakeProvider(), FakeCache(hit=hit)
    response = ask("what about the fee structure?", FakeRetriever(), provider, cache)
    assert cache.lookups == []                       # never looked up...
    assert provider.calls == 1                       # answered from the model
    assert cache.stores == []                        # ...never stored either
    assert response.cached is False


def test_a_grounded_answer_is_stored_for_next_time() -> None:
    provider, cache = FakeProvider(), FakeCache()
    response = ask("What happens below a 2.00 CGPA?", FakeRetriever(), provider, cache)
    assert response.answered_from_context is True
    assert cache.lookups == ["What happens below a 2.00 CGPA?"]
    assert cache.stores == ["What happens below a 2.00 CGPA?"]


def test_x_cache_header_reports_miss_for_a_fresh_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RETRIEVER", "stub")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/ask", json={"question": "What does the stub say?"})
    assert r.status_code == 200
    assert r.headers["X-Cache"] == "miss"
    assert r.json()["cached"] is False
