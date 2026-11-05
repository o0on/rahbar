# Failure analysis

Five of the six failure modes were induced on purpose, one at a time, each restored and re-verified before the next. The blocks below describe symptoms exactly as the logs printed them.

## Stage 1 · extraction

- **What we broke:** put a corrupt PDF in the corpus directory as the only document — a valid `%PDF-1.9` header, then garbage bytes, no xref/trailer.
- **Symptom:** `python -m pipeline.run` exits 1 with
  `pdfplumber.utils.exceptions.PdfminerException: No /Root object! - Is this really a PDF?`
  raised from `extract()`. No `embedded` or `stored` log lines follow; the target collection's count is unchanged.
- **Diagnosis, as if we had not caused it:** stage order says extraction is first (`run.py`: discover → extract → clean → chunk → embed → store), so nothing downstream can be guilty yet. Rule out a bad download first: `python corpus/fetch.py --verify` re-hashes every local file against the SHA-256s in `corpus/MANIFEST.md` — a mismatch means truncation in transit (then `python corpus/fetch.py --force`). Here the hashes match, so the bytes are exactly what the site published; `pdfinfo <file>` then reproduces `No /Root object` → the file itself is not a parseable PDF: the header claims PDF but the cross-reference table / root catalog is absent. The failing log line appearing before any `embedded` line proves the index was not touched.
- **Remedy:** the Job fails fast with exit 1 — `kubectl logs job/indexer` shows the same traceback and `kubectl get jobs`/`kubectl describe job indexer` exposes the backoff instead of a half-written index. The remedy for the file is upstream: re-fetch (`fetch.py --force`) or remove the URL from `corpus/sources.yaml`. Note OCR cannot rescue this case: OCR rasterises pages *of* a parseable PDF, so a document with no `/Root` never reaches the page loop.

## Stage 2 · chunking

- **What we broke:** set `CHUNK_SIZE=0` (overlap left at 50) in the process environment for a scratch run over the fixture corpus.
- **Symptom:** exit 1 with
  `ValueError: chunk size must be positive` (chunk.py:235),
  raised at `pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)`. Nothing embedded or stored.
- **Diagnosis, as if we had not caused it:** the traceback names `chunk()` directly, and there is no `embedded` log line, so the failure is pre-embedding — configuration, not data. Confirm the source: `kubectl get cm rahbar-config -o yaml` shows `CHUNK_SIZE: "0"` (a typo or a bad edit), while the documents themselves extract and clean normally (`extract` and `clean` stage logs precede the traceback). Had the guard not fired, a non-positive size would pack zero-token sentences forever and `overlap ≥ size` would repeat the tail indefinitely — the symptom would have been a hung Job (`kubectl get pods` shows Running with no completion, `kubectl top pods` flat) rather than a clean exit.
- **Remedy:** set the measured pair, `CHUNK_SIZE=400` / `CHUNK_OVERLAP=50` (docs/PIPELINE.md), `kubectl apply` the ConfigMap, delete the failed Job and re-run (`kubectl create job --from=cronjob/reindex …` or `make index`). The guard stays: config errors are caught at the stage boundary, before any embedding spend.

## Stage 3 · retrieval

- **What we broke:** started the API with `CHROMA_PORT=9999` — nothing listening on that port.
- **Symptom:** the API never becomes ready and exits 1:
  `httpx.ConnectError: [WinError 10061] No connection could be made because the target machine actively refused it` →
  `ValueError: Could not connect to a Chroma server. Are you sure it is running?` →
  `ERROR: Application startup failed. Exiting.`
- **Diagnosis, as if we had not caused it:** the error names the endpoint, so interrogate the backend first: `kubectl get svc,endpoints chroma` (no endpoints → no ready pods), `kubectl logs deploy/chroma` (crashlooping or pending), and from inside the pod network `curl http://chroma:8000/api/v2/heartbeat` → connection refused. If Chroma is healthy, compare `CHROMA_HOST`/`CHROMA_PORT` in `kubectl get cm rahbar-config -o yaml` against the Service — a ConfigMap typo points the client at a port nothing serves. `/readyz` is the steady-state instrument: while broken it returns 503 with the `chroma` check failing; after restore it returns `{"status":"ready","checks":{"chroma":"ok","collection_count":807,"embedding_pin":"ok","redis":"ok"}}`.
- **Remedy:** restore the backend (`kubectl rollout status sts/chroma`, or fix the port) — endpoints appear, the next restart passes startup, readyz flips to 200. Design note: retriever construction is deliberately *not* wrapped in the startup probe (unlike the model-pin check) — a wrong-but-reachable backend would be worse than a dead one, with readyz green while every query fails. Kubernetes restart backoff bridges the gap: once Chroma is reachable the API self-heals.

