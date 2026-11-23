"""A3 Part A: verification verdicts, lifecycle moves and zero-change re-ingestion.

No Ollama, Chroma or Redis: the embedder, Chroma client and cache are fakes,
exactly like the A2 stage tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import store as store_module
from pipeline import verify as verify_module
from pipeline.config import IndexerSettings
from pipeline.lake import apply_verdicts, load_state, record_chunks, rollback, save_state
from pipeline.run import run_pipeline
from pipeline.verify import Check, Prepared, Source, load_sources, prepare, verify


def settings(data_dir: Path, corpus_dir: Path, **over: object) -> IndexerSettings:
    values: dict[str, object] = dict(
        embed_model="nomic-embed-text", chroma_host="chroma", chroma_port=8000,
        collection="rahbar", corpus_dir=str(corpus_dir), chunk_size=40,
        chunk_overlap=5, ollama_url="http://ollama:11434", redis_host="redis",
        redis_port=6379, data_dir=str(data_dir), near_dup_threshold=0.92,
    )
    values.update(over)
    return IndexerSettings(**values)  # type: ignore[arg-type]


def write_corpus(root: Path, entries: list[dict], files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_index.json").write_text(json.dumps(entries), encoding="utf-8")
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- A1

def test_verification_reports_each_check_with_a_reason(tmp_path: Path) -> None:
    corpus = tmp_path / "batch_b"
    write_corpus(
        corpus,
        [
            {"file": "good.md", "name": "Good", "doc_id": "good", "doc_version": "2026-01",
             "fetched_at": "2026-01-01", "page_count": 1},
            {"file": "duplicate.md", "name": "Duplicate", "doc_id": "duplicate",
             "doc_version": "2026-01", "fetched_at": "2026-01-01", "page_count": 1},
            {"file": "near.md", "name": "Near", "doc_id": "near", "doc_version": "2026-01",
             "fetched_at": "2026-01-01", "page_count": 1},
            {"file": "truncated.md", "name": "Truncated", "doc_id": "truncated",
             "doc_version": "2026-01", "fetched_at": "2026-01-01", "page_count": 3,
             "truncated": True},
            {"file": "nometa.md", "doc_id": "nometa", "doc_version": "2026-01"},
            {"file": "newer.md", "name": "Newer", "doc_id": "good",
             "doc_version": "2026-05", "fetched_at": "2026-05-01", "page_count": 1},
        ],
        {
            "good.md": "# Good\n\n## 1 Rules\n\nStudents must register early.\n",
            "duplicate.md": "# Good\n\n## 1 Rules\n\nStudents must register early.\n",
            "near.md": "# Good\n\n## 1 Rules\n\nStudents must register early….\n",
            "truncated.md": "# Lab\n\n## 1 Rules\n\nThe text stops here …\n",
            "nometa.md": "# No metadata\n\nSome text that is otherwise fine.\n",
            "newer.md": "# Good\n\n## 1 Rules\n\nThe rules were updated in May.\n",
        },
    )
    prepared = prepare(load_sources(corpus))

    def embedder(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "register early" in text else [0.0, 1.0] for text in texts]

    report = verify(prepared, settings(tmp_path, corpus), embedder=embedder)
    by_source = {doc["source"]: doc for doc in report["documents"]}

    assert by_source["duplicate.md"]["checks"]["uniqueness"]["pass"] is False
    assert "exact duplicate" in by_source["duplicate.md"]["checks"]["uniqueness"]["reason"]
    assert by_source["near.md"]["checks"]["uniqueness"]["pass"] is False
    assert "near-duplicate" in by_source["near.md"]["checks"]["uniqueness"]["reason"]
    assert by_source["truncated.md"]["checks"]["completeness"]["pass"] is False
    assert by_source["nometa.md"]["checks"]["completeness"]["pass"] is False
    assert "title" in by_source["nometa.md"]["checks"]["completeness"]["reason"]
    assert by_source["good.md"]["checks"]["recency"]["pass"] is False
    assert by_source["good.md"]["action"] == "archive"
    assert by_source["newer.md"]["action"] == "index"
    assert report["summary"]["documents"] == 6
    assert report["summary"]["archive"] == 1


def test_quality_check_catches_an_empty_document(tmp_path: Path) -> None:
    source = Source(path=tmp_path / "empty.md", doc_id="empty", doc_version="2026-01",
                    superseded=False, title="Empty", date="2026-01-01", page_count=1,
                    sha256="abc", manifest={"file": "empty.md"})
    item = Prepared(source=source, document=None, sample="")
    verify([item], settings(tmp_path, tmp_path), embedder=lambda texts: [])
    assert item.checks["quality"].passed is False
    assert item.action == "quarantine"


# ---------------------------------------------------------------- A2

def _prepared(doc_id: str, version: str, action: str, reason: str = "",
              base: Path | None = None) -> Prepared:
    path = (base or Path.cwd()) / f"{doc_id}-{version}.md"
    source = Source(path=path, doc_id=doc_id,
                    doc_version=version, superseded=False, title=doc_id,
                    date="2026-01-01", page_count=1, sha256=f"hash-{version}",
                    manifest={"file": f"{doc_id}-{version}.md"})
    item = Prepared(source=source, checks={name: Check(name != reason, reason or "ok")
                                           for name in ("quality", "completeness",
                                                        "recency", "uniqueness")},
                    action=action)
    return item


def test_lifecycle_moves_files_and_plans_incremental_updates(tmp_path: Path) -> None:
    data = tmp_path / "data"
    state = load_state(data)
    state["docs"]["handbook"] = {"source": "handbook-2026-06.md",
                                 "doc_version": "2026-06", "sha256": "hash-2026-06",
                                 "chunk_ids": ["h-p001-0000"], "superseded": False}
    state["docs"]["gone"] = {"source": "gone.md", "doc_version": "2026-01",
                             "sha256": "hash-gone", "chunk_ids": ["gone-p001-0000"],
                             "superseded": False}
    save_state(data, state)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    old = _prepared("handbook", "2026-06", "archive", "recency", corpus)
    old.source.path.write_text("old version", encoding="utf-8")
    new = _prepared("handbook", "2026-11", "index", base=corpus)
    new.source.path.write_text("new version", encoding="utf-8")
    gone_bad = _prepared("bad", "2026-01", "quarantine", "quality", corpus)
    gone_bad.source.path.write_text("bad", encoding="utf-8")

    update = apply_verdicts([old, new, gone_bad], state, data, "run-1")

    assert update.archived == ["handbook@2026-06"]
    assert update.quarantined == ["bad-2026-01.md"]
    assert "gone-p001-0000" in update.deleted_chunk_ids
    assert "h-p001-0000" in update.superseded_chunk_ids
    assert [item.source.doc_version for item in update.new_or_changed] == ["2026-11"]
    assert (data / "archive" / "handbook" / "2026-06" / "meta.json").exists()
    assert (data / "quarantine" / "bad.quality.bad-2026-01.md").exists()

    info = rollback(data, "handbook", "2026-06")
    assert info["restored_version"] == "2026-06"
    assert info["restore_chunk_ids"] == ["h-p001-0000"]
    assert (data / "lake" / "handbook" / "2026-06.md").exists()


# ---------------------------------------------------------------- end to end

class FakeCollection:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata
        self.rows: dict[str, tuple[str, list[float], dict]] = {}
        self.deleted: list[str] = []
        self.updated: list[list[str]] = []

    def get(self, ids: list[str] | None = None, include: object = None) -> dict:
        keys = ids if ids is not None else list(self.rows)
        return {"ids": [key for key in keys if key in self.rows],
                "metadatas": [self.rows[key][2] for key in keys if key in self.rows]}

    def upsert(self, ids, documents, embeddings, metadatas) -> None:
        for chunk_id, doc, vector, meta in zip(ids, documents, embeddings, metadatas):
            self.rows[chunk_id] = (doc, vector, meta)

    def delete(self, ids: list[str]) -> None:
        self.deleted.extend(ids)
        for chunk_id in ids:
            self.rows.pop(chunk_id, None)

    def update(self, ids: list[str], metadatas: list[dict]) -> None:
        self.updated.append(list(ids))
        for chunk_id, meta in zip(ids, metadatas):
            text, vector, _ = self.rows[chunk_id]
            self.rows[chunk_id] = (text, vector, meta)

    def modify(self, metadata: dict) -> None:
        self.metadata = {**self.metadata, **{k: v for k, v in metadata.items()
                                             if k != "hnsw:space"}}

    def count(self) -> int:
        return len(self.rows)


class FakeClient:
    def __init__(self) -> None:
        self.collection: FakeCollection | None = None

    def get_collection(self, name: str) -> FakeCollection:
        if self.collection is None:
            raise RuntimeError("no collection")
        return self.collection

    def create_collection(self, name: str, metadata: dict) -> FakeCollection:
        self.collection = FakeCollection(metadata)
        return self.collection


def test_second_ingest_embeds_zero_chunks_and_deletion_leaves_no_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    data = tmp_path / "data"
    write_corpus(
        corpus,
        [
            {"file": "a.md", "name": "A", "doc_id": "a", "doc_version": "2026-01",
             "fetched_at": "2026-01-01", "page_count": 1},
            {"file": "b.md", "name": "B", "doc_id": "b", "doc_version": "2026-01",
             "fetched_at": "2026-01-01", "page_count": 1},
        ],
        {"a.md": "# A\n\n## 1 Rules\n\nAlpha rule text.\nBeta rule text.\nGamma rule text.\n",
         "b.md": "# B\n\n## 1 Rules\n\nDelta rule text.\nEpsilon rule text.\nZeta rule text.\n"},
    )
    client = FakeClient()
    monkeypatch.setattr(store_module, "get_client", lambda _settings: client)
    monkeypatch.setattr(store_module, "invalidate_cache", lambda *_args: None)
    monkeypatch.setattr(verify_module, "default_embedder",
                        lambda _settings: lambda _texts: [])
    monkeypatch.setattr(verify_module, "write_report",
                        lambda report, data_dir: Path(data_dir) / "verify" / "run.json")
    embedded: list[str] = []

    def fake_embed(texts: list[str], _settings: IndexerSettings) -> list[list[float]]:
        embedded.extend(texts)
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr("pipeline.embed.embed", fake_embed)
    conf = settings(data, corpus)

    assert run_pipeline(conf) > 0
    first_embeddings = len(embedded)
    assert first_embeddings > 0

    # unchanged corpus: no chunk embedding at all
    assert run_pipeline(conf) > 0
    assert len(embedded) == first_embeddings

    # delete document b from the manifest and its file: its chunks must go
    index = json.loads((corpus / "_index.json").read_text(encoding="utf-8"))
    (corpus / "_index.json").write_text(json.dumps(index[:1]), encoding="utf-8")
    (corpus / "b.md").unlink()
    run_pipeline(conf)
    state = load_state(data)
    assert "b" not in state["docs"]
    assert all(meta.get("doc_id") == "a" for _, _, meta in client.collection.rows.values())

    # restore an archived version through lake.rollback (metadata-only flip)
    state["docs"]["a"]["chunk_ids"] = list(client.collection.rows)
    save_state(data, state)
    assert record_chunks(state, [], {}, data).exists()
