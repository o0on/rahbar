"""Stage 4: Embed. One vector per chunk, from the pinned model only.

The pin (section 4.4) is settings.embed_model, read from the same ConfigMap
key the API reads. Nothing else is allowed to choose a model here: two
different models produce vectors that are not comparable, and the failure is
silent - retrieval returns nonsense and no error appears anywhere.

Calls are batched (one request holds many chunks): one request per chunk is
slow enough to make a 40-page index take an hour.
"""

from __future__ import annotations

import time

import httpx

from .config import IndexerSettings
from .log import log

BATCH_SIZE = 32
ATTEMPTS = 3
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class _NoBatchEndpoint(Exception):
    """This Ollama exposes no batch /api/embed; fall back to the legacy shape."""


def embed(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Embed every text with settings.embed_model and nothing else."""
    if not texts:
        return []
    vectors: list[list[float]] = []
    started = time.perf_counter()
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        vectors.extend(_embed_batch(batch, settings))
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        raise RuntimeError("embedding service returned vectors of different dimensions")
    log("embedded", model=settings.embed_model, vectors=len(vectors), dimension=dimension,
        seconds=round(time.perf_counter() - started, 1))
    return vectors


def _embed_batch(batch: list[str], settings: IndexerSettings) -> list[list[float]]:
    cleaned = [text if text.strip() else " " for text in batch]
    last_error: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return _post(settings, "/api/embed", {"model": settings.embed_model,
                                                  "input": cleaned})
        except _NoBatchEndpoint:               # legacy server: per-chunk endpoint
            return _embed_legacy(cleaned, settings)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < ATTEMPTS:
                wait = 2 ** (attempt - 1)
                log("embedding retry", attempt=attempt, wait_seconds=wait, error=str(exc))
                time.sleep(wait)
    raise RuntimeError(f"embedding failed after {ATTEMPTS} attempts: {last_error}")


def _post(settings: IndexerSettings, path: str, payload: dict) -> list[list[float]]:
    response = httpx.post(f"{settings.ollama_url.rstrip('/')}{path}", json=payload,
                          timeout=TIMEOUT)
    if response.status_code in (404, 405):
        raise _NoBatchEndpoint(path)           # this Ollama has no batch endpoint
    response.raise_for_status()
    body = response.json()
    if "embeddings" in body:
        vectors = body["embeddings"]
    elif "embedding" in body:                  # older single-input shape
        vectors = [body["embedding"]]
    else:
        raise KeyError(f"no embedding in response: {sorted(body)}")
    expected = len(payload.get("input", []))
    if expected and len(vectors) != expected:
        raise ValueError(f"expected {expected} vectors, got {len(vectors)}")
    return [[float(x) for x in vector] for vector in vectors]


def _embed_legacy(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Ollama without /api/embed: one POST /api/embeddings per chunk."""
    log("using legacy embedding endpoint", model=settings.embed_model, chunks=len(texts))
    vectors: list[list[float]] = []
    for text in texts:
        response = httpx.post(
            f"{settings.ollama_url.rstrip('/')}/api/embeddings",
            json={"model": settings.embed_model, "prompt": text},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        vectors.append([float(x) for x in response.json()["embedding"]])
    return vectors
