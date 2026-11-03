"""Prometheus metrics at /metrics (section 4.8).

Request count and latency come with the starter kit; everything below is the
observability the case study asks for: retrieval and generation latency, cache
hit ratio, abstention and fallback counts, citations pruned as fabrication
attempts, and tokens in and out of the model.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

router = APIRouter()

REQUESTS = Counter("rahbar_requests_total", "HTTP requests", ["path", "status"])
LATENCY = Histogram(
    "rahbar_request_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

RETRIEVAL = Histogram(
    "rahbar_retrieval_seconds",
    "Time spent embedding the query and reading Chroma",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
GENERATION = Histogram(
    "rahbar_generation_seconds",
    "Time spent waiting on the LLM provider, including its retry",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 60, 120),
)
CACHE = Counter(
    "rahbar_cache_requests_total",
    "Semantic cache outcomes; hit ratio is hit / total",
    ["result"],                                   # hit | miss | bypass
)
ABSTENTIONS = Counter(
    "rahbar_abstentions_total",
    "Answers returned with answered_from_context=false",
)
FALLBACKS = Counter(
    "rahbar_fallbacks_total",
    "Requests answered from the safe fallback after a validation failure",
)
CITATIONS_PRUNED = Counter(
    "rahbar_citations_pruned_total",
    "Citations dropped because no retrieved passage matched them",
)
TOKENS_IN = Counter("rahbar_tokens_in_total", "Prompt tokens sent to the model")
TOKENS_OUT = Counter("rahbar_tokens_out_total", "Completion tokens produced by the model")


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
