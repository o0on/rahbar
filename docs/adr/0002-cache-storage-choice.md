# ADR 0002: Semantic Cache Storage Choice

## Status
Accepted · November 2026

## Context
Serving grounded LLM generation is computationally expensive, latency-intensive (seconds per generation), and resource-heavy. A semantic cache stores previously answered queries and evaluates incoming questions for semantic similarity, returning cached answers when a query matches prior questions above a similarity threshold.

The system requires persistent, low-latency cache storage that supports fast similarity lookups and straightforward bulk invalidation when the indexing Job updates the corpus.

## Decision
We select **Redis with Persistence (AOF / PVC)** as the semantic cache storage engine:
- User queries are embedded with the pinned embedding model (`nomic-embed-text`) and compared against stored question vectors using cosine similarity.
- Redis handles high-throughput in-memory reads, TTL expirations, and straightforward invalidation.
- When an indexer Job executes, a cache flush signal (`FLUSHDB` or generation increment) invalidates stale entries so superseded answers are never served.

## Options Considered
1. **Secondary ChromaDB Collection for Cache**:
   - *Pros*: Uses same vector search API as retrieval.
   - *Cons*: Heavy read/write overhead for transient query-response caching, lacks native atomic TTL expirations, and entangles query cache lifecycle with core vector store operations.
2. **In-Memory Cache inside API Process (Dict / LRU)**:
   - *Pros*: Simplest implementation.
   - *Cons*: Isolated across the 3 API replicas, causing cache misses depending on which pod receives the request; lost on pod restart; cannot be synchronized with re-indexing.
3. **Dedicated Redis Instance with PVC (Chosen)**:
   - *Pros*: Shared across all 3 API replicas, sub-millisecond retrieval latency, persistent across pod restarts, and straightforward cluster-wide invalidation upon corpus re-indexing.
   - *Cons*: Requires managing a separate Deployment and PVC in Kubernetes.

## Consequences
- **Positive**: Consistent cache state across all 3 API replicas, sub-5ms cache hits, clean separation of concern between persistent knowledge store (Chroma) and transient query caching (Redis).
- **Negative**: Adds an additional Kubernetes object and persistent volume to monitor and secure via NetworkPolicies.
