"""A1: verification, after extraction and before chunking (paper 4.2.2).

Every source gets four independent checks — quality, completeness, recency and
uniqueness — each with a pass/fail reason. Failures go to quarantine (or to the
archive when the only problem is that a newer version exists) and never reach
the chunker. One JSON report per run is written to the data PVC and mirrored
into eval/results/verify when running outside a pod.

Run on its own for the Part A screenshot:

    python -m pipeline.verify --corpus corpus/batch_b --data data

The module has no Chroma dependency; the near-duplicate check uses the same
embedding function as the indexer (injectable for tests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import IndexerSettings, get_settings
from .log import log
from .models import Document

CHECKS = ("quality", "completeness", "recency", "uniqueness")
_VERSION = re.compile(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?")
_TRUNCATION = re.compile(r"truncated|\[cut off\]|…\s*$", re.IGNORECASE)


@dataclass
class Source:
    """A document as the corpus manifest describes it."""

    path: Path
    doc_id: str
    doc_version: str
    superseded: bool
    title: str = ""
    date: str = ""
    page_count: int | None = None
    sha256: str = ""
    manifest: dict = field(default_factory=dict)


@dataclass
class Check:
    passed: bool
    reason: str

    def as_dict(self) -> dict[str, str | bool]:
        return {"pass": self.passed, "reason": self.reason}


@dataclass
class Prepared:
    """Extraction output plus the verification verdict for one source."""

    source: Source
    document: Document | None = None
    sample: str = ""
    checks: dict[str, Check] = field(default_factory=dict)
    action: str = "index"

    @property
    def failed(self) -> bool:
        return any(not check.passed for check in self.checks.values())


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(value).stem.lower()).strip("-")


def _doc_id(entry: dict, aliases: dict[str, str]) -> str:
    file_name = str(entry.get("file", ""))
    explicit = entry.get("doc_id") or aliases.get(file_name)
    if explicit:
        return str(explicit)
    # Strip repeated version suffixes: "...-jun-2026" and "...-2026-11" are
    # the same handbook as "...-2023" when the manifest has no explicit id.
    stem = _slug(file_name)
    stem = re.sub(r"-(?:\d{4}|\d{2}-\d{2}|jun|aug|jan|feb|mar|apr|may|jul|sep|oct|nov|dec)$",
                  "", stem)
    return re.sub(r"-(?:\d{4}|jun|aug|jan|feb|mar|apr|may|jul|sep|oct|nov|dec)$", "", stem)


def load_sources(corpus_dir: str | Path) -> list[Source]:
    """Read _index.json when present; fall back to front matter (A2 fixtures)."""
    from .run import _front_matter

    root = Path(corpus_dir)
    aliases: dict[str, str] = {}
    aliases_path = root.parent / "a3-doc-ids.json"
    if aliases_path.exists():
        aliases = json.loads(aliases_path.read_text(encoding="utf-8"))

    index = root / "_index.json"
    if index.exists():
        entries = json.loads(index.read_text(encoding="utf-8"))
        sources = []
        for entry in entries:
            path = root / str(entry["file"])
            if not path.exists():
                raise SystemExit(f"manifest lists a missing document: {path}")
            page_count = entry.get("page_count")
            sources.append(Source(
                path=path,
                doc_id=_doc_id(entry, aliases),
                doc_version=str(entry.get("doc_version", entry.get("date", "unknown"))),
                superseded=bool(entry.get("superseded", False)),
                title=str(entry.get("title") or entry.get("name") or ""),
                date=str(entry.get("date") or entry.get("fetched_at") or ""),
                page_count=int(page_count) if page_count else None,
                sha256=_file_hash(path),
                manifest=entry,
            ))
        return sources

    sources = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".pdf", ".md", ".html", ".txt"}:
            continue
        if path.name.lower().startswith("readme"):
            continue
        meta = _front_matter(path) if path.suffix.lower() == ".md" else {}
        sources.append(Source(
            path=path,
            doc_id=str(meta.get("doc_id") or _slug(path.name)),
            doc_version=str(meta.get("doc_version", meta.get("date", "unknown"))),
            superseded=str(meta.get("superseded", "false")).lower() == "true",
            title=str(meta.get("title") or path.stem),
            date=str(meta.get("date", "")),
            page_count=int(meta["page_count"]) if meta.get("page_count") else None,
            sha256=_file_hash(path),
            manifest=meta,
        ))
    return sources


def prepare(sources: list[Source]) -> list[Prepared]:
    """Extract and clean once; verification and chunking share this output."""
    from .clean import clean
    from .extract import extract

    prepared: list[Prepared] = []
    for source in sources:
        try:
            document = clean(extract(str(source.path), source.doc_version,
                                     source.superseded))
            document.doc_id = source.doc_id
            text = "\n".join(page.text for page in document.pages)
            sample = re.sub(r"\s+", " ", text[:1200]).strip()
        except Exception as exc:                    # extraction failures are data, not a crash
            log("extraction failed", source=source.path.name, error=str(exc))
            document, sample = None, ""
        prepared.append(Prepared(source=source, document=document, sample=sample))
    return prepared


def _version_key(version: str) -> tuple[int, int, int]:
    match = _VERSION.search(version)
    if not match:
        return (0, 0, 0)
    parts = [int(part or 0) for part in match.groups()]
    return (parts[0], parts[1], parts[2])


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def default_embedder(settings: IndexerSettings):
    from .embed import embed
    return lambda texts: embed(texts, settings)


def verify(prepared: list[Prepared], settings: IndexerSettings,
           embedder=None, skip_embed: set[str] | None = None) -> dict:
    """Run all four checks; mutate each Prepared with its verdict and action."""
    skip_embed = skip_embed or set()
    embedder = embedder or default_embedder(settings)
    newest: dict[str, Prepared] = {}
    for item in prepared:
        current = newest.get(item.source.doc_id)
        if current is None or _version_key(item.source.doc_version) > _version_key(current.source.doc_version):
            newest[item.source.doc_id] = item

    seen_hashes: dict[str, Prepared] = {}
    for item in prepared:
        src = item.source
        doc = item.document
        text = "\n".join(page.text for page in doc.pages) if doc is not None else ""

        # 1. quality: extraction worked and the text is readable.
        if doc is None or not text.strip():
            quality = Check(False, "extraction failed or produced no text")
        else:
            bad = sum(text.count(char) for char in ("\ufffd", "\x00"))
            ratio = bad / max(len(text), 1)
            if ratio > 0.01:
                quality = Check(False, f"garbled text: {ratio:.1%} replacement/NUL characters")
            elif re.search(r"extraction failed|unreadable|access denied", text, re.IGNORECASE):
                quality = Check(False, "extractor error marker in text")
            else:
                quality = Check(True, "text extracted and readable")

        # 2. completeness: required metadata and a complete text.
        missing = []
        if not src.manifest.get("file") and not src.manifest.get("source"):
            missing.append("source")
        if not src.title:
            missing.append("title")
        if not src.date and not src.doc_version:
            missing.append("date/version")
        if src.page_count is None and doc is None:
            missing.append("page count")
        truncation = []
        if src.manifest.get("truncated"):
            truncation.append("manifest marks the file truncated")
        if _TRUNCATION.search(text[-200:]):
            truncation.append("text ends with a truncation marker")
        if src.page_count and doc is not None and len(doc.pages) < src.page_count:
            truncation.append(f"only {len(doc.pages)} of {src.page_count} pages extracted")
        if missing or truncation:
            reason = ", ".join(missing + truncation)
            completeness = Check(False, reason)
        else:
            pages = src.page_count if src.page_count is not None else (len(doc.pages) if doc else 0)
            completeness = Check(True, f"source/title/date/version/page count present ({pages} pages); text complete")

        # 3. recency: a newer version of the same doc_id wins.
        current = newest.get(src.doc_id)
        if current is not None and current is not item and \
                _version_key(current.source.doc_version) > _version_key(src.doc_version):
            recency = Check(False, f"newer version {current.source.doc_version} in {current.source.path.name}")
        elif src.superseded:
            recency = Check(False, "marked superseded in the corpus manifest")
        else:
            recency = Check(True, f"latest version of {src.doc_id} ({src.doc_version})")

        # 4. uniqueness: exact bytes, then embedding similarity.
        if src.sha256 in seen_hashes:
            first = seen_hashes[src.sha256].source
            uniqueness = Check(False, f"exact duplicate of {first.path.name} (sha256 {src.sha256[:12]})")
        elif src.path.name in skip_embed:
            uniqueness = Check(True, "unchanged sha256; prior near-duplicate verdict still stands")
        else:
            seen_hashes[src.sha256] = item
            uniqueness = Check(True, f"unique sha256 {src.sha256[:12]}")

        item.checks = {"quality": quality, "completeness": completeness,
                       "recency": recency, "uniqueness": uniqueness}
        failed = [name for name in CHECKS if not item.checks[name].passed]
        if not failed:
            item.action = "index"
        elif failed == ["recency"]:
            item.action = "archive"
        else:
            item.action = "quarantine"
        if failed:
            log("verification failed", source=src.path.name, action=item.action,
                checks=failed, reasons=[item.checks[name].reason for name in failed])

    # Near-duplicates are checked against documents that passed uniqueness so
    # far. One embedding request for the whole corpus, not one per comparison.
    candidates = [item for item in prepared if item.action in {"index", "archive"} and
                  item.source.path.name not in skip_embed and item.sample]
    if len(candidates) > 1:
        vectors = embedder([item.sample for item in candidates])
    if len(candidates) > 1 and len(vectors) == len(candidates):
        for i, item in enumerate(candidates):
            for j, earlier in enumerate(candidates[:i]):
                score = cosine(vectors[i], vectors[j])
                if score >= settings.near_dup_threshold:
                    item.checks["uniqueness"] = Check(
                        False, f"near-duplicate of {earlier.source.path.name} "
                               f"(cosine {score:.3f} >= {settings.near_dup_threshold})")
                    item.action = "quarantine"
                    log("verification failed", source=item.source.path.name,
                        action="quarantine", checks=["uniqueness"],
                        reasons=[item.checks["uniqueness"].reason])
                    break

    return {
        "run_id": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        "threshold": settings.near_dup_threshold,
        "summary": {
            "documents": len(prepared),
            "index": sum(item.action == "index" for item in prepared),
            "quarantine": sum(item.action == "quarantine" for item in prepared),
            "archive": sum(item.action == "archive" for item in prepared),
            **{name: sum(not item.checks[name].passed for item in prepared) for name in CHECKS},
        },
        "documents": [
            {
                "doc_id": item.source.doc_id,
                "source": item.source.path.name,
                "doc_version": item.source.doc_version,
                "sha256": item.source.sha256,
                "checks": {name: item.checks[name].as_dict() for name in CHECKS},
                "action": item.action,
            }
            for item in prepared
        ],
    }


def write_report(report: dict, data_dir: str | Path) -> Path:
    """Persist the report on the PVC and mirror it into the repo when local."""
    root = Path(data_dir)
    target = root / "verify" / f"{report['run_id']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    mirror = Path(__file__).resolve().parents[2] / "eval" / "results" / "verify"
    if mirror.parent.is_dir():
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / f"{report['run_id']}.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log("verification report written", path=str(target),
        **report["summary"])
    return target


def print_report(report: dict) -> None:
    """Terminal table for the Part A screenshot: command + full output."""
    print(f"Verification run {report['run_id']}  near-duplicate threshold={report['threshold']}")
    print(f"{'SOURCE':<44} {'ID':<24} {'VERSION':<9} {'QUAL':<5} {'COMPL':<6} "
          f"{'RECENT':<7} {'UNIQ':<5} {'ACTION'}")
    for doc in report["documents"]:
        marks = {name: "PASS" if doc["checks"][name]["pass"] else "FAIL"
                 for name in CHECKS}
        print(f"{doc['source'][:44]:<44} {doc['doc_id'][:24]:<24} {doc['doc_version'][:9]:<9} "
              f"{marks['quality']:<5} {marks['completeness']:<6} {marks['recency']:<7} "
              f"{marks['uniqueness']:<5} {doc['action'].upper()}")
        for name in CHECKS:
            check = doc["checks"][name]
            if not check["pass"]:
                print(f"    {name:<13} FAIL — {check['reason']}")
    s = report["summary"]
    print(f"SUMMARY documents={s['documents']} index={s['index']} "
          f"quarantine={s['quarantine']} archive={s['archive']} "
          f"quality_fail={s['quality']} completeness_fail={s['completeness']} "
          f"recency_fail={s['recency']} uniqueness_fail={s['uniqueness']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A1 verification without indexing")
    parser.add_argument("--corpus", default=None, help="corpus directory (default: settings.corpus_dir)")
    parser.add_argument("--data", default=None, help="data PVC path (default: settings.data_dir)")
    parser.add_argument("--no-embed", action="store_true",
                        help="skip the embedding-based near-duplicate check (offline smoke test)")
    args = parser.parse_args()
    settings = get_settings()
    prepared = prepare(load_sources(args.corpus or settings.corpus_dir))
    embedder: Callable[[list[str]], list[list[float]]] | None = \
        (lambda _texts: []) if args.no_embed else None
    report = verify(prepared, settings, embedder=embedder)
    path = write_report(report, args.data or settings.data_dir)
    print_report(report)
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
