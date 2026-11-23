"""Stage 5: Store. The only code in the whole system that writes to Chroma.

Decisions, written up in docs/adr/0001-indexer-as-job.md:

* The collection is never dropped during a normal re-index. Chunks are upserted
  with their stable ids and ids this run did not produce are pruned afterwards,
  so the count never touches zero and the API's readiness never blinks: the
  cluster keeps serving the old index until the new one has replaced it, piece
  by piece.
* If the embedding model (or the vector dimension) differs from what the
  collection was built with, upserting would mix incomparable vectors. That
  case rebuilds the collection from scratch: readiness then fails until the Job
  finishes, which is the honest state - serving garbage is not.
* The collection metadata records embed_model and dimension (section 4.4), so
  the API can refuse to start against an index built by another model.
* After every successful run the semantic cache is invalidated: a new index
  that keeps serving old answers is the bug section 3.3 warns about.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .config import IndexerSettings
from .log import log
from .models import Chunk

UPSERT_BATCH = 100


def get_client(settings: IndexerSettings) -> Any:
    import chromadb  # the thin client, from chromadb-client in requirements.txt
    from chromadb.config import Settings as ChromaSettings

    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port,
                               settings=ChromaSettings(anonymized_telemetry=False))


def _open_collection(client: Any, settings: IndexerSettings, dimension: int) -> Any:
    collection = None
    try:
        collection = client.get_collection(name=settings.collection)
    except Exception:                          # does not exist yet
        collection = None

    if collection is not None:
        meta = collection.metadata or {}
        built_with = meta.get("embed_model")
        built_dim = meta.get("dimension")
        if built_with == settings.embed_model and built_dim in (None, dimension):
            return collection
        log("collection was built by a different model - rebuilding",
            collection=settings.collection, indexed_model=built_with,
            runtime_model=settings.embed_model, indexed_dimension=built_dim,
            runtime_dimension=dimension)
        client.delete_collection(settings.collection)
        collection = None

    collection = client.create_collection(
        name=settings.collection,
        metadata={"hnsw:space": "cosine",           # scores are cosine similarity
                  "embed_model": settings.embed_model,
                  "dimension": dimension},
    )
    log("collection created", collection=settings.collection,
        embed_model=settings.embed_model, dimension=dimension)
    return collection


def store(chunks: list[Chunk], vectors: list[list[float]], settings: IndexerSettings) -> int:
    """Write every chunk with its vector and metadata, record the embedding
    model and vector dimension in the collection metadata (section 4.4),
    prune ids from previous runs, invalidate the semantic cache. Return the
    number of chunks now in the index."""
    if not chunks or not vectors:
        raise RuntimeError("nothing to index: the pipeline produced no chunks")
    if len(chunks) != len(vectors):
        raise RuntimeError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    dimension = len(vectors[0])

    started = time.perf_counter()
    client = get_client(settings)
    collection = _open_collection(client, settings, dimension)

    for start in range(0, len(chunks), UPSERT_BATCH):
        group = chunks[start:start + UPSERT_BATCH]
        collection.upsert(
            ids=[c.id for c in group],
            documents=[c.text for c in group],
            embeddings=vectors[start:start + UPSERT_BATCH],
            metadatas=[c.metadata() for c in group],
        )

    wanted = {c.id for c in chunks}
    existing = set(collection.get(include=[])["ids"])
    stale = sorted(existing - wanted)
    for start in range(0, len(stale), UPSERT_BATCH):
        collection.delete(ids=stale[start:start + UPSERT_BATCH])

    generation = uuid.uuid4().hex
    meta = dict(collection.metadata or {})
    # Chroma refuses modify() requests that mention hnsw:space at all, even
    # with the same value, so the distance function stays create-time only.
    meta.pop("hnsw:space", None)
    meta.update({"embed_model": settings.embed_model,
                 "dimension": dimension,
                 "generation": generation,
                 "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "chunks": len(chunks)})
    collection.modify(metadata=meta)

    invalidate_cache(settings, generation)
    log("index written", collection=settings.collection, chunks=len(chunks),
        pruned=len(stale), generation=generation,
        seconds=round(time.perf_counter() - started, 1))
    return int(collection.count())


def invalidate_cache(settings: IndexerSettings, generation: str) -> None:
    """Expire every cached answer (section 3.3).

    Redis being down fails the Job on purpose: Kubernetes retries it, and a
    delayed re-index is cheaper than every replica serving answers from a
    corpus you have already replaced.
    """
    import redis

    client = redis.Redis(host=settings.redis_host, port=settings.redis_port,
                         socket_timeout=5.0)
    client.set("rahbar:index:generation", generation)
    client.delete("rahbar:cache:entries")
    client.set("rahbar:cache:generation", generation)
    log("semantic cache invalidated", generation=generation)


# ---------------------------------------------------------------------------
# A2/A3: incremental sync. The old `store()` above is kept for A2 compatibility
# (full rebuilds, tests); ingestion Jobs call `sync()` instead.
# ---------------------------------------------------------------------------

def count_by_doc_id(collection: Any) -> dict[str, dict[str, int]]:
    """Proof that a deleted document left no orphans and only one version is active.

    Returns {doc_id: {"total": n, "active": n, "superseded": n}}.
    """
    rows = collection.get(include=["metadatas"])
    counts: dict[str, dict[str, int]] = {}
    for meta in rows.get("metadatas") or []:
        doc_id = str(meta.get("doc_id", "(no doc_id)")) if meta else "(no doc_id)"
        entry = counts.setdefault(doc_id, {"total": 0, "active": 0, "superseded": 0})
        entry["total"] += 1
        key = "superseded" if meta and meta.get("superseded") is True else "active"
        entry[key] += 1
    return counts


def print_counts(counts: dict[str, dict[str, int]]) -> None:
    print(f"{'DOC_ID':<40} {'TOTAL':>6} {'ACTIVE':>7} {'SUPERSEDED':>11}")
    for doc_id, entry in sorted(counts.items()):
        print(f"{doc_id[:40]:<40} {entry['total']:>6} {entry['active']:>7} "
              f"{entry['superseded']:>11}")
    print(f"TOTAL {sum(e['total'] for e in counts.values())} rows across "
          f"{len(counts)} document ids")


def _open_existing(client: Any, settings: IndexerSettings) -> Any:
    try:
        return client.get_collection(name=settings.collection)
    except Exception as exc:
        raise RuntimeError(f"collection {settings.collection} does not exist; "
                           "run a full ingestion first") from exc


def _flip_superseded(collection: Any, ids: list[str], value: bool) -> int:
    """Metadata-only update: no embeddings, no chunk text rewritten."""
    if not ids:
        return 0
    rows = collection.get(ids=ids, include=["metadatas"])
    metas = []
    for chunk_id, meta in zip(rows.get("ids", []), rows.get("metadatas") or []):
        updated = dict(meta or {})
        updated["superseded"] = value
        metas.append(updated)
    if metas:
        collection.update(ids=[row for row in rows.get("ids", [])],
                          metadatas=metas)
    return len(metas)


def sync(chunks: list[Chunk], vectors: list[list[float]], settings: IndexerSettings,
         *, deleted_ids: list[str] | None = None,
         superseded_ids: list[str] | None = None,
         replaced_doc_ids: set[str] | None = None,
         reused: int = 0) -> dict:
    """Apply an incremental plan. Only changed documents are embedded.

    Returns a counts dict that the indexer logs for the Part A screenshots.
    """
    deleted_ids = list(deleted_ids or [])
    superseded_ids = list(superseded_ids or [])
    replaced_doc_ids = set(replaced_doc_ids or set())
    if chunks and len(chunks) != len(vectors):
        raise RuntimeError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    started = time.perf_counter()
    client = get_client(settings)

    # A genuinely unchanged run does not even open a writer. It still prints
    # counts from the existing collection to prove zero work was done.
    if not chunks and not deleted_ids and not superseded_ids and not replaced_doc_ids:
        collection = _open_existing(client, settings)
        counts = count_by_doc_id(collection)
        result = {"embedded": 0, "reused": reused, "deleted": 0,
                  "superseded": 0, "total": sum(e["total"] for e in counts.values())}
        log("incremental sync - no writes", **result)
        print_counts(counts)
        return result

    dimension = len(vectors[0]) if vectors else None
    if dimension is None:
        collection = _open_existing(client, settings)
        dimension = int((collection.metadata or {}).get("dimension", 0)) or None
        if dimension is None:
            raise RuntimeError("cannot open collection without a known vector dimension")
    collection = _open_collection(client, settings, dimension)

    wanted = {chunk.id for chunk in chunks}
    for start in range(0, len(chunks), UPSERT_BATCH):
        group = chunks[start:start + UPSERT_BATCH]
        collection.upsert(
            ids=[chunk.id for chunk in group],
            documents=[chunk.text for chunk in group],
            embeddings=vectors[start:start + UPSERT_BATCH],
            metadatas=[chunk.metadata() for chunk in group],
        )

    stale = set(deleted_ids)
    if replaced_doc_ids:
        rows = collection.get(include=["metadatas"])
        for chunk_id, meta in zip(rows.get("ids", []), rows.get("metadatas") or []):
            if meta and str(meta.get("doc_id", "")) in replaced_doc_ids and chunk_id not in wanted:
                stale.add(chunk_id)
    stale -= wanted
    for start in range(0, len(stale), UPSERT_BATCH):
        batch = sorted(stale)[start:start + UPSERT_BATCH]
        if batch:
            collection.delete(ids=batch)

    flagged = _flip_superseded(collection, superseded_ids, True)
    generation = uuid.uuid4().hex
    meta = dict(collection.metadata or {})
    meta.pop("hnsw:space", None)
    meta.update({"embed_model": settings.embed_model, "dimension": dimension,
                 "generation": generation,
                 "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "chunks": int(collection.count())})
    collection.modify(metadata=meta)
    invalidate_cache(settings, generation)

    counts = count_by_doc_id(collection)
    result = {"embedded": len(chunks), "reused": reused, "deleted": len(stale),
              "superseded": flagged, "total": sum(e["total"] for e in counts.values())}
    log("incremental sync written", **result,
        seconds=round(time.perf_counter() - started, 1))
    print_counts(counts)
    return result


def apply_rollback(info: dict, settings: IndexerSettings) -> int:
    """Flip metadata flags for a restored version. No embedding, no delete."""
    client = get_client(settings)
    collection = _open_existing(client, settings)
    restored = _flip_superseded(collection, info["restore_chunk_ids"], False)
    superseded = _flip_superseded(collection, info["supersede_chunk_ids"], True)
    generation = uuid.uuid4().hex
    meta = dict(collection.metadata or {})
    meta.pop("hnsw:space", None)
    meta.update({"generation": generation,
                 "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    collection.modify(metadata=meta)
    invalidate_cache(settings, generation)
    log("rollback applied in vector store", doc_id=info["doc_id"],
        restored_version=info["restored_version"], restored_rows=restored,
        superseded_rows=superseded)
    return int(collection.count())