## Stage 4 · prompt

- **What we broke:** the first shipped version of the `_SYSTEM` prompt told the model to answer from context but never said it must cite the retrieved chunk ids, or how (rules 2/3 were added later, commit `096e3be`).
- **Symptom:** the golden evaluation over 16 questions returned **2 citations in total**, with grounding warnings on the way —
  `rahbar.grounding "pruned fabricated citations" pruned:2 kept:[]` and
  `answer claimed context but every citation was fabricated; downgraded to abstention`.
  Answers carried invented source names/sections instead of ids from `retrieved`; only 8 of 16 questions came back `answered_from_context: true`.
- **Diagnosis, as if we had not caused it:** retrieval was provably innocent — recall@5 was 1.0 on the same run, so the passages and scores reaching the prompt were correct. Compare the response's own fields: `/api/ask` returns `retrieved` and `citations`; the citations name sections that appear nowhere in `retrieved`, which is exactly what `grounding.py` prunes (each citation must match a retrieved passage by source + section). `eval/run_eval.py` shows the collapse as `citations_total: 2`, and the prune warnings cluster inside `generation_ms` — the generator wrote citation-shaped text without being told to copy ids, i.e. the defect is in the prompt layer, not retrieval or the index.
- **Remedy:** `_SYSTEM` now states in rules 2/3 that every claim carries citations and each citation is a chunk id (and section) taken from `retrieved`, never invented. Three layers then hold it: the `Answer` schema requires the array, grounding validates every citation against the response's own passages (pruning fabricated ones and downgrading to abstention when none survive), and the CI gate refuses citation validity below 0.95. Re-run: **22/22 citations valid, 11/16 answered from context** (`eval/results.json`).

## Stage 5 · generation (temperature)

- **What we broke:** `TEMPERATURE=2.0` (shipped default 0.2 — the only custom generation parameter) on the full path (Chroma retriever + Ollama `llama3.2:3b`), after flushing the Redis cache, asking five questions.
- **Symptom:** retrieval was byte-identical to a healthy run (same chunk ids; fees question scores `[0.8652, 0.8436, 0.8396, …]`) but answers degraded: fees and CGPA questions each logged
  `pruned fabricated citations pruned:2 kept:[]` then
  `answer claimed context but every citation was fabricated; downgraded to abstention`
  (`answered_from_context: false`, citations `[]`); the attendance question survived with 5 citations (0 pruned); the version question returned `cache: bypass` and abstained; only **1 of 5** questions was answered from context.
- **Diagnosis, as if we had not caused it:** line up the `rahbar.answer` log entries run-to-run: `retriever`, `chunk_ids`, `scores` and `retrieval_ms` match the healthy run exactly, so the index and retriever are innocent; `generation_ms` is small and there are no timeouts, so it is not a hang. The answers' `citations` cite sections absent from `retrieved` — grounding's prune warnings fire on precisely those — so the generator is inventing citations. Check the environment: `kubectl get cm rahbar-config -o yaml | grep TEMP` (or the process env) shows 2.0 against the shipped 0.2. The Ollama provider constrains decoding to the Answer JSON schema, so structure was still valid — `format` guarantees truth-shaped output, not truth.
- **Remedy:** restore `TEMPERATURE=0.2` (the config default), flush the cache, re-ask the same fees question → `answered_from_context: true`, 5 citations, answer `Rs. 12,000`, log `citations_pruned: 0`. On the same five-question set the shipped configuration answers attendance, fees and version questions from context (q01, q06 and q10 all show `answered_from_context: true` in `eval/results.json`). Defense in depth is kept for a genuinely hot model: grounding's prune + downgrade means a fabricated answer becomes a refusal, never a cited lie — and the eval gate (validity ≥ 0.95) would fail CI if one leaked.
