#!/usr/bin/env python3
"""Part E3: Measure Batch B corpus & query drift against Batch A baseline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from component_retrieval import load_jsonl, query_chroma_direct, score_retrieval  # noqa: E402


def build_batch_b(chroma_host: str, chroma_port: int, ollama_url: str, embed_model: str) -> int:
    py = "\n".join([
        "import os, chromadb",
        "from pipeline.config import IndexerSettings",
        "from pipeline.run import discover",
        "from pipeline.extract import extract",
        "from pipeline.clean import clean",
        "from pipeline.chunk import chunk",
        "from pipeline.embed import embed",
        "from pipeline.verify import load_sources, prepare, verify",
        "s = IndexerSettings(embed_model=os.environ['EMBED_MODEL'], chroma_host=os.environ['CHROMA_HOST'], chroma_port=int(os.environ['CHROMA_PORT']), collection='rahbar-batch-b', ollama_url=os.environ['OLLAMA_URL'], corpus_dir='/corpus/batch_b', chunk_size=400, chunk_overlap=50, redis_host='127.0.0.1', redis_port=6379)",
        "sources = load_sources('/corpus/batch_b')",
        "prep = prepare(sources)",
        "rep = verify(prep, s)",
        "keep_files = {d['source'] for d in rep['documents'] if d['action'] == 'index'}",
        "chunks = []",
        "for p, dv, sup in discover('/corpus/batch_b'):",
        "    if p.name in keep_files:",
        "        chunks.extend(chunk(clean(extract(str(p), dv, sup)), size=400, overlap=50))",
        "vecs = embed([c.text for c in chunks], s)",
        "cl = chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port)",
        "try:",
        "    cl.delete_collection('rahbar-batch-b')",
        "except Exception:",
        "    pass",
        "col = cl.create_collection(name='rahbar-batch-b', metadata={'hnsw:space':'cosine','embed_model':s.embed_model,'dimension':len(vecs[0])})",
        "for i in range(0, len(chunks), 100):",
        "    col.upsert(ids=[c.id for c in chunks[i:i+100]], documents=[c.text for c in chunks[i:i+100]], embeddings=vecs[i:i+100], metadatas=[c.metadata() for c in chunks[i:i+100]])",
        "print('BUILT_BATCH_B', col.count())",
    ])
    out = subprocess.check_output([
        "docker", "run", "--rm", "--network", "host",
        "-e", f"CHROMA_HOST={chroma_host}",
        "-e", f"CHROMA_PORT={chroma_port}",
        "-e", f"OLLAMA_URL={ollama_url}",
        "-e", f"EMBED_MODEL={embed_model}",
        "rahbar-indexer:latest", "python", "-c", py,
    ]).decode()
    print(out.strip().splitlines()[-1])
    return int(out.strip().splitlines()[-1].split()[-1])


def eval_collection_drift(
    collection: str,
    golden_rows: list[dict],
    live_in_rows: list[dict],
    chroma_host: str,
    chroma_port: int,
    ollama_url: str,
    embed_model: str,
) -> dict:
    ret_rows = []
    for q in golden_rows:
        if not q.get("relevant"):
            continue
        got = query_chroma_direct(
            q["question"], chroma_host=chroma_host, chroma_port=chroma_port,
            collection_name=collection, ollama_url=ollama_url, embed_model=embed_model, top_k=5
        )
        ret_rows.append({**q, "retrieved": got})
    comp = score_retrieval(ret_rows)

    top1_scores = []
    for lq in live_in_rows:
        got = query_chroma_direct(
            lq["question"], chroma_host=chroma_host, chroma_port=chroma_port,
            collection_name=collection, ollama_url=ollama_url, embed_model=embed_model, top_k=1
        )
        top1_scores.append(got[0]["score"] if got else 0.0)
    mean_top1 = round(sum(top1_scores) / len(top1_scores), 4) if top1_scores else 0.0
    above_floor = sum(1 for s in top1_scores if s >= 0.65)

    return {
        "collection": collection,
        "recall@5": comp["recall@5"],
        "mrr": comp["mrr"],
        "ndcg@5": comp["ndcg@5"],
        "live_in_domain_mean_top1_similarity": mean_top1,
        "live_in_domain_above_floor_065": f"{above_floor}/{len(top1_scores)}",
    }


def main() -> int:
    chroma_host = os.environ.get("CHROMA_HOST", "127.0.0.1")
    chroma_port = int(os.environ.get("CHROMA_PORT", "8001"))
    ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    embed_model = os.environ.get("EMBED_MODEL", "nomic-embed-text")

    build_batch_b(chroma_host, chroma_port, ollama_url, embed_model)
    golden_25 = load_jsonl(HERE / "golden.jsonl")[:25]
    live_in = [r for r in load_jsonl(HERE / "live_queries.jsonl") if r.get("domain") == "in"]

    res_a = eval_collection_drift("rahbar", golden_25, live_in, chroma_host, chroma_port, ollama_url, embed_model)
    res_b = eval_collection_drift("rahbar-batch-b", golden_25, live_in, chroma_host, chroma_port, ollama_url, embed_model)

    out = {
        "batch_a": res_a,
        "batch_b": res_b,
        "delta": {
            "recall@5": round(res_b["recall@5"] - res_a["recall@5"], 4),
            "mrr": round(res_b["mrr"] - res_a["mrr"], 4),
            "live_mean_top1_similarity": round(res_b["live_in_domain_mean_top1_similarity"] - res_a["live_in_domain_mean_top1_similarity"], 4),
        },
        "cause": (
            "Batch B replaced academic-rules-handbook-jun-2026.pdf with nov-2026.md (changing withdrawal "
            "deadline and appeal days) and introduced 6 new student-service/IT/sports/refund policies while "
            "omitting Batch A fee-structure and grading HTML pages, causing concept & corpus distribution drift."
        ),
    }
    dest = HERE / "results" / "coverage" / "drift.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
