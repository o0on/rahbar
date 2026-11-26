# Assignment 03 — 50/50 work split (Ashar × Aun)

**Repo decision: use this repository** (`https://github.com/o0on/rahbar`). A3 extends the A2 RAHBAR pipeline; a new repo would duplicate the code, the CI, and the history. A2 stays frozen on `dev`/`main`; all A3 work happens on the shared branch `a3` (cut from `dev`). At the end: `a3 → dev → main` PR, both members commit there.

Both members must be able to explain every part → each member owns an area, then reviews the other's area before the demo (see *Swap review* at the bottom).

---

## 1. Owner map (equal effort)

| Owner | Parts (items) | Files/directories owned | Evidence they produce |
|---|---|---|---|
| **Ashar** (`ashar1x`) | **A1, A2, Batch B** — verification stage, quarantine, incremental data-lake update, archive/rollback, 10 planted-problem docs | `indexer/pipeline/verify.py` (new), `indexer/pipeline/store.py` / `run.py` (incremental logic), `indexer/tests/test_verify.py`, `corpus/batch_b/*`, `eval/results/verify/*`, `data/` layout (git-ignored, layout documented) | A verification report per run, 0-chunk re-ingestion, orphan/rollback counts, Batch B screenshot |
| **Ashar** | **D1–D5** — trace record, trace tool, metrics summary, guardrail logging, root-cause-from-trace | `api/app/tracing.py` (new), `api/app/routes/ask.py` + `api/app/main.py` (trace hook only), `scripts/trace_tool.py`, `scripts/trace_summary.py`, `traces/*` | Full JSON trace line, rebuilt prompt, metrics summary, injection trace, 5-line root cause |
| **Ashar** | **F1 (data plane), F4, F5 (data plane)** — PVC for data lake/quarantine/archive, ingestion Job + CronJob (sole vector-store writer), trace stdout + volume, measured requests/limits for API/Chroma/ingest Job | `k8s/base/pvc.yaml`, `k8s/base/ingestion-job.yaml`, `k8s/base/ingestion-cronjob.yaml`, `k8s/base/trace-*`, resource patches on `api-deployment.yaml` / `chroma-statefulset.yaml` | `kubectl get all,pvc,configmap,cronjob,job`, ingestion Job logs, trace logs via `kubectl logs`, resource numbers |
| **Aun** (`o0on`) | **B (all items)** — 25-question golden set (8 hand-written, 8 synthetic hand-reviewed), module/component/e2e metrics, noise test, seeds + two runs, one-command suite | `eval/golden.jsonl`, `eval/suite.py`, `eval/module_embedding.py`, `eval/component_retrieval.py`, `eval/e2e_answers.py`, `eval/results/<timestamp>.json`, `docs/EVAL.md`, `Makefile` target `a3-eval` | The three-level evaluation summary screenshot + timestamped result file |
| **Aun** | **C1–C3** — one pipeline change (baseline vs candidate), threshold config, gate script with pass + reject cases, release decision | `eval/gate.py`, `eval/gate-thresholds.json` → `k8s/base/gate-configmap.yaml`, `eval/change_experiment.py`, `eval/results/change/*` | Gate FAIL and gate PASS screenshots, comparison table, written decision |
| **Aun** | **E1–E4** — 40 live queries (28 domain / 12 out-of-domain), coverage script + threshold exit code, drift re-run, 6 new golden questions | `eval/live_queries.jsonl`, `eval/coverage.py`, `eval/results/coverage/*`, `eval/golden.jsonl` (additions only) | Coverage before/after screenshots + uncovered topic list |
| **Aun** | **F2 (eval Job + gate ConfigMap), F3, F5 (eval Job)** — eval Job that fails on gate, canary Deployment/Service with own collection, rolling update `maxUnavailable: 0`, `rollout undo`, resource measurements for eval/candidate Jobs | `k8s/base/eval-job.yaml`, `k8s/base/gate-configmap.yaml`, `k8s/base/candidate-deployment.yaml`, `k8s/base/candidate-service.yaml`, `k8s/README.md` | Eval Job logs, ConfigMap-edit gate flip, rollout status/undo + post-undo trace |
| **Joint** | **G report** — one PDF, ≤8 pages + screenshot appendix | `report/A3-Report-Template.docx` (skeleton created), final `report/A3-Report.docx` → exported PDF | Ashar: §1 figure-3 map, §3 A/D/F tables, short answers (a), (c), (f), contribution table assembly, PDF export. Aun: §2 Table-2 qualities, §3 B/C/E tables, short answers (b), (d), (e). |

