"""The real retriever (sections 3.2 and 4.4).

Two rules this file exists to keep:

1. The query is embedded with `settings.embed_model` - the same ConfigMap key
   the indexer read. Different models produce vectors that are not comparable,
   and the failure is silent.
2. Superseded documents are filtered out of the results, because a repealed
   clause retrieved faithfully is still a wrong answer (failure mode E). The
   filter lifts only when the question itself asks about an older version.

Chroma returns cosine *distance*; Passage.score expects similarity, so
similarity = 1 - distance.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..config import Settings
from ..schemas import Passage

log = logging.getLogger("rahbar.retrieval.chroma")

# Questions that explicitly ask about an older version of a policy retrieve the
# superseded documents too; everything else sees only the current rules.
_OLDER_VERSION = re.compile(
    r"\b(20(1[0-9]|2[0-5])"
    r"|old(er)? (version|rules|policy|policies|handbook|regulations)"
    r"|previous (version|rules|policy|handbook)"
    r"|superseded|supersession|repealed|withdrawn"
    r"|before (the )?(202[0-6])"
    r"|original (version|policy|rules))"
    r"\b",
    re.IGNORECASE,
)

OVERFETCH = 4            # ask Chroma for more than k, then filter client-side
EMBED_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class ChromaRetriever:
    name = "chroma"

    def __init__(self, settings: Settings) -> None:
        import chromadb  # the thin client, from chromadb-client in requirements.txt
        from chromadb.config import Settings as ChromaSettings

        self.settings = settings
        self.client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port,
                                          settings=ChromaSettings(anonymized_telemetry=False))

    # -- the collection, fetched lazily so a pod can start before the index exists
    def collection(self) -> Any:
        return self.client.get_collection(name=self.settings.collection)

    def embed(self, question: str) -> list[float]:
        """Embed the question with the pinned model - the indexer's model."""
        response = httpx.post(
            f"{self.settings.ollama_url.rstrip('/')}/api/embed",
            json={"model": self.settings.embed_model, "input": question},
            timeout=EMBED_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        vectors = body.get("embeddings") or [body["embedding"]]
        return [float(x) for x in vectors[0]]

    def retrieve(self, question: str, k: int) -> list[Passage]:
        vector = self.embed(question)
        return self.retrieve_vector(vector, question, k)

    def retrieve_vector(self, vector: list[float], question: str, k: int) -> list[Passage]:
        """Top-k by cosine similarity, excluding superseded documents unless
        the question explicitly asks about an older version."""
        k = max(1, k)
        wants_older = bool(_OLDER_VERSION.search(question))
        where = None if wants_older else {"superseded": False}
        collection = self.collection()
        count = collection.count()
        n_results = min(max(k * OVERFETCH, k), count) if count else k
        if not count:
            log.warning("collection is empty - the indexer Job has not run yet",
                        extra={"collection": self.settings.collection})
            return []

        results = collection.query(
            query_embeddings=[vector],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        passages: list[Passage] = []
        ids = (results.get("ids") or [[]])[0]
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        for chunk_id, text, meta, distance in zip(ids, docs, metas, dists):
            meta = meta or {}
            if not wants_older and meta.get("superseded") is True:
                continue                        # client-side double-check of the filter
            passages.append(Passage(
                chunk_id=chunk_id,
                text=text or "",
                source=str(meta.get("source", "unknown")),
                section=str(meta.get("section", "(no section)")),
                page=int(meta["page"]) if meta.get("page") is not None else None,
                doc_version=meta.get("doc_version"),
                superseded=bool(meta.get("superseded", False)),
                score=round(1.0 - float(distance), 6),   # cosine distance -> similarity
            ))
            if len(passages) >= k:
                break
        return passages
