"""The indexer Job's entry point. It runs once and exits (section 4.2).

    python -m pipeline.run --dummy            # A2: three fake chunks, no model needed
    python -m pipeline.run                    # A2 compatibility: full rebuild
    python -m pipeline.run --verify-only      # A1: verification report, no indexing
    python -m pipeline.run                    # A3: verify, then incremental sync

Exit code 0 means the index was written; anything else fails the Job, and
Kubernetes retries it up to the Job's backoffLimit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .config import IndexerSettings, get_settings
from .models import Chunk


def log(msg: str, **fields: object) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "logger": "rahbar.indexer", "msg": msg, **fields}), flush=True)


def _front_matter(path: Path) -> dict[str, str]:
    """Read `key: value` lines between leading '---' markers (used by eval/fixtures)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def discover(corpus_dir: str) -> list[tuple[Path, str, bool]]:
    """List (path, doc_version, superseded) for every document to index.

    corpus/fetch.py writes _index.json next to the files it downloads; use it when
    present. Otherwise read Markdown front matter (the CI fixtures work this way).
    Kept exactly as in A2: eval/tune_chunking.py and the starter test rely on it.
    """
    root = Path(corpus_dir)
    index = root / "_index.json"
    if index.exists():
        entries = json.loads(index.read_text(encoding="utf-8"))
        return [(root / e["file"], str(e.get("doc_version", "unknown")),
                 bool(e.get("superseded", False))) for e in entries]
    found = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".pdf", ".md", ".html", ".txt"}:
            continue
        if path.name.lower().startswith("readme"):
            continue
        meta = _front_matter(path) if path.suffix.lower() == ".md" else {}
        found.append((path, meta.get("doc_version", "unknown"),
                      meta.get("superseded", "false").lower() == "true"))
    return found


def _verify_only(settings: IndexerSettings) -> int:
    from .verify import prepare, print_report, verify, write_report, load_sources

    prepared = prepare(load_sources(settings.corpus_dir))
    report = verify(prepared, settings)
    path = write_report(report, settings.data_dir)
    print_report(report)
    print(f"report: {path}")
    return 0


def run_pipeline(settings: IndexerSettings) -> int:
    """A1 verification -> A2 lifecycle update -> incremental vector sync."""
    from .chunk import chunk
    from .embed import embed
    from .lake import apply_verdicts, load_state, record_chunks
    from .store import sync
    from .verify import load_sources, prepare, print_report, verify, write_report

    sources = load_sources(settings.corpus_dir)
    if not sources:
        raise SystemExit(f"no documents found in {settings.corpus_dir}")
    prepared = prepare(sources)
    state = load_state(settings.data_dir)

    # Unchanged bytes keep their previous near-duplicate verdict: no embedding
    # call is needed for them, which is what "0 chunks embedded" means below.
    skip_embed: set[str] = set()
    for item in prepared:
        previous = state["docs"].get(item.source.doc_id, {})
        if previous.get("sha256") == item.source.sha256 and \
                previous.get("doc_version") == item.source.doc_version:
            skip_embed.add(item.source.path.name)

    report = verify(prepared, settings, skip_embed=skip_embed)
    report_path = write_report(report, settings.data_dir)
    print_report(report)
    print(f"report: {report_path}")

    update = apply_verdicts(prepared, state, settings.data_dir, report["run_id"])

    chunks: list[Chunk] = []
    vectors: list[list[float]] = []
    chunk_ids_by_doc: dict[str, list[str]] = {}
    for item in update.new_or_changed:
        if item.document is None:
            raise RuntimeError(f"no extracted text for {item.source.path.name}")
        pieces = chunk(item.document, settings.chunk_size, settings.chunk_overlap)
        chunk_ids_by_doc[item.source.doc_id] = [piece.id for piece in pieces]
        log("chunked", source=item.source.path.name, doc_id=item.source.doc_id,
            chunks=len(pieces))
        vectors.extend(embed([piece.text for piece in pieces], settings))
        chunks.extend(pieces)

    reused = sum(len(state["docs"].get(item.source.doc_id, {}).get("chunk_ids", []))
                 for item in update.unchanged)
    result = sync(
        chunks, vectors, settings,
        deleted_ids=update.deleted_chunk_ids,
        superseded_ids=update.superseded_chunk_ids,
        replaced_doc_ids=update.replaced_doc_ids,
        reused=reused,
    )
    record_chunks(state, update.new_or_changed, chunk_ids_by_doc, settings.data_dir)

    log("A2 incremental summary",
        embedded=result["embedded"], reused=reused, deleted=result["deleted"],
        superseded=result["superseded"], archived=len(update.archived),
        quarantined=len(update.quarantined), total=result["total"])
    # Deliberately plain text: these two lines are the Part A screenshot.
    print(f"INCREMENTAL RESULT  chunks_embedded={result['embedded']}  "
          f"chunks_reused={reused}  chunks_deleted={result['deleted']}  "
          f"rows_superseded={result['superseded']}")
    print(f"LIFECYCLE  archived={len(update.archived)}  "
          f"quarantined={len(update.quarantined)}  "
          f"archived_docs={','.join(update.archived) or '-'}")
    return int(result["total"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the RAHBAR index, then exit.")
    parser.add_argument("--dummy", action="store_true", help="write three fake chunks")
    parser.add_argument("--verify-only", action="store_true",
                        help="A1 only: verify the corpus and write the report, no indexing")
    args = parser.parse_args()
    settings = get_settings()
    log("indexer starting", dummy=args.dummy, verify_only=args.verify_only,
        embed_model=settings.embed_model, collection=settings.collection,
        corpus=settings.corpus_dir, data=settings.data_dir,
        chroma=f"{settings.chroma_host}:{settings.chroma_port}",
        pod=os.environ.get("HOSTNAME", "-"))
    started = time.perf_counter()
    if args.dummy:
        from .dummy import write_dummy_index
        from .store import get_client

        count = write_dummy_index(settings, get_client(settings))
    elif args.verify_only:
        count = _verify_only(settings)
    else:
        count = run_pipeline(settings)
    log("indexer finished", chunks=count, seconds=round(time.perf_counter() - started, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
