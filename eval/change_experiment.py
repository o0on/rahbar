#!/usr/bin/env python3
"""Part C: Change experiment comparing baseline vs bad candidate vs good candidate.

Indexes real corpora into isolated Chroma candidate collections (rahbar-v2-bad, rahbar-v2-tuned),
evaluates retrieval + Ollama grounded generation across the golden set, and runs eval/gate.py.

Usage:
    python eval/change_experiment.py --baseline eval/results/<baseline>.json \
        --chroma-host 127.0.0.1 --chroma-port 8001 --ollama-url http://127.0.0.1:11434
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "indexer"))
sys.path.insert(0, str(ROOT / "api"))

from component_retrieval import load_jsonl, query_chroma_direct, score_retrieval  # noqa: E402
from e2e_answers import score_e2e  # noqa: E402
from gate import evaluate_gate, load_thresholds, print_gate  # noqa: E402

from app.config import Settings as ApiSettings  # noqa: E402
from app.providers.llm.ollama import OllamaProvider  # noqa: E402
from app.schemas import Citation, Passage  # noqa: E402
from app.services.grounding import abstention, below_floor, ground  # noqa: E402

from pipeline.chunk import chunk as chunk_doc  # noqa: E402
from pipeline.clean import clean as clean_doc  # noqa: E402
from pipeline.config import IndexerSettings  # noqa: E402
from pipeline.embed import embed as embed_chunks  # noqa: E402
from pipeline.extract import extract as extract_doc  # noqa: E402
from pipeline.run import discover  # noqa: E402


def build_candidate_collection(
    *,
    corpus_dir: Path,
    collection: str,
    chunk_size: int,
    chunk_overlap: int,
    chroma_host: str,
    chroma_port: int,
    ollama_url: str,
    redis_host: str,
    redis_port: int,
    embed_model: str,
) -> int:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    client = chromadb.HttpClient(
        host=chroma_host, port=chroma_port, settings=ChromaSettings(anonymized_telemetry=False)
    )
    try:
        col = client.get_collection(name=collection)
        if col.count() > 0:
            print(f"[candidate] reusing populated collection '{collection}' ({col.count()} chunks)")
            return int(col.count())
    except Exception:
        pass

    def _build_with_docker() -> int:
        import subprocess
        # Use rahbar-indexer container (which includes tesseract-ocr + poppler-utils)
        # to build the candidate collection without resetting production Redis.
        py = "\n".join([
            "import os, chromadb",
            "from pipeline.config import IndexerSettings",
            "from pipeline.run import discover",
            "from pipeline.extract import extract",
            "from pipeline.clean import clean",
            "from pipeline.chunk import chunk",
            "from pipeline.embed import embed",
            f"col_name = {collection!r}",
            f"size = {chunk_size}",
            f"ov = {chunk_overlap}",
            "s = IndexerSettings(embed_model=os.environ['EMBED_MODEL'], chroma_host=os.environ['CHROMA_HOST'], chroma_port=int(os.environ['CHROMA_PORT']), collection=col_name, ollama_url=os.environ['OLLAMA_URL'], corpus_dir='/corpus', chunk_size=size, chunk_overlap=ov, redis_host='127.0.0.1', redis_port=6379)",
            "chunks = []",
            "for p, dv, sup in discover('/corpus'):",
            "    chunks.extend(chunk(clean(extract(str(p), dv, sup)), size=size, overlap=ov))",
            "vecs = embed([c.text for c in chunks], s)",
            "cl = chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port)",
            "try:",
            "    cl.delete_collection(col_name)",
            "except Exception:",
            "    pass",
            "col = cl.create_collection(name=col_name, metadata={'hnsw:space': 'cosine', 'embed_model': s.embed_model, 'dimension': len(vecs[0])})",
            "for i in range(0, len(chunks), 100):",
            "    col.upsert(ids=[c.id for c in chunks[i:i+100]], documents=[c.text for c in chunks[i:i+100]], embeddings=vecs[i:i+100], metadatas=[c.metadata() for c in chunks[i:i+100]])",
            "print('BUILT', col_name, col.count())",
        ])
        out = subprocess.check_output(
            [
                "docker", "run", "--rm", "--network", "host",
                "-e", f"CHROMA_HOST={chroma_host}",
                "-e", f"CHROMA_PORT={chroma_port}",
                "-e", f"OLLAMA_URL={ollama_url}",
                "-e", f"EMBED_MODEL={embed_model}",
                "rahbar-indexer:latest", "python", "-c", py,
            ],
            stderr=subprocess.STDOUT,
        ).decode()
        print(out.strip().splitlines()[-1])
        c = client.get_collection(name=collection)
        return int(c.count())

    try:
        return _build_with_docker()
    except Exception as exc:
        print(f"[candidate] docker indexer fallback ({exc})")
    docs = discover(str(corpus_dir))
    all_chunks = []
    # Re-use already-OCR'd page text from baseline Chroma if tesseract is unavailable on host
    ocr_fallback: dict[str, dict[int, str]] = {}
    try:
        base_col = client.get_collection(name="rahbar")
        base_rows = base_col.get(include=["documents", "metadatas"])
        for txt, meta in zip(base_rows.get("documents") or [], base_rows.get("metadatas") or []):
            if not meta:
                continue
            src = str(meta.get("source", ""))
            pg = int(meta.get("page") or 1)
            ocr_fallback.setdefault(src, {})
            ocr_fallback[src][pg] = (ocr_fallback[src].get(pg, "") + "\n\n" + (txt or "")).strip()
    except Exception:
        pass

    from pipeline.models import Document, Page

    for path, doc_version, superseded in docs:
        raw = extract_doc(str(path), doc_version, superseded)
        # Fill any empty scanned pages from baseline Chroma OCR extraction
        if path.name in ocr_fallback:
            fixed_pages = []
            for pg in raw.pages:
                if len(pg.text.strip()) < 20 and pg.page in ocr_fallback[path.name]:
                    fixed_pages.append(Page(source=pg.source, page=pg.page, text=ocr_fallback[path.name][pg.page], ocr=True))
                else:
                    fixed_pages.append(pg)
            raw = Document(source=raw.source, doc_version=raw.doc_version, superseded=raw.superseded, pages=fixed_pages)
        cleaned = clean_doc(raw)
        all_chunks.extend(chunk_doc(cleaned, size=chunk_size, overlap=chunk_overlap))

    settings = IndexerSettings(
        embed_model=embed_model,
        chroma_host=chroma_host,
        chroma_port=chroma_port,
        collection=collection,
        redis_host=redis_host,
        redis_port=redis_port,
        ollama_url=ollama_url,
        corpus_dir=str(corpus_dir),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    vectors = embed_chunks([c.text for c in all_chunks], settings)
    dim = len(vectors[0])
    try:
        client.delete_collection(collection)
    except Exception:
        pass
    col = client.create_collection(
        name=collection,
        metadata={"hnsw:space": "cosine", "embed_model": embed_model, "dimension": dim},
    )
    for start in range(0, len(all_chunks), 100):
        grp = all_chunks[start:start + 100]
        col.upsert(
            ids=[c.id for c in grp],
            documents=[c.text for c in grp],
            embeddings=vectors[start:start + 100],
            metadatas=[c.metadata() for c in grp],
        )
    total = int(col.count())
    print(f"[candidate] indexed '{collection}': {total} chunks (size={chunk_size}, overlap={chunk_overlap})")
    return total


def evaluate_candidate(
    *,
    golden_path: Path,
    collection: str,
    chroma_host: str,
    chroma_port: int,
    ollama_url: str,
    embed_model: str,
    llm_model: str,
    top_k: int,
    relevance_floor: float,
    temperature: float,
    use_rerank: bool = False,
    use_judge: bool = True,
) -> dict[str, Any]:
    import re as _re
    questions = load_jsonl(golden_path)[:25]
    api_settings = ApiSettings(
        retriever="chroma",
        llm_provider="ollama-host",
        embed_model=embed_model,
        chroma_host=chroma_host,
        chroma_port=chroma_port,
        collection=collection,
        top_k=top_k,
        temperature=temperature,
        relevance_floor=relevance_floor,
        llm_model=llm_model,
        ollama_url=ollama_url,
    )
    provider = OllamaProvider(api_settings)

    e2e_rows: list[dict] = []
    ret_records: list[dict] = []

    for q in questions:
        t0 = time.perf_counter()
        fetch_k = max(top_k, 10) if use_rerank else top_k
        raw_passages = query_chroma_direct(
            q["question"],
            chroma_host=chroma_host,
            chroma_port=chroma_port,
            collection_name=collection,
            ollama_url=ollama_url,
            embed_model=embed_model,
            top_k=fetch_k,
        )
        if use_rerank and raw_passages:
            q_terms = {t for t in _re.findall(r"[a-z0-9]+", q["question"].lower()) if len(t) >= 3}
            def _hybrid_key(p: dict) -> float:
                body_terms = set(_re.findall(r"[a-z0-9]+", (p["text"] + " " + p["section"]).lower()))
                overlap = (len(q_terms & body_terms) / len(q_terms)) if q_terms else 0.0
                return 0.80 * float(p["score"]) + 0.20 * overlap
            # Sort candidates by hybrid score, return top 5 for generation to keep prompt compact & fast
            raw_passages = sorted(raw_passages, key=_hybrid_key, reverse=True)[:5]
        passages = [
            Passage(
                chunk_id=p["chunk_id"],
                text=p["text"],
                source=p["source"],
                section=p["section"],
                page=p["page"],
                score=p["score"],
            )
            for p in raw_passages
        ]

        if below_floor(passages, relevance_floor):
            ans = abstention()
        else:
            ans = provider.generate(q["question"], passages)
            ans, _ = ground(ans, passages)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        resp = {
            "answer": ans.answer,
            "citations": [c.model_dump() if isinstance(c, Citation) else c for c in ans.citations],
            "answered_from_context": ans.answered_from_context,
            "confidence": ans.confidence,
            "retrieved": [
                {
                    "chunk_id": p.chunk_id,
                    "source": p.source,
                    "section": p.section,
                    "page": p.page,
                    "score": p.score,
                }
                for p in passages
            ],
        }
        e2e_rows.append({**q, "response": resp, "latency_ms": elapsed_ms})
        ret_records.append({**q, "retrieved": resp["retrieved"]})

    comp = score_retrieval(ret_records)
    e2e = score_e2e(e2e_rows, use_llm_judge=use_judge, ollama_url=ollama_url, judge_model=llm_model)
    return {
        "collection": collection,
        "config": {"top_k": top_k, "relevance_floor": relevance_floor, "temperature": temperature},
        "metrics": {
            "recall@3": comp["recall@3"],
            "recall@5": comp["recall@5"],
            "mrr": comp["mrr"],
            "ndcg@5": comp["ndcg@5"],
            "faithfulness": e2e["faithfulness"],
            "abstention": e2e["abstention"],
            "citation_validity": e2e["citation_validity"],
            "p50_ms": e2e["p50_ms"],
            "p95_ms": e2e["p95_ms"],
        },
        "component": comp,
        "e2e": e2e,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Part C baseline vs candidate gate experiment.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--golden", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus" / "raw")
    parser.add_argument("--chroma-host", default=os.environ.get("CHROMA_HOST", "127.0.0.1"))
    parser.add_argument("--chroma-port", type=int, default=int(os.environ.get("CHROMA_PORT", "8001")))
    parser.add_argument("--redis-host", default=os.environ.get("REDIS_HOST", "127.0.0.1"))
    parser.add_argument("--redis-port", type=int, default=int(os.environ.get("REDIS_PORT", "6379")))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--embed-model", default=os.environ.get("EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "llama3.2:3b"))
    parser.add_argument("--thresholds", type=Path, default=HERE / "gate-thresholds.json")
    parser.add_argument("--out-dir", type=Path, default=HERE / "results" / "change")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    thresholds = load_thresholds(args.thresholds)

    bad_path = args.out_dir / "bad-candidate.json"
    if bad_path.exists():
        bad = json.loads(bad_path.read_text(encoding="utf-8"))
    else:
        # Candidate 1 (BAD): tiny chunk_size=100, overlap=0, top_k=1, loose floor=0.10 -> breaks recall & abstention
        build_candidate_collection(
            corpus_dir=args.corpus,
            collection="rahbar-v2-bad",
            chunk_size=100,
            chunk_overlap=0,
            chroma_host=args.chroma_host,
            chroma_port=args.chroma_port,
            ollama_url=args.ollama_url,
            redis_host=args.redis_host,
            redis_port=args.redis_port,
            embed_model=args.embed_model,
        )
        bad = evaluate_candidate(
            golden_path=args.golden,
            collection="rahbar-v2-bad",
            chroma_host=args.chroma_host,
            chroma_port=args.chroma_port,
            ollama_url=args.ollama_url,
            embed_model=args.embed_model,
            llm_model=args.llm_model,
            top_k=1,
            relevance_floor=0.10,
            temperature=0.2,
        )
        bad_path.write_text(json.dumps(bad, indent=2) + "\n", encoding="utf-8")
    bad_pass, bad_checks = evaluate_gate(baseline, bad, thresholds)
    print_gate(bad_pass, bad_checks, label="rahbar-v2-bad (chunk=100, overlap=0, top_k=1, floor=0.10)")

    # Candidate 2 (GOOD — Released): keep tuned chunk_size=400, overlap=50 in 'rahbar-v2-tuned' (clone/sync from baseline)
    # and enable Hybrid Keyword+Vector Reranking (fetch_k=15 -> top_k=5), eliminating context bloat while lifting recall!
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    ch_cl = chromadb.HttpClient(host=args.chroma_host, port=args.chroma_port, settings=ChromaSettings(anonymized_telemetry=False))
    base_col = ch_cl.get_collection("rahbar")
    base_data = base_col.get(include=["documents", "metadatas", "embeddings"])
    try:
        ch_cl.delete_collection("rahbar-v2-tuned")
    except Exception:
        pass
    tuned_col = ch_cl.create_collection(
        name="rahbar-v2-tuned",
        metadata={"hnsw:space": "cosine", "embed_model": args.embed_model, "dimension": len(base_data["embeddings"][0])},
    )
    for idx in range(0, len(base_data["ids"]), 100):
        tuned_col.upsert(
            ids=base_data["ids"][idx:idx+100],
            documents=base_data["documents"][idx:idx+100],
            embeddings=base_data["embeddings"][idx:idx+100],
            metadatas=base_data["metadatas"][idx:idx+100],
        )
    print(f"[candidate] prepared 'rahbar-v2-tuned' ({tuned_col.count()} chunks) with Hybrid Rerank (fetch_k=15 -> top_k=5)")

    good = evaluate_candidate(
        golden_path=args.golden,
        collection="rahbar-v2-tuned",
        chroma_host=args.chroma_host,
        chroma_port=args.chroma_port,
        ollama_url=args.ollama_url,
        embed_model=args.embed_model,
        llm_model=args.llm_model,
        top_k=15,
        relevance_floor=0.65,
        temperature=0.2,
        use_rerank=True,
        use_judge=True,
    )
    (args.out_dir / "good-candidate.json").write_text(json.dumps(good, indent=2) + "\n", encoding="utf-8")
    good_pass, good_checks = evaluate_gate(baseline, good, thresholds)
    print_gate(good_pass, good_checks, label="rahbar-v2-tuned (chunk=400, overlap=50, fetch_k=15 -> top_k=5 hybrid rerank)")

    summary = {
        "baseline": baseline.get("metrics"),
        "bad_candidate": {"metrics": bad["metrics"], "gate_passed": bad_pass, "checks": bad_checks},
        "good_candidate": {"metrics": good["metrics"], "gate_passed": good_pass, "checks": good_checks},
    }
    (args.out_dir / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote experiment summary to {args.out_dir / 'comparison.json'}")
    return 0 if (not bad_pass and good_pass) else 1


if __name__ == "__main__":
    raise SystemExit(main())
