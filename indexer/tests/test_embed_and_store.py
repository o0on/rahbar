"""Stage 4 and 5 tests: embedding (batching, the pin, retries) and storing
(upsert + prune, rebuild on model change, cache invalidation) - all against
fakes, so no Ollama, Chroma or Redis is needed (section 6)."""

from __future__ import annotations

import httpx
import pytest

from pipeline import embed as embed_module
from pipeline import store as store_module
from pipeline.config import IndexerSettings
from pipeline.embed import embed
from pipeline.models import Chunk
from pipeline.store import store


def make_settings(**over: object) -> IndexerSettings:
    base: dict[str, object] = dict(
        embed_model="nomic-embed-text", chroma_host="chroma", chroma_port=8000,
        collection="rahbar", corpus_dir="/corpus", chunk_size=400,
        chunk_overlap=50, ollama_url="http://ollama:11434",
        redis_host="redis", redis_port=6379,
    )
    base.update(over)
    return IndexerSettings(**base)  # type: ignore[arg-type]


class FakeResponse:
    """The three methods of httpx.Response that the pipeline uses."""

    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=httpx.Request("POST", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict:
        return self._body


def make_chunks(n: int) -> list[Chunk]:
    return [
        Chunk(id=f"handbook-2026-p001-{i:04d}", text=f"chunk {i}",
              source="handbook.pdf", section="1. Intro", page=1,
              doc_version="2026-06", superseded=False)
        for i in range(n)
    ]


# ------------------------------------------------------------------------ embed

def test_embed_uses_the_pinned_model_and_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        seen.append(json or {})
        return FakeResponse(200, {"embeddings": [[0.1, 0.2] for _ in json["input"]]})

    monkeypatch.setattr(embed_module.httpx, "post", fake_post)
    vectors = embed(["alpha", "beta", "gamma"], make_settings())
    assert len(vectors) == 3
    assert all(len(v) == 2 for v in vectors)
    assert seen[0]["model"] == "nomic-embed-text"          # the pin, nothing else
    assert seen[0]["input"] == ["alpha", "beta", "gamma"]   # one batched request


def test_embed_falls_back_to_the_legacy_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server without /api/embed answers per chunk on /api/embeddings."""
    urls: list[str] = []

    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        urls.append(url)
        if url.endswith("/api/embed"):
            return FakeResponse(404)
        return FakeResponse(200, {"embedding": [0.5, 0.6]})

    monkeypatch.setattr(embed_module.httpx, "post", fake_post)
    vectors = embed(["one", "two"], make_settings())
    assert vectors == [[0.5, 0.6], [0.5, 0.6]]
    assert sum(u.endswith("/api/embeddings") for u in urls) == 2


def test_embed_retries_a_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def flaky_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return FakeResponse(200, {"embeddings": [[0.1] for _ in json["input"]]})

    monkeypatch.setattr(embed_module.httpx, "post", flaky_post)
    monkeypatch.setattr(embed_module.time, "sleep", lambda _s: None)   # no real waits
    assert embed(["text"], make_settings()) == [[0.1]]
    assert attempts["n"] == 2


def test_embed_rejects_mixed_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        return FakeResponse(200, {"embeddings": [[0.1, 0.2], [0.3]]})

    monkeypatch.setattr(embed_module.httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="different dimensions"):
        embed(["a", "b"], make_settings())


def test_embed_of_nothing_makes_no_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        embed_module.httpx, "post",
        lambda *a, **k: pytest.fail("no HTTP call expected for an empty batch"),
    )
    assert embed([], make_settings()) == []


# ------------------------------------------------------------------------ store

class FakeCollection:
    def __init__(self, metadata: dict | None = None, ids: list[str] | None = None) -> None:
        self.metadata = metadata
        self.ids = list(ids or [])
        self.upserts: list[list[str]] = []
        self.deleted: list[str] = []
        self.modified: dict | None = None

    def upsert(self, ids: list[str], documents: list[str],
               embeddings: list[list[float]], metadatas: list[dict]) -> None:
        self.upserts.append(list(ids))
        for chunk_id in ids:
            if chunk_id not in self.ids:
                self.ids.append(chunk_id)

    def get(self, include: object = None) -> dict:
        return {"ids": list(self.ids)}

    def delete(self, ids: list[str]) -> None:
        self.deleted.extend(ids)
        self.ids = [i for i in self.ids if i not in set(ids)]

    def modify(self, metadata: dict) -> None:
        self.modified = metadata
        self.metadata = metadata

    def count(self) -> int:
        return len(self.ids)


class FakeClient:
    def __init__(self, collection: FakeCollection | None = None, exists: bool = True) -> None:
        self.collection = collection
        self.exists = exists
        self.created: list[dict] = []
        self.deleted_collections: list[str] = []

    def get_collection(self, name: str) -> FakeCollection:
        if not self.exists or self.collection is None:
            raise RuntimeError(f"collection {name} does not exist")
        return self.collection

    def create_collection(self, name: str, metadata: dict) -> FakeCollection:
        self.created.append(metadata)
        self.collection = FakeCollection(metadata=metadata)
        self.exists = True
        return self.collection

    def delete_collection(self, name: str) -> None:
        self.deleted_collections.append(name)
        self.exists = False
        self.collection = None


@pytest.fixture()
def invalidated(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake_invalidate(settings: IndexerSettings, generation: str) -> None:
        calls.append((settings.collection, generation))

    monkeypatch.setattr(store_module, "invalidate_cache", fake_invalidate)
    return calls


def test_store_upserts_prunes_stale_ids_and_records_the_pin(
    monkeypatch: pytest.MonkeyPatch, invalidated: list[tuple[str, str]],
) -> None:
    existing = FakeCollection(
        metadata={"embed_model": "nomic-embed-text", "dimension": 2},
        ids=["handbook-2026-p001-0000", "old-run-chunk-that-is-gone"],
    )
    client = FakeClient(collection=existing)
    monkeypatch.setattr(store_module, "get_client", lambda settings: client)

    chunks, vectors = make_chunks(3), [[0.1, 0.2]] * 3
    count = store(chunks, vectors, make_settings())

    assert existing.upserts == [[c.id for c in chunks]]       # upsert, never drop
    assert existing.deleted == ["old-run-chunk-that-is-gone"]  # prune only the stale id
    assert count == 3
    assert existing.modified is not None
    assert existing.modified["embed_model"] == "nomic-embed-text"   # section 4.4 pin
    assert existing.modified["dimension"] == 2
    assert existing.modified["chunks"] == 3
    assert existing.metadata["hnsw:space"] == "cosine"
    assert invalidated and invalidated[0][1] == existing.modified["generation"]


def test_store_rebuilds_when_the_collection_was_built_by_another_model(
    monkeypatch: pytest.MonkeyPatch, invalidated: list[tuple[str, str]],
) -> None:
    old = FakeCollection(metadata={"embed_model": "all-MiniLM-L6-v2", "dimension": 384},
                         ids=["stale"])
    client = FakeClient(collection=old)
    monkeypatch.setattr(store_module, "get_client", lambda settings: client)

    store(make_chunks(2), [[0.1, 0.2]] * 2, make_settings())

    assert client.deleted_collections == ["rahbar"]       # incomparable vectors: rebuild
    assert client.created and client.created[0]["embed_model"] == "nomic-embed-text"
    assert client.created[0]["hnsw:space"] == "cosine"
    assert invalidated                                       # and the cache is flushed


def test_store_creates_the_collection_on_first_run(
    monkeypatch: pytest.MonkeyPatch, invalidated: list[tuple[str, str]],
) -> None:
    client = FakeClient(exists=False)
    monkeypatch.setattr(store_module, "get_client", lambda settings: client)
    count = store(make_chunks(1), [[0.1, 0.2]], make_settings())
    assert count == 1
    assert client.created[0]["embed_model"] == "nomic-embed-text"


def test_store_refuses_to_write_an_empty_or_mismatched_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store_module, "get_client",
                        lambda settings: pytest.fail("no write should happen"))
    with pytest.raises(RuntimeError, match="nothing to index"):
        store([], [], make_settings())
    with pytest.raises(RuntimeError, match="chunks but"):
        store(make_chunks(2), [[0.1]], make_settings())
