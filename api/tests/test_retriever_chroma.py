"""Retriever tests: the embedding pin, the superseded filter (failure mode E),
cosine distance -> similarity, and the empty-index case - against a fake
Chroma client, no cluster needed (sections 3.2, 4.4)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.retrieval import chroma as chroma_module
from app.retrieval.chroma import OVERFETCH, ChromaRetriever
from app.schemas import Passage


class FakeCollection:
    def __init__(self, results: dict | None = None, count: int = 10) -> None:
        self.results = results or {"ids": [[]], "documents": [[]],
                                   "metadatas": [[]], "distances": [[]]}
        self._count = count
        self.queries: list[dict] = []

    def count(self) -> int:
        return self._count

    def query(self, **kwargs: object) -> dict:
        self.queries.append(kwargs)
        return self.results


class FakeClient:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection

    def get_collection(self, name: str) -> FakeCollection:
        return self.collection


def make_retriever(collection: FakeCollection) -> ChromaRetriever:
    """Built without __init__: the constructor would open a real HTTP client."""
    retriever = ChromaRetriever.__new__(ChromaRetriever)
    retriever.settings = Settings(retriever="chroma")
    retriever.client = FakeClient(collection)
    return retriever


def results(rows: list[tuple[str, str, dict, float]]) -> dict:
    return {"ids": [[r[0] for r in rows]],
            "documents": [[r[1] for r in rows]],
            "metadatas": [[r[2] for r in rows]],
            "distances": [[r[3] for r in rows]]}


def meta(**over: object) -> dict:
    base: dict[str, object] = {"source": "Academic_Rules_Handbook_2026.pdf",
                               "section": "5.4 Academic Warning", "page": 12,
                               "doc_version": "2026-06", "superseded": False}
    base.update(over)
    return base


def test_empty_index_returns_nothing_and_warns_instead_of_raising() -> None:
    collection = FakeCollection(count=0)
    assert make_retriever(collection).retrieve_vector([1.0, 0.0], "Q?", 5) == []
    assert collection.queries == []                 # nothing to ask Chroma for


def test_superseded_documents_are_filtered_out_of_results() -> None:
    collection = FakeCollection(results=results([
        ("old-p001-0000", "Repealed rule text.", meta(superseded=True), 0.01),
        ("new-p001-0000", "Current rule text.", meta(), 0.20),
    ]))
    passages = make_retriever(collection).retrieve_vector([1.0, 0.0], "Q?", 5)
    assert [p.chunk_id for p in passages] == ["new-p001-0000"]
    assert collection.queries[0]["where"] == {"superseded": False}
    assert passages[0].score == pytest.approx(0.8)   # cosine distance -> similarity


def test_client_side_double_check_drops_a_superseded_row() -> None:
    """Even if the server ignores the filter, a repealed clause never answers."""
    collection = FakeCollection(results=results([
        ("old-p001-0000", "Repealed rule text.", meta(superseded=True), 0.01),
        ("new-p001-0000", "Current rule text.", meta(), 0.30),
    ]))
    passages = make_retriever(collection).retrieve_vector([1.0, 0.0], "Q?", 5)
    assert [p.chunk_id for p in passages] == ["new-p001-0000"]


def test_a_question_about_an_older_version_lifts_the_filter() -> None:
    collection = FakeCollection(results=results([
        ("old-p001-0000", "Repealed rule text.", meta(superseded=True), 0.05),
        ("new-p001-0000", "Current rule text.", meta(), 0.20),
    ]))
    question = "What did the 2023 handbook require before it was superseded?"
    passages = make_retriever(collection).retrieve_vector([1.0, 0.0], question, 5)
    assert collection.queries[0]["where"] is None
    assert [p.chunk_id for p in passages] == ["old-p001-0000", "new-p001-0000"]


def test_results_are_over_fetched_then_truncated_to_top_k() -> None:
    rows = [(f"c{i}", "text", meta(), 0.1 + i * 0.01) for i in range(20)]
    collection = FakeCollection(results=results(rows), count=len(rows))
    passages = make_retriever(collection).retrieve_vector([1.0, 0.0], "Q?", 3)
    assert len(passages) == 3                                    # top-k only
    assert collection.queries[0]["n_results"] == min(3 * OVERFETCH, 20)


def test_passages_carry_the_metadata_citations_need() -> None:
    collection = FakeCollection(results=results([
        ("handbook-2026-p012-0003", "text", meta(page=None, doc_version="2026-06"), 0.1),
    ]))
    passage = make_retriever(collection).retrieve_vector([1.0, 0.0], "Q?", 1)[0]
    assert isinstance(passage, Passage)
    assert passage.source == "Academic_Rules_Handbook_2026.pdf"
    assert passage.section == "5.4 Academic Warning"
    assert passage.page is None and passage.doc_version == "2026-06"
    assert passage.superseded is False


def test_query_is_embedded_with_the_pinned_model(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"embeddings": [[0.4, 0.5]]}

    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        seen.append(json or {})
        return FakeResponse()

    monkeypatch.setattr(chroma_module.httpx, "post", fake_post)
    retriever = make_retriever(FakeCollection(count=0))
    assert retriever.embed("Q?") == [0.4, 0.5]
    assert seen[0]["model"] == Settings().embed_model          # the indexer's model
    assert seen[0]["input"] == "Q?"


def test_an_unknown_retriever_name_fails_fast_at_startup() -> None:
    from app.retrieval import get_retriever

    with pytest.raises(ValueError, match="unknown RETRIEVER"):
        get_retriever(Settings(retriever="definitely-not-a-retriever"))
    assert get_retriever(Settings(retriever="stub")).name == "stub"
