"""A2: the versioned data lake, quarantine, archive and incremental plan.

Layout on the data PVC (/data):

    lake/<doc_id>/<version>.<ext>        currently active source bytes
    quarantine/<doc_id>.<reason>.<file>  rejected by A1, never chunked
    archive/<doc_id>/<version>/          superseded versions + meta.json
    verify/<run_id>.json                 one report per ingestion run
    state.json                           doc_id -> version/hash/chunk ids

The state file is the source of truth for "which chunks belong to which
document". It is what makes zero-change re-ingestion and orphan deletion
provable rather than inferred from a total vector count.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .log import log
from .verify import Prepared

STATE_FILE = "state.json"


@dataclass
class LakeUpdate:
    new_or_changed: list[Prepared] = field(default_factory=list)
    unchanged: list[Prepared] = field(default_factory=list)
    deleted_chunk_ids: list[str] = field(default_factory=list)
    superseded_chunk_ids: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    replaced_doc_ids: set[str] = field(default_factory=set)


def load_state(data_dir: str | Path) -> dict:
    path = Path(data_dir) / STATE_FILE
    if not path.exists():
        return {"version": 1, "updated_at": None, "docs": {}, "runs": [], "rollbacks": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(data_dir: str | Path, state: dict) -> Path:
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = root / STATE_FILE
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return path


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def apply_verdicts(prepared: list[Prepared], state: dict, data_dir: str | Path,
                   run_id: str) -> LakeUpdate:
    """Move files to their lifecycle folder and compute the incremental plan.

    Mutates `state` (write it with save_state after chunk ids are known).
    """
    root = Path(data_dir)
    update = LakeUpdate()
    accepted: set[str] = set()
    archived_ids: set[str] = set()

    for item in prepared:
        src = item.source
        name = src.path.name
        failures = [key for key, check in item.checks.items() if not check.passed]
        reason = failures[0] if failures else "none"
        previous = state["docs"].get(src.doc_id, {})

        if item.action == "quarantine":
            target = root / "quarantine" / f"{src.doc_id}.{reason}.{name}"
            _copy(src.path, target)
            update.quarantined.append(name)
            # A document that was previously good but is now rejected must not
            # leave its old chunks searchable.
            update.deleted_chunk_ids.extend(previous.get("chunk_ids", []))
            state["docs"].pop(src.doc_id, None)
            log("quarantined", doc_id=src.doc_id, source=name, reason=reason,
                path=str(target))
            continue

        if item.action == "archive":
            target_dir = root / "archive" / src.doc_id / src.doc_version
            _copy(src.path, target_dir / name)
            (target_dir / "meta.json").write_text(json.dumps({
                "doc_id": src.doc_id, "doc_version": src.doc_version,
                "source": name, "sha256": src.sha256,
                "chunk_ids": previous.get("chunk_ids", []),
                "superseded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, indent=2) + "\n", encoding="utf-8")
            update.archived.append(f"{src.doc_id}@{src.doc_version}")
            archived_ids.add(src.doc_id)
            # Keep the rows but flip their metadata: the retriever filters on
            # superseded=False, so the archive stops being retrieved at once.
            if previous.get("chunk_ids") and not previous.get("superseded"):
                update.superseded_chunk_ids.extend(previous["chunk_ids"])
                previous["superseded"] = True
            accepted.add(src.doc_id)
            log("archived", doc_id=src.doc_id, version=src.doc_version,
                path=str(target_dir))
            continue

        accepted.add(src.doc_id)
        if previous and previous.get("sha256") == src.sha256 and \
                previous.get("doc_version") == src.doc_version and \
                previous.get("chunk_ids") and not previous.get("superseded"):
            update.unchanged.append(item)
            log("unchanged document - no chunks embedded", doc_id=src.doc_id,
                source=name, chunks=len(previous["chunk_ids"]))
            continue

        target = root / "lake" / src.doc_id / f"{src.doc_version}{src.path.suffix.lower()}"
        _copy(src.path, target)
        update.new_or_changed.append(item)
        update.replaced_doc_ids.add(src.doc_id)
        if previous and (previous.get("superseded") or src.doc_id in archived_ids):
            # An archived version keeps its rows (flagged superseded) so it can
            # be rolled back; only genuinely replaced/removed rows are deleted.
            update.superseded_chunk_ids.extend(previous.get("chunk_ids", []))
        else:
            update.deleted_chunk_ids.extend(previous.get("chunk_ids", []))
        log("changed document - will re-embed", doc_id=src.doc_id, source=name,
            old_version=previous.get("doc_version"), new_version=src.doc_version)

    # A document missing from the corpus is a deletion: no orphan chunks.
    for doc_id, entry in list(state["docs"].items()):
        if doc_id in accepted:
            continue
        update.deleted_chunk_ids.extend(entry.get("chunk_ids", []))
        state["docs"].pop(doc_id, None)
        log("deleted document - chunks will be removed", doc_id=doc_id,
            chunks=len(entry.get("chunk_ids", [])))

    update.deleted_chunk_ids = sorted(set(update.deleted_chunk_ids))
    update.superseded_chunk_ids = sorted(set(update.superseded_chunk_ids) -
                                         set(update.deleted_chunk_ids))
    state.setdefault("runs", []).append({
        "run_id": run_id,
        "embedded": len(update.new_or_changed),
        "unchanged": len(update.unchanged),
        "deleted": len(update.deleted_chunk_ids),
        "archived": len(update.archived),
        "quarantined": len(update.quarantined),
    })
    return update


def record_chunks(state: dict, items: list[Prepared], chunk_ids_by_doc: dict[str, list[str]],
                  data_dir: str | Path) -> Path:
    """Store the chunk ids produced for each newly embedded document."""
    for item in items:
        src = item.source
        state["docs"][src.doc_id] = {
            "source": src.path.name,
            "doc_version": src.doc_version,
            "sha256": src.sha256,
            "superseded": False,
            "chunk_ids": chunk_ids_by_doc.get(src.doc_id, []),
        }
    return save_state(data_dir, state)


def rollback(data_dir: str | Path, doc_id: str, version: str) -> dict:
    """Restore an archived version and mark the current one superseded.

    The chunk rows already exist in Chroma (archiving flagged, never deleted,
    them), so the restore is a metadata-only operation: no re-embedding.
    """
    root = Path(data_dir)
    state = load_state(root)
    meta_path = root / "archive" / doc_id / version / "meta.json"
    if not meta_path.exists():
        available = [p.name for p in (root / "archive" / doc_id).glob("*") if p.is_dir()] \
            if (root / "archive" / doc_id).exists() else []
        raise SystemExit(f"no archive for {doc_id}@{version}; available: {available}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    current = state["docs"].get(doc_id, {})
    restore_ids = meta.get("chunk_ids", [])
    if not restore_ids:
        raise SystemExit(f"archived {doc_id}@{version} has no recorded chunk ids")

    active = root / "lake" / doc_id / f"{version}{Path(meta['source']).suffix.lower()}"
    archived_file = meta_path.parent / meta["source"]
    _copy(archived_file, active)

    state["docs"][doc_id] = {
        "source": meta["source"], "doc_version": version, "sha256": meta["sha256"],
        "superseded": False, "chunk_ids": restore_ids,
    }
    state.setdefault("rollbacks", []).append({
        "doc_id": doc_id, "restored_version": version,
        "previous_version": current.get("doc_version"), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    save_state(root, state)
    log("rollback prepared", doc_id=doc_id, restored_version=version,
        restored_chunks=len(restore_ids),
        superseded_chunks=len(current.get("chunk_ids", [])))
    return {
        "doc_id": doc_id, "restored_version": version,
        "restore_chunk_ids": restore_ids,
        "supersede_chunk_ids": sorted(set(current.get("chunk_ids", [])) - set(restore_ids)),
        "lake_path": str(active),
    }


def main() -> int:
    import argparse

    from .config import get_settings

    parser = argparse.ArgumentParser(description="Roll a superseded document back")
    parser.add_argument("doc_id")
    parser.add_argument("version")
    parser.add_argument("--data", default=None)
    args = parser.parse_args()
    settings = get_settings()
    info = rollback(args.data or settings.data_dir, args.doc_id, args.version)

    # A rollback is a metadata flip in Chroma; the indexer Job remains the only
    # writer, so this command calls the same store module rather than Chroma
    # directly. It needs a vector only to open the collection with the pinned
    # dimension; the existing collection metadata supplies it when empty.
    from .store import apply_rollback
    count = apply_rollback(info, settings)
    print(json.dumps(info, indent=2))
    print(f"active rows in collection after rollback: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
