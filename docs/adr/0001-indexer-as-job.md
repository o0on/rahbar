# ADR 0001: Indexer as a Kubernetes Job

## Status
Accepted · November 2026

## Context
In a RAG service deployed to Kubernetes with multiple API replicas (3 replicas in RAHBAR) sharing a single ChromaDB vector database, deciding where and when document indexing executes is a fundamental architectural choice. 

A naive pattern is to execute indexing during API container startup (e.g., in a FastAPI startup event or container entrypoint). In a multi-replica cluster, this naive approach causes catastrophic operational failures:
1. **Concurrent Writer Race Conditions**: Three API replicas starting simultaneously will attempt concurrent writes to the single Chroma volume. Each pod chunking and inserting different documents concurrently risks index corruption and duplicate entries.
2. **Restart Re-indexing Tax**: Any pod restart (due to node drain, rolling updates, or transient failures) forces the full extraction and embedding workload across the entire corpus, burning massive CPU and memory.
3. **Falsified Readiness Probes**: Pods either declare readiness prematurely before vectors are loaded (serving empty search results) or take minutes to become ready, triggering Kubernetes probe failure timeouts and CrashLoopBackOff loops.
4. **Coupled Deployment and Indexing**: Updating the document corpus would require rebuilding and redeploying the API image—a clear anti-pattern (Lecture 05 Friday failure).

## Decision
We decouple indexing from querying completely:
- The **Indexer is implemented as a standalone Kubernetes Job** (`batch/v1 Job`) that runs on demand, plus a scheduled **CronJob** for periodic re-indexing.
- The **API is strictly read-only**, consuming embeddings from Chroma and cache hits from Redis without ever calling write operations on the vector collection.
- API readiness (`/readyz`) strictly probes whether the Chroma collection exists and contains indexed items, withholding traffic until the Job finishes indexing.

## Options Considered
1. **Indexing in API Startup Event**:
   - *Rejected*: Direct race conditions across 3 replicas, massive restart latency, and index corruption on persistent volumes.
2. **InitContainer in API Deployment**:
   - *Rejected*: While InitContainers run before app containers, 3 replicas still spawn 3 InitContainers that race against the shared Chroma storage unless replica counts are artificially held to 1 during bootstrapping.
3. **Dedicated Indexer Batch Job (Chosen)**:
   - *Accepted*: Guarantees exactly one writer at a time, frees API pods from heavy PDF/OCR extraction memory overhead, and allows single-command re-indexing without redeploying the API:
     `kubectl create job --from=cronjob/reindex manual-reindex -n rahbar`

## Consequences
- **Positive**: Zero write-contention on ChromaDB, lean API resource footprints (256Mi memory request), instantaneous API pod restarts during rolling updates, and complete decoupling of corpus updates from code deployments.
- **Negative**: Requires orchestration: the indexer Job must execute successfully before the API pods pass their readiness checks and receive user traffic.
