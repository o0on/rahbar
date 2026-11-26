# RAHBAR Evaluation & RAGOps Testing (Part B, C, E)

## 1. Golden Dataset (`eval/golden.jsonl`)

The golden benchmark covers university academic regulations, grading tables, fee schedules, faculty entrepreneurship agreements, unanswerable topics, and prompt-injection defenses:

- **Initial Set (25 questions)**: 19 answerable (including 5 hard multi-clause/exact-table/versioned cases) and 6 unanswerable/injection (`must_abstain: true`).
- **Provenance**: 13 hand-written questions and 12 synthetic hand-reviewed questions.
- **Expanded Set (+6 questions in Part E)**: 31 total questions covering Batch B policies (`it-account-policy.md`, `sports-facilities-policy.md`, `fee-refund-policy.md`, `student-services-guide.md`, `library-services-guide.md`, `exam-conduct-injected.md`).

## 2. Three-Level Evaluation Suite (`eval/suite.py`)

Run all three evaluation levels in one command:

```bash
python eval/suite.py --api http://127.0.0.1:8080 --collection rahbar --seed 42 --compare-seed 43
```

1. **Module Level (`eval/module_embedding.py`)**: Tests `nomic-embed-text` directly for paraphrase vs. unrelated cosine gap, embedding latency (`ms/chunk`), bit-exact determinism, and typo noise resilience.
2. **Component Level (`eval/component_retrieval.py`)**: Measures `recall@3`, `recall@5`, `MRR`, and `nDCG@5` on clean queries and typo-perturbed noise queries.
3. **End-to-End Level (`eval/e2e_answers.py`)**: Evaluates `faithfulness`, `abstention`, `citation_validity`, `p50_ms` / `p95_ms`, and human-vs-LLM judge agreement over 12 reviewed labels.

## 3. Release Gate & Change Experiment (`eval/gate.py`, `eval/change_experiment.py`)

Thresholds live in [`k8s/base/gate-configmap.yaml`](../k8s/base/gate-configmap.yaml) and [`eval/gate-thresholds.json`](gate-thresholds.json):

| Threshold Key | Value | Justification |
|---|---:|---|
| `RECALL5_MAX_DROP` | `0.02` | Candidate tuning must retain at least 98% of baseline retrieval recall@5. |
| `FAITHFULNESS_MIN` | `0.80` | At least 80% of answers must stay grounded in cited chunks with factual overlap. |
| `P95_MS_MAX` | `30000` | Enforces 30s p95 SLA for cold-pass laptop GPU inference while catching CPU stalls. |
| `ABSTENTION_MIN_BASELINE` | `1.0` | Zero tolerance for hallucinating answers on out-of-scope or injection probes. |
| `COVERAGE_MIN` | `0.85` | At least 85% of live domain queries must map close to the golden test set. |
