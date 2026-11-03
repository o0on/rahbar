"""The semantic cache (section 3.3), stored in Redis (ADR 0002).

A hit is cosine similarity at or above `settings.cache_threshold` between the
incoming question's embedding and a stored question's embedding - both come
from the pinned model, so the vectors are comparable.

Never cached, because a cached mistake is served to everyone:
* follow-up questions (they only make sense in their conversation),
* abstentions and out-of-scope answers (answered_from_context: false),
* anything with no citation (nothing grounded to reuse),
* anything that failed validation and came back from the fallback.

Invalidated on re-index: the indexer Job bumps `rahbar:index:generation` and
wipes the entries; this module also compares generations on lookup, so even a
Job that died between the two writes cannot leave stale answers serving.

Every rule that does not need Redis is a module-level function, so the rules
are unit-testable without a server.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx

from ..config import Settings
from ..schemas import AskResponse, Answer

log = logging.getLogger("rahbar.cache")

ENTRIES_KEY = "rahbar:cache:entries"
CACHE_GENERATION_KEY = "rahbar:cache:generation"
INDEX_GENERATION_KEY = "rahbar:index:generation"
ENTRY_TTL_SECONDS = 7 * 24 * 3600
EMBED_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

# A follow-up leans on the previous turn: "what about fees?", "and the exam
# dates?". It names no topic of its own, so it is neither lookup-able nor
# cacheable on its own.
_FOLLOWUP = re.compile(
    r"^\s*(what about|how about|what else|why|then|so|and\b|or\b|it\b|they\b|"
    r"this\b|that\b|these\b|those\b|the (same|other|previous) (one|question|rule))",
    re.IGNORECASE,
)


def is_followup(question: str) -> bool:
    """True when the question only makes sense relative to the previous turn."""
    return bool(_FOLLOWUP.match(question.strip()))


def should_cache(question: str, answer: Answer, fallback_used: bool) -> bool:
    """The never-cache rules from section 3.3, in one place."""
    if is_followup(question):
        return False
    if fallback_used:
        return False
    if not answer.answered_from_context:
        return False
    if not answer.citations:
        return False
    return True


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = (sum(a * a for a in left) ** 0.5) * (sum(b * b for b in right) ** 0.5)
    return dot / norm if norm else 0.0


def _key(question: str) -> str:
    normalised = " ".join(question.lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]


class SemanticCache:
    """Redis-backed lookup/store for grounded answers."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client          # injectable for tests

    # -- connection -------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis(host=self.settings.redis_host,
                                       port=self.settings.redis_port,
                                       socket_timeout=2.0, decode_responses=True)
        return self._client

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    # -- generation sync --------------------------------------------------
    def sync_generation(self) -> None:
        """Drop every entry if the index changed underneath us (section 3.3)."""
        try:
            index_generation = self.client.get(INDEX_GENERATION_KEY)
            cache_generation = self.client.get(CACHE_GENERATION_KEY)
            if index_generation and index_generation != cache_generation:
                self.client.delete(ENTRIES_KEY)
                self.client.set(CACHE_GENERATION_KEY, index_generation)
                log.info("index generation changed - cache flushed",
                         extra={"generation": index_generation})
        except Exception as exc:
            log.warning("cache generation check failed", extra={"error": str(exc)})

    def invalidate(self) -> None:
        """Explicit flush (used by tests and available to an admin path)."""
        try:
            self.client.delete(ENTRIES_KEY)
            log.info("cache invalidated")
        except Exception as exc:
            log.warning("cache invalidate failed", extra={"error": str(exc)})

    # -- lookup / store ---------------------------------------------------
    def lookup(self, question: str, vector: list[float]) -> AskResponse | None:
        """The cached answer whose question is closest to this one, or None."""
        try:
            self.sync_generation()
            raw = self.client.hgetall(ENTRIES_KEY)
        except Exception as exc:
            log.warning("cache lookup failed", extra={"error": str(exc)})
            return None
        if not raw:
            return None
        best_score, best_body = -1.0, None
        for body in raw.values():
            try:
                entry = json.loads(body)
            except ValueError:
                continue
            score = cosine(vector, entry.get("vector") or [])
            if score > best_score:
                best_score, best_body = score, entry
        threshold = self.settings.cache_threshold
        if best_body is None or best_score < threshold:
            return None
        try:
            # store() deliberately drops the old request_id; the caller re-stamps
            # it, so the round trip only needs the rest of the contract.
            payload = dict(best_body["response"])
            payload.setdefault("request_id", "")
            response = AskResponse(**payload)
        except (TypeError, ValueError, KeyError) as exc:
            log.warning("cache entry is malformed - dropping it", extra={"error": str(exc)})
            self.client.hdel(ENTRIES_KEY, _key(best_body.get("question", "")))
            return None
        log.info("cache hit", extra={"similarity": round(best_score, 4),
                                     "threshold": threshold})
        return response.model_copy(update={"cached": True})

    def store(self, question: str, vector: list[float], response: AskResponse) -> None:
        entry = {"question": question, "vector": vector, "stored_at": int(time.time()),
                 "response": response.model_dump(exclude={"request_id", "cached"})}
        try:
            self.client.hset(ENTRIES_KEY, _key(question), json.dumps(entry))
            self.client.expire(ENTRIES_KEY, ENTRY_TTL_SECONDS)
        except Exception as exc:
            log.warning("cache store failed", extra={"error": str(exc)})

    # -- embeddings for the lookup ---------------------------------------
    def embed(self, question: str) -> list[float]:
        response = httpx.post(
            f"{self.settings.ollama_url.rstrip('/')}/api/embed",
            json={"model": self.settings.embed_model, "input": question},
            timeout=EMBED_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        vectors = body.get("embeddings") or [body["embedding"]]
        return [float(x) for x in vectors[0]]
