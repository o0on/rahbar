"""Create a representative trace sample for the repo (no cluster needed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.config import Settings  # noqa: E402
from app.schemas import AskResponse, Citation, Passage, RetrievedRef  # noqa: E402
from app.tracing import TraceWriter, finish_trace, new_trace  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "traces" / "sample-trace.jsonl"

settings = Settings(collection="rahbar-v1", embed_model="nomic-embed-text",
                    llm_model="llama3.2:3b")
passage = Passage(
    chunk_id="academic-rules-handbook-jun-2026-p012-0000",
    text="A student is placed on academic warning when the semester GPA falls below 2.0.",
    source="academic-rules-handbook-jun-2026.pdf", section="5.4 Academic Warning",
    page=12, doc_version="2026-06", doc_id="academic-rules-handbook", score=0.91,
)
response = AskResponse(
    request_id="sample-0001",
    answer="A semester GPA below 2.0 places a student on academic warning.",
    citations=[Citation(source=passage.source, section=passage.section, page=passage.page)],
    answered_from_context=True, confidence="high",
    retrieved=[RetrievedRef(chunk_id=passage.chunk_id, source=passage.source,
                            section=passage.section, page=passage.page, score=passage.score)],
    provider="stub",
)
trace = new_trace("What CGPA puts a student on academic warning?", settings,
                  trace_id="sample-0001")
finish_trace(
    trace, question=trace["query"], passages=[passage], response=response,
    cache_hit=False, guardrail_actions=[
        {"type": "retrieval_filter", "action": "superseded=False"}
    ],
    latency_ms={"retrieval_ms": 18.4, "generation_ms": 120.7, "total_ms": 140.2},
    filters={"top_k": 5, "superseded": "excluded"},
    writer=TraceWriter(OUT),
)
print(f"wrote {OUT}")
