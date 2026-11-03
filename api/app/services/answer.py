"""Turns a question into an AskResponse. The query pipeline of section 3.2:

1. receive and validate the question (the route's Pydantic model does it),
2. cache check - skipped for follow-ups,
3. embed the question with the pinned model,
4. retrieve top-k with the superseded filter,
5. augment (the provider builds instructions -> evidence -> question),
6. generate at low temperature, schema-constrained,
7. validate, retry once, fall back - the provider does that internally,
8. ground the citations, then store the turn if it is safe to cache.

Indexing and querying use the same embedding model; this file never writes to
Chroma. Each step lives in its own module so it can be unit-tested alone.
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..providers.llm.common import is_fallback
from ..retrieval.base import Retriever
from ..routes.metrics import (ABSTENTIONS, CACHE, CITATIONS_PRUNED, FALLBACKS,
                              GENERATION, RETRIEVAL)
from ..schemas import AskResponse, Passage, RetrievedRef
from .cache import SemanticCache, is_followup, should_cache
from .grounding import abstention, below_floor, ground

log = logging.getLogger("rahbar.answer")


def _refs(passages: list[Passage]) -> list[RetrievedRef]:
    return [
        RetrievedRef(chunk_id=p.chunk_id, source=p.source, section=p.section,
                     page=p.page, score=p.score)
        for p in passages
    ]


def answer_question(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    cache: SemanticCache | None = None,
) -> AskResponse:
    request_id = request_id_var.get()
    followup = is_followup(question)

    # 2. Cache check. Follow-ups are never looked up and never stored: they
    # only make sense in their conversation (section 3.3).
    vector: list[float] | None = None
    if cache is not None:
        if followup:
            CACHE.labels(result="bypass").inc()
        else:
            embed = getattr(retriever, "embed", None)
            if callable(embed):
                try:
                    vector = embed(question)
                except Exception as exc:
                    log.warning("query embedding failed", extra={"error": str(exc)})
            try:
                hit = cache.lookup(question, vector) if vector else None
            except Exception as exc:
                log.warning("cache lookup failed", extra={"error": str(exc)})
                hit = None
            CACHE.labels(result="hit" if hit else "miss").inc()
            if hit:
                log.info("cache hit", extra={"top_k": settings.top_k,
                                             "chunk_ids": [r.chunk_id for r in hit.retrieved]})
                return hit.model_copy(update={"request_id": request_id, "cached": True})

    # 3./4. Embed (inside the retriever, same pinned model) and retrieve with
    # the superseded filter.
    retrieval_started = time.perf_counter()
    try:
        retrieve_vector = getattr(retriever, "retrieve_vector", None)
        if vector is not None and callable(retrieve_vector):
            passages = retrieve_vector(vector, question, settings.top_k)
        else:
            passages = retriever.retrieve(question, settings.top_k)
    except HTTPException:
        raise
    except Exception as exc:
        log.error("retrieval failed", extra={"error": str(exc),
                                             "retriever": retriever.name})
        raise HTTPException(status_code=503,
                            detail="vector index unavailable - is the indexer Job running?"
                            ) from exc
    retrieval_seconds = time.perf_counter() - retrieval_started
    RETRIEVAL.observe(retrieval_seconds)

    # 3.4 relevance floor: nothing close enough means an honest abstention,
    # decided here so a question the corpus cannot answer never reaches the
    # model and can never come back confident.
    if below_floor(passages, settings.relevance_floor):
        ABSTENTIONS.inc()
        answer = abstention()
        response = AskResponse(
            request_id=request_id, answer=answer.answer, citations=answer.citations,
            answered_from_context=False, confidence=answer.confidence,
            retrieved=_refs(passages), provider=provider.name,
        )
        log.info("answered", extra={
            "retriever": retriever.name, "provider": provider.name,
            "top_k": settings.top_k, "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "answered_from_context": False, "abstention": "relevance_floor",
            "retrieval_ms": round(retrieval_seconds * 1000, 1),
        })
        return response

    # 5./6./7. Generate: the provider builds the augmented prompt, requests
    # schema-constrained output, validates, retries once and falls back.
    started = time.perf_counter()
    answer = provider.generate(question, passages)
    generation_seconds = time.perf_counter() - started
    GENERATION.observe(generation_seconds)

    # Citation check: every citation must match a retrieved passage (3.4).
    answer, pruned = ground(answer, passages)
    if pruned:
        CITATIONS_PRUNED.inc(pruned)
    fallback_used = is_fallback(answer)
    if fallback_used:
        FALLBACKS.inc()
    if not answer.answered_from_context:
        ABSTENTIONS.inc()

    response = AskResponse(
        request_id=request_id,
        answer=answer.answer,
        citations=answer.citations,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=_refs(passages),
        provider=provider.name,
    )

    # 8. Store the turn - only when it is safe (section 3.3).
    if cache is not None and vector is not None and \
            should_cache(question, answer, fallback_used):
        try:
            cache.store(question, vector, response)
        except Exception as exc:
            log.warning("cache store failed", extra={"error": str(exc)})

    log.info("answered", extra={
        "retriever": retriever.name, "provider": provider.name,
        "top_k": settings.top_k,
        "chunk_ids": [p.chunk_id for p in passages],
        "scores": [round(p.score, 4) for p in passages],
        "answered_from_context": answer.answered_from_context,
        "confidence": answer.confidence,
        "abstention": not answer.answered_from_context,
        "citations_pruned": pruned,
        "fallback": fallback_used,
        "retrieval_ms": round(retrieval_seconds * 1000, 1),
        "generation_ms": round(generation_seconds * 1000, 1),
        "cache": "bypass" if followup else "miss",
    })
    return response