Effort check: A ≈ 5 units + D ≈ 4 + F data/observability ≈ 4 + report ≈ 1 = **14**; B ≈ 5.5 + C ≈ 2.5 + E ≈ 3 + F release/eval ≈ 3.5 + report ≈ 1 = **15.5** (within noise; Aun takes the slightly larger eval side because Ashar owns the data-side code that B/E consume).

---

## 2. Shared contract — agree on this FIRST, then work in parallel

1. **Trace line** (`api/app/tracing.py`, Ashar writes): one JSON object per query in `traces/trace.jsonl` (also stdout). Keys: `trace_id, timestamp, query, rewritten_query, embedding_model, embedding_version, collection, retrieved:[{chunk_id,doc_id,doc_version,score}], filters, reranked_order, prompt_hash, model, parameters, response, citations, guardrail_actions, latency_ms:{stage:ms}, cache_hit`. Ashar commits a sample line in `traces/sample-trace.jsonl` **before** Aun needs it.
2. **Result file** (`eval/results/<UTC-timestamp>.json`, Aun writes): flat `metrics` dict with keys `recall@3, recall@5, mrr, ndcg@5, faithfulness, abstention, citation_validity, p50_ms, p95_ms, embed_cosine_gap, embed_ms_per_chunk, embed_determinism` plus `run:{seed,collection,embedding_model,git_sha}`. Gate + coverage read this file.
3. **Data layout** (Ashar writes, PVC `/data`): `/data/lake/<doc_id>/<version>.md`, `/data/quarantine/<doc_id>.<reason>.md`, `/data/archive/<doc_id>/<version>/`, `/data/verify/<timestamp>.json`. Chunk metadata always carries `doc_id`, `doc_version`, `page`, `superseded`; `superseded=true` chunks are filtered at retrieval.
4. **Collections**: baseline `rahbar-v1`, candidate `rahbar-v2-<change>`; the API reads `COLLECTION` from env/ConfigMap so switching needs no rebuild. Embedding model+version also in ConfigMap.
5. **Gate ConfigMap keys** (Aun writes): `RECALL5_MAX_DROP=0.02`, `FAITHFULNESS_MIN=<justified>`, `P95_MS_MAX=<justified>`, `ABSTENTION_MIN_BASELINE=1.0`, `COVERAGE_MIN=0.85`. Changing a value must change the Job outcome without rebuilding an image.
6. **Command names** in README (one per part A–F), agreed before coding so neither member duplicates a target.

---

## 3. Independence rules

- Never edit the other member's files directly; send a patch or ask them to change it.
- Shared integration files: `README.md` (Aun edits; Ashar sends his A/D/F command blocks), `k8s/base/kustomization.yaml` (Aun edits; Ashar sends resource filenames), `api/app/main.py` (Ashar edits only the trace hook), `Makefile` (Aun owns).
- Conventional commits, from the correct account (`ashar1x` / `o0on`), small and frequent → both members easily satisfy the two-member commit rule.
- Push to `origin/a3`; pull before push; **no force push**.
- Both run the full suite at least once from a clean cluster before the demo.

---

## 4. Screenshots — start at Part A, not at the end

Capture the command **and** its full output in the terminal, nothing cropped, as soon as that part is runnable. Some states are one-shot (a gate failure, a rollout undo), so retrofitting screenshots later is not possible.

| When | Screenshot ID | What to capture | Owner |
|---|---|---|---|
| After A1/A2 first run | **A** (two shots) | Batch B verification report (pass/fail + reason per check); second ingest on unchanged corpus → 0 chunks embedded | Ashar |
| After B suite | **B** | Evaluation summary showing all three levels | Aun |
| After C gate runs | **C** (two shots) | Gate FAIL (bad candidate) and gate PASS (good candidate) | Aun |
| After D | **D** (two shots) | One complete JSON trace line; `trace_tool.py <id>` rebuilding prompt | Ashar |
| After E | **E** (two shots) | Coverage before and after adding 6 golden questions | Aun |
| On the cluster, F day | **F** (7 shots) | `kubectl get all,pvc,configmap,cronjob,job`; ingestion Job logs; eval Job logs; gate result after ConfigMap edit; `kubectl rollout status`; `kubectl rollout undo`; trace after undo showing previous collection + embedding version | Aun + Ashar |

---

## 5. Swap review (both must explain everything)

- Aun walks Ashar through B/C/E/F3 and Ashar re-runs one metric + the gate from a clean shell.
- Ashar walks Aun through A/D/F1/F4 and Aun re-runs verification + opens a trace from `kubectl logs`.
- 30 minutes each, day before the demo; write the names of the files each member reviewed in the report's contribution table.
