#!/usr/bin/env python3
"""Measure three (size, overlap) chunking configs against the golden set (section 3.1).

    python eval/tune_chunking.py --ollama http://127.0.0.1:11434

Extraction (OCR included) is the expensive part, so cleaned documents are cached
in the system temp dir and only re-extracted when a source file changes. Each
config then chunks the corpus, embeds every chunk and every golden question with
the pinned model, and scores recall exactly the way eval/run_eval.py does -
including the retriever's superseded filter, which is imported rather than
re-implemented. Storage amplification is stored tokens divided by cleaned-corpus
tokens; overlap is what inflates it.

Prints a markdown table and writes eval/chunk-tuning.json for docs/PIPELINE.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import yaml

from run_eval import recall_at_k

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = (Path(os.environ.get("TEMP", tempfile.gettempdir())) / "opencode"
         / "rahbar-tune-docs.json")

# The three measured configurations (section 3.1): small / current / large.
# Overlap scales with size so the duplication it causes stays comparable.
CONFIGS: list[tuple[int, int]] = [(200, 40), (400, 50), (800, 100)]
EMBED_BATCH = 32
TOP_K = 5


def _pipeline() -> dict[str, Any]:
    """Import the indexer's stages; `indexer/` sits on the path only here."""
    if str(ROOT / "indexer") not in sys.path:
        sys.path.insert(0, str(ROOT / "indexer"))
    from pipeline.chunk import chunk, tokens
    from pipeline.clean import clean
    from pipeline.extract import extract
    from pipeline.run import discover
    return {"discover": discover, "extract": extract, "clean": clean,
            "chunk": chunk, "tokens": tokens}


def _older_version_rule() -> Any:
    """The retriever's own superseded-filter rule, imported, not copied."""
    if str(ROOT / "api") not in sys.path:
        sys.path.insert(0, str(ROOT / "api"))
    from app.retrieval.chroma import _OLDER_VERSION
    return _OLDER_VERSION


def load_documents(corpus_dir: Path) -> list[Any]:
    """extract + clean every document once, cached until a source file changes."""
    stages = _pipeline()
    discovered = stages["discover"](str(corpus_dir))
    if not discovered:
        sys.exit(f"no documents found in {corpus_dir}")
    stamp = [{"file": p.name, "mtime": p.stat().st_mtime, "size": p.stat().st_size}
             for p, _, _ in discovered]
    if CACHE.exists():
        payload = json.loads(CACHE.read_text(encoding="utf-8"))
        if payload.get("stamp") == stamp:
            print(f"cleaned corpus loaded from cache ({len(payload['documents'])} documents)")
            from pipeline.models import Document, Page
            return [Document(**{**d, "pages": [Page(**p) for p in d["pages"]]})
                    for d in payload["documents"]]

    documents = []
    for path, doc_version, superseded in discovered:
        doc = stages["clean"](stages["extract"](str(path), doc_version, superseded))
        documents.append(doc)
        print(f"extracted {path.name}: {len(doc.pages)} pages")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"stamp": stamp,
                                 "documents": [asdict(d) for d in documents]}),
                     encoding="utf-8")
    return documents


def embed_all(texts: list[str], url: str, model: str, label: str) -> list[list[float]]:
    """Embed in batches through Ollama's /api/embed, retrying transient failures."""
    out: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]
        last: Exception | None = None
        for attempt in range(5):
            try:
                response = httpx.post(
                    f"{url.rstrip('/')}/api/embed",
                    json={"model": model, "input": batch},
                    timeout=180.0,
                )
                response.raise_for_status()
                body = response.json()
                vectors = body.get("embeddings") or [body["embedding"]]
                if len(vectors) != len(batch):
                    raise RuntimeError(f"embedded {len(vectors)} of {len(batch)} texts")
                out.extend([[float(x) for x in vector] for vector in vectors])
                last = None
                break
            except Exception as exc:             # retry anything transient
                last = exc
                time.sleep(2 ** attempt)
        if last is not None:
            raise RuntimeError(f"{label}: embedding failed after retries: {last}")
        if start % (EMBED_BATCH * 10) == 0:
            print(f"  {label}: embedded {start + len(batch)}/{len(texts)}")
    return out


