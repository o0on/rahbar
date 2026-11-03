# ADR 0003: Structure-Aware Chunking Strategy

## Status
Accepted · November 2026

## Context
University policies, student handbooks, and course catalogs contain hierarchical structures (chapters, numbered clauses, sub-clauses, tables, and long definitions). Fixed-character or fixed-token chunking arbitrarily cuts clauses in half, separates table rows from their column headers, and destroys the contextual coherence necessary for vector search.

Furthermore, superseded policy versions (e.g., 2023 vs 2026 regulations) share similar semantic vocabulary, causing vector search to blur exact clauses unless document boundaries and structural metadata are preserved during chunking.

## Decision
We implement **Structure-Aware Chunking with Token Overlap**:
1. **Hierarchy-Aware Splitting**: Split text on major structural boundaries (headings, numbered sections, clauses), then by paragraphs, then sentences.
2. **Metadata Enrichment**: Every chunk is injected with granular metadata: `source`, `section`, `page`, `doc_version`, and `superseded: bool`.
3. **Chunk Size & Overlap Tuning**: Baseline chunk size of 400 tokens with 50 tokens of sliding overlap, empirically evaluated on the golden dataset to optimize `recall@5` without inflating the storage amplification ratio.

## Options Considered
1. **Naive Fixed-Size Token Chunking (e.g., 200 tokens, 0 overlap)**:
   - *Rejected*: Severely fractures legal/academic definitions and clause references, causing catastrophic drops in exact-term retrieval.
2. **Document-Level Chunking**:
   - *Rejected*: Far too large for embedding models and context windows; dilutes vector similarity.
3. **Structure-Aware Chunking with Overlap (Chosen)**:
   - *Accepted*: Preserves clause context, respects table structure, and maintains high semantic fidelity across policy versions.

## Consequences
- **Positive**: High precision retrieval for clause-specific queries, clear citation mapping to document section/page, and clean filtering of superseded regulations.
- **Negative**: Requires structural parsing rules during extraction and clean stages rather than a simple text slicer.
