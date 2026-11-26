"""Part B (Level 2 — Component evaluation): Retrieval recall@3, recall@5, MRR, nDCG@5 & noise."""

from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path
from typing import Any

import httpx


def _normalise(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def section_matches(claimed: object, actual: object) -> bool:
    left, right = _normalise(claimed), _normalise(actual)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def is_hit(relevant: list[dict], got: dict) -> bool:
    for want in relevant:
        cid = want.get("chunk_id")
        src = want.get("source")
        sec = want.get("section")
        if cid and got.get("chunk_id") == cid:
            return True
        if src and got.get("source") != src:
            continue
        if sec and not section_matches(sec, got.get("section")):
            continue
        if src or sec:
            return True
    return False


def recall_at_k(relevant: list[dict], retrieved: list[dict], k: int) -> float | None:
    if not relevant:
        return None
    hits = 0
    for want in relevant:
        if any(is_hit([want], got) for got in retrieved[:k]):
            hits += 1
    return hits / len(relevant)


def reciprocal_rank(relevant: list[dict], retrieved: list[dict], k: int = 5) -> float | None:
    if not relevant:
        return None
    for idx, got in enumerate(retrieved[:k], start=1):
        if is_hit(relevant, got):
            return 1.0 / idx
    return 0.0


def ndcg_at_k(relevant: list[dict], retrieved: list[dict], k: int = 5) -> float | None:
    if not relevant:
        return None
    dcg = 0.0
    matched_targets: set[int] = set()
    for idx, got in enumerate(retrieved[:k], start=1):
        rel = 0.0
        for w_idx, want in enumerate(relevant):
            if w_idx not in matched_targets and is_hit([want], got):
                rel = 1.0
                matched_targets.add(w_idx)
                break
        if rel > 0:
            dcg += rel / math.log2(idx + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return (dcg / idcg) if idcg > 0 else 0.0


def inject_noise(question: str, rng: random.Random) -> str:
    """Perturb a query with realistic typos and casing noise."""
    words = question.split()
    out: list[str] = []
    for w in words:
        if len(w) > 4 and rng.random() < 0.35:
            pos = rng.randint(1, len(w) - 2)
            # swap two adjacent characters
            w = w[:pos] + w[pos + 1] + w[pos] + w[pos + 2:]
        out.append(w.lower() if rng.random() < 0.5 else w)
    return " ".join(out)


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def score_retrieval(records: list[dict]) -> dict[str, Any]:
    """Compute recall@3, recall@5, MRR, and nDCG@5 from [{question, retrieved}]."""
    r3_vals: list[float] = []
    r5_vals: list[float] = []
    mrr_vals: list[float] = []
    ndcg_vals: list[float] = []
    per_item: list[dict] = []

    for rec in records:
        rel = rec.get("relevant") or []
        if not rel:
            continue
        ret = rec.get("retrieved") or []
        r3 = recall_at_k(rel, ret, 3)
        r5 = recall_at_k(rel, ret, 5)
        mrr = reciprocal_rank(rel, ret, 5)
        ndcg = ndcg_at_k(rel, ret, 5)
        assert r3 is not None and r5 is not None and mrr is not None and ndcg is not None
        r3_vals.append(r3)
        r5_vals.append(r5)
        mrr_vals.append(mrr)
        ndcg_vals.append(ndcg)
        per_item.append({
            "id": rec.get("id"),
            "difficulty": rec.get("difficulty", "normal"),
            "recall@3": round(r3, 4),
            "recall@5": round(r5, 4),
            "mrr": round(mrr, 4),
            "ndcg@5": round(ndcg, 4),
        })

    def _avg(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    return {
        "answerable_count": len(r5_vals),
        "recall@3": _avg(r3_vals),
        "recall@5": _avg(r5_vals),
        "mrr": _avg(mrr_vals),
        "ndcg@5": _avg(ndcg_vals),
        "per_item": per_item,
    }


def query_chroma_direct(
    question: str,
    chroma_host: str,
    chroma_port: int,
    collection_name: str,
    ollama_url: str,
    embed_model: str,
    top_k: int = 5,
) -> list[dict]:
    """Query Chroma directly (for evaluating candidate collections without redeploying API)."""
    import re as _re
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    older_re = _re.compile(r"\b(20(1[0-9]|2[0-5])|old(er)?|previous|superseded|2023)\b", _re.IGNORECASE)
    wants_older = bool(older_re.search(question))
    where = None if wants_older else {"superseded": False}

    with httpx.Client(timeout=60.0) as hc:
        er = hc.post(f"{ollama_url.rstrip('/')}/api/embed", json={"model": embed_model, "input": question})
        er.raise_for_status()
        eb = er.json()
        vec = (eb.get("embeddings") or [eb["embedding"]])[0]

    client = chromadb.HttpClient(
        host=chroma_host, port=chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    for attempt in range(3):
        try:
            col = client.get_collection(name=collection_name)
            count = col.count()
            n_res = min(max(top_k * 4, top_k), count) if count else top_k
            res = col.query(query_embeddings=[vec], n_results=n_res, where=where,
                            include=["documents", "metadatas", "distances"])
            break
        except Exception:
            if attempt == 2:
                raise
            import time as _time
            _time.sleep(1.0)
    out: list[dict] = []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for cid, txt, meta, dist in zip(ids, docs, metas, dists):
        meta = meta or {}
        if not wants_older and meta.get("superseded") is True:
            continue
        out.append({
            "chunk_id": cid,
            "source": str(meta.get("source", "unknown")),
            "section": str(meta.get("section", "")),
            "page": meta.get("page"),
            "score": round(1.0 - float(dist), 6),
            "text": txt or "",
        })
        if len(out) >= top_k:
            break
    return out