def _normalise_rows(vectors: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def score_config(chunks: list[Any], chunk_vectors: list[list[float]],
                 questions: list[dict], question_vectors: list[list[float]],
                 older_rule: Any) -> dict[str, Any]:
    """recall@1/3/5 over the golden set, with the retriever's superseded filter."""
    matrix = _normalise_rows(chunk_vectors)
    superseded = np.array([bool(c.superseded) for c in chunks])
    per_question: dict[str, float | None] = {}
    hits3 = hits5 = hits1 = 0
    first_ranks: list[int] = []
    scored = 0
    for question, vector in zip(questions, question_vectors):
        relevant = question.get("relevant") or []
        if not relevant:
            continue                            # must-abstain: no recall to compute
        wants_older = bool(older_rule.search(question["question"]))
        similarities = matrix @ np.asarray(vector, dtype=np.float32)
        if not wants_older:
            similarities = np.where(superseded, -np.inf, similarities)
        order = np.argsort(-similarities)
        retrieved = [{"chunk_id": chunks[j].id, "source": chunks[j].source,
                      "section": chunks[j].section} for j in order[:TOP_K]]
        recall1 = recall_at_k(relevant, retrieved, 1)
        recall3 = recall_at_k(relevant, retrieved, 3)
        recall5 = recall_at_k(relevant, retrieved, TOP_K)
        per_question[question["id"]] = recall5
        scored += 1
        hits1 += recall1 or 0.0
        hits3 += recall3 or 0.0
        hits5 += recall5 or 0.0
        for rank, passage in enumerate(retrieved, start=1):
            if any(passage["source"] == want.get("source")
                   and _section_matches(want.get("section"), passage["section"])
                   for want in relevant if want.get("source")):
                first_ranks.append(rank)
                break
    return {
        "questions_scored": scored,
        "recall_at_1": round(hits1 / scored, 4) if scored else None,
        "recall_at_3": round(hits3 / scored, 4) if scored else None,
        "recall_at_5": round(hits5 / scored, 4) if scored else None,
        "mean_first_relevant_rank": (round(sum(first_ranks) / len(first_ranks), 2)
                                     if first_ranks else None),
        "per_question_recall_at_5": per_question,
    }


def _section_matches(claimed: object, actual: object) -> bool:
    """run_eval's section rule, reused for the first-relevant-rank statistic."""
    left = re.sub(r"[^a-z0-9]+", " ", str(claimed or "").lower()).strip()
    right = re.sub(r"[^a-z0-9]+", " ", str(actual or "").lower()).strip()
    if not left or not right:
        return False
    return left == right or left in right or right in left


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure chunking configs against the golden set.")
    parser.add_argument("--ollama", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--model", default=os.environ.get("EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus" / "raw")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.yaml")
    parser.add_argument("--out", type=Path, default=HERE / "chunk-tuning.json")
    args = parser.parse_args()

    questions = (yaml.safe_load(args.golden.read_text(encoding="utf-8")) or {}).get("questions") or []
    older_rule = _older_version_rule()
    documents = load_documents(args.corpus)

    question_vectors = embed_all([q["question"] for q in questions],
                                 args.ollama, args.model, "questions")
    corpus_tokens = sum(len(_pipeline()["tokens"](page.text))
                        for doc in documents for page in doc.pages)
    print(f"cleaned corpus: {len(documents)} documents, {corpus_tokens} tokens")

    rows: list[dict[str, Any]] = []
    for size, overlap in CONFIGS:
        started = time.perf_counter()
        stages = _pipeline()
        chunks = [c for doc in documents for c in stages["chunk"](doc, size, overlap)]
        vectors = embed_all([c.text for c in chunks], args.ollama, args.model,
                            f"chunks {size}/{overlap}")
        row: dict[str, Any] = {"size": size, "overlap": overlap, "chunks": len(chunks)}
        row.update(score_config(chunks, vectors, questions, question_vectors, older_rule))
        stored = sum(len(stages["tokens"](c.text)) for c in chunks)
        row.update({
            "stored_tokens": stored,
            "corpus_tokens": corpus_tokens,
            "storage_amplification": round(stored / corpus_tokens, 4),
            "mean_chunk_tokens": round(stored / len(chunks), 1),
            "max_chunk_tokens": max(len(stages["tokens"](c.text)) for c in chunks),
            "seconds": round(time.perf_counter() - started, 1),
        })
        rows.append(row)
        print(f"size={size} overlap={overlap}: {row['chunks']} chunks, "
              f"recall@5={row['recall_at_5']}, amplification={row['storage_amplification']}")

    # Pick the best recall@5; ties go to the cheaper index (lower amplification).
    best = max(rows, key=lambda r: (r["recall_at_5"] or 0.0,
                                    -(r["storage_amplification"] or 0.0)))
    payload = {"model": args.model, "configs": rows,
               "chosen": {"size": best["size"], "overlap": best["overlap"]}}
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n| config (size, overlap) | chunks | mean tokens | recall@1 | recall@3 | "
          "recall@5 | storage amplification |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print(f"| {row['size']} / {row['overlap']} | {row['chunks']} | "
              f"{row['mean_chunk_tokens']} | {row['recall_at_1']} | {row['recall_at_3']} | "
              f"**{row['recall_at_5']}** | {row['storage_amplification']}x |")
    print(f"\nchosen: size={best['size']} overlap={best['overlap']} "
          f"(recall@5={best['recall_at_5']})")
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
