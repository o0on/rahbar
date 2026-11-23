"""D1–D4: one JSON trace line per query, plus a small retrieval rail.

The trace is the audit record: it contains the exact evidence and the exact
prompt messages used for an answer, so scripts/trace_tool.py can rebuild them
from a trace id alone. Every line also goes to stdout prefixed with TRACE so
`kubectl logs -n rahbar deploy/rahbar-api | grep TRACE` works without a volume.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .schemas import AskResponse, Passage

INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above|your) instructions"
    r"|reveal (the )?system prompt"
    r"|disregard the corpus"
    r"|do not follow (the )?instructions",
    re.IGNORECASE,
)
_WRITE_LOCK = threading.Lock()


def trace_path() -> Path | None:
    """The volume path; unset means stdout only (keeps A2 tests side-effect free)."""
    value = os.environ.get("TRACE_PATH", "")
    return Path(value) if value else None


class TraceWriter:
    """Append JSON lines to the trace volume and echo them to stdout."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else trace_path()

    def write(self, trace: dict[str, Any]) -> Path | None:
        line = json.dumps(trace, ensure_ascii=False, default=str)
        with _WRITE_LOCK:
            print(f"TRACE {line}", flush=True)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")
        return self.path


def new_trace(question: str, settings: Settings, trace_id: str | None = None) -> dict[str, Any]:
    return {
        "trace_id": trace_id or uuid.uuid4().hex,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query": question,
        "rewritten_query": None,          # this pipeline has no query rewriter
        "embedding_model": settings.embed_model,
        "embedding_version": os.environ.get("EMBED_VERSION", "v1"),
        "collection": settings.collection,
        "retrieved": [],
        "filters": {},
        "reranked_order": [],
        "prompt_hash": None,
        "prompt": [],
        "context": [],
        "model": settings.llm_model,
        "parameters": {"temperature": settings.temperature, "top_k": settings.top_k},
        "response": None,
        "citations": [],
        "guardrail_actions": [],
        "latency_ms": {},
        "cache_hit": False,
    }


def looks_like_injection(text: str | None) -> bool:
    return bool(text and INJECTION.search(text))


def retrieval_rail(passages: list[Passage], question: str) -> tuple[list[Passage], list[dict]]:
    """Flag and drop untrusted text that tries to give the model instructions."""
    actions: list[dict] = []
    kept: list[Passage] = []
    if looks_like_injection(question):
        actions.append({"type": "prompt_injection", "target": "question",
                        "action": "flagged_and_ignored"})
    for passage in passages:
        if looks_like_injection(passage.text):
            actions.append({"type": "prompt_injection",
                            "target": f"chunk:{passage.chunk_id}",
                            "doc_id": passage.doc_id, "source": passage.source,
                            "action": "chunk_dropped"})
        else:
            kept.append(passage)
    return kept, actions


def prompt_for(question: str, passages: list[Passage]) -> list[dict[str, str]]:
    """The exact prompt messages sent to the provider (A2's build_messages)."""
    from .providers.llm.common import build_messages
    return build_messages(question, passages)


def finish_trace(
    trace: dict[str, Any],
    *,
    question: str,
    passages: list[Passage],
    response: AskResponse,
    cache_hit: bool,
    guardrail_actions: list[dict],
    latency_ms: dict[str, float],
    filters: dict[str, Any] | None = None,
    writer: TraceWriter | None = None,
) -> dict[str, Any]:
    """Fill the response-dependent fields, then write one line."""
    prompt = prompt_for(question, passages)
    trace["prompt"] = prompt
    trace["prompt_hash"] = hashlib.sha256(
        json.dumps(prompt, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    trace["context"] = [
        {"chunk_id": p.chunk_id, "doc_id": p.doc_id, "source": p.source,
         "section": p.section, "page": p.page, "doc_version": p.doc_version,
         "score": p.score, "text": p.text}
        for p in passages
    ]
    trace["retrieved"] = [
        {"chunk_id": p.chunk_id, "doc_id": p.doc_id, "doc_version": p.doc_version,
         "score": p.score, "source": p.source}
        for p in passages
    ]
    trace["reranked_order"] = [p.chunk_id for p in passages]
    trace["filters"] = filters or {}
    trace["response"] = {"answer": response.answer,
                         "answered_from_context": response.answered_from_context,
                         "confidence": response.confidence}
    trace["citations"] = [citation.model_dump() for citation in response.citations]
    trace["guardrail_actions"] = guardrail_actions
    trace["latency_ms"] = {key: round(value, 2) for key, value in latency_ms.items()}
    trace["cache_hit"] = cache_hit
    (writer or TraceWriter()).write(trace)
    return trace


def read_trace(trace_id: str, path: Path | None = None) -> dict[str, Any]:
    """Find one trace by id (also matches a unique prefix for convenience)."""
    target = path or trace_path()
    if target is None:
        raise SystemExit("set TRACE_PATH to the trace JSONL file (or pass --path)")
    with target.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            entry = json.loads(line)
            if str(entry.get("trace_id", "")) == trace_id or \
                    str(entry.get("trace_id", "")).startswith(trace_id):
                return entry
    raise SystemExit(f"trace id {trace_id!r} not found in {target}")


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """D3 metrics over a list of trace lines."""
    def percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))]

    top1 = [entry["retrieved"][0]["score"] for entry in entries
            if entry.get("retrieved")]
    stages: dict[str, list[float]] = {}
    for entry in entries:
        for stage, value in (entry.get("latency_ms") or {}).items():
            stages.setdefault(stage, []).append(float(value))
    ttfb = stages.get("total_ms", [])
    return {
        "traces": len(entries),
        "top1_similarity": {"min": round(min(top1), 4) if top1 else 0.0,
                            "mean": round(sum(top1) / len(top1), 4) if top1 else 0.0,
                            "p50": round(percentile(top1, 0.5), 4),
                            "p95": round(percentile(top1, 0.95), 4)},
        "abstention_rate": round(sum(not (entry.get("response") or {}).get(
            "answered_from_context", False) for entry in entries) / len(entries), 4)
            if entries else 0.0,
        "cache_hit_rate": round(sum(bool(entry.get("cache_hit")) for entry in entries)
                                / len(entries), 4) if entries else 0.0,
        "p95_latency_ms_per_stage": {
            stage: round(percentile(values, 0.95), 2)
            for stage, values in sorted(stages.items())
        },
        "p95_total_ms": round(percentile(ttfb, 0.95), 2),
        "injection_attempts_flagged": sum(
            any(action.get("type") == "prompt_injection"
                for action in entry.get("guardrail_actions", []))
            for entry in entries
        ),
    }
