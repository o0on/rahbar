"""Liveness and readiness probes (sections 4.3 and 4.4).

Liveness answers: is the Python process running and responsive? (Must NEVER touch dependencies)
Readiness answers: can this pod accept user traffic right now?
Checks Chroma reachable, collection non-empty, Redis reachable, and EMBED_MODEL pin match.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response

log = logging.getLogger("rahbar.api.health")
router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Is the process alive? Must never touch Chroma, Redis or the LLM."""
    return {"status": "alive"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, Any]:
    """Can this pod take traffic now?

    Returns 200 only if:
    1. Chroma is reachable
    2. The collection exists and is NON-EMPTY (count() > 0)
    3. Redis is reachable
    4. The collection's embedding model metadata matches the runtime EMBED_MODEL pin
    Otherwise returns 503 naming the failed dependency.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        response.status_code = 503
        return {"status": "not_ready", "error": "settings_not_initialized"}

    checks: dict[str, Any] = {}
    is_ready = True

    # In pure stub mode (for offline unit tests), skip external network probes
    if settings.retriever == "stub":
        checks["chroma"] = "stub_ready"
        checks["collection_count"] = 3
        checks["embedding_pin"] = "matched"
        checks["redis"] = "stub_ready"
        return {"status": "ready", "checks": checks}

    # 1. Chroma & Collection Check
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        chroma_client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        chroma_client.heartbeat()

        # Check collection exists
        collection = chroma_client.get_collection(name=settings.collection)
        chunk_count = collection.count()

        if chunk_count <= 0:
            is_ready = False
            checks["chroma"] = "empty_collection"
            checks["error"] = "Collection exists but is empty (indexer Job must run first)"
            log.warning("readiness check failed: collection %s has 0 items (count=%d)",
                        settings.collection, chunk_count)
        else:
            checks["chroma"] = "ok"
            checks["collection_count"] = chunk_count

            # Section 4.4: Enforce embedding model pin guardrail in code
            meta = collection.metadata or {}
            indexed_model = meta.get("embed_model") or meta.get("embedding_model")
            if indexed_model and indexed_model != settings.embed_model:
                is_ready = False
                checks["embedding_pin"] = "mismatch"
                checks["error"] = (
                    f"embed_model mismatch: collection built with '{indexed_model}' "
                    f"does not match runtime pin '{settings.embed_model}'"
                )
                log.error(
                    "EMBED_MODEL mismatch guardrail triggered: "
                    "collection model '%s' != runtime pin '%s'",
                    indexed_model, settings.embed_model
                )
            else:
                checks["embedding_pin"] = "ok"

    except Exception as exc:
        is_ready = False
        checks["chroma"] = f"unreachable: {exc}"
        log.warning("readiness check failed: Chroma unreachable: %s", exc)

    # 2. Redis Cache Check
    try:
        import redis

        redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            socket_timeout=1.0,
        )
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        is_ready = False
        checks["redis"] = f"unreachable: {exc}"
        log.warning("readiness check failed: Redis unreachable: %s", exc)

    if not is_ready:
        response.status_code = 503
        return {"status": "not_ready", "checks": checks}

    return {"status": "ready", "checks": checks}
