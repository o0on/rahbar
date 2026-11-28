# Ashar's A3 commands (A, D, F data plane)

Run from the repository root in **Windows PowerShell**, with VS Code opened on
`D:\University Assignments\SCD A2\rahbar`. The local venv is
`.venv\Scripts\python.exe`; the commands below use `kubectl` directly because
the pipeline runs as Jobs in the cluster.

## 0. Local cluster (already prepared on this machine)

`kind v0.33.0` lives at `E:\kind\kind.exe`; cluster name is `rahbar`
(context `kind-rahbar`). All images are pre-loaded into the node, and the
ConfigMap was patched to use the in-cluster Ollama Service:

```powershell
# Recreate from scratch only if needed:
& E:\kind\kind.exe create cluster --name rahbar --wait 300s `
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
docker build -t rahbar-api:latest api
docker build -t rahbar-indexer:latest -f indexer/Dockerfile --build-arg FETCH_CORPUS=0 .
docker build -t rahbar-ui:latest ui
& E:\kind\kind.exe load docker-image rahbar-api:latest rahbar-indexer:latest rahbar-ui:latest --name rahbar
kubectl apply -k k8s/overlays/dev
kubectl apply -f k8s/base/ollama-incluster.yaml

# Point the stack at in-cluster Ollama (kind has no host.minikube.internal):
kubectl -n rahbar get configmap rahbar-config -o yaml |
  ForEach-Object { $_ -replace 'OLLAMA_URL:.*','OLLAMA_URL: "http://ollama:11434"' `
                     -replace 'RETRIEVER:.*','RETRIEVER: "chroma"' } |
  kubectl apply -f -

# Local-only memory saving (HPA minReplicas=3 is Aun's release setting):
kubectl -n rahbar delete hpa api-hpa --ignore-not-found
kubectl -n rahbar scale deploy/api --replicas=1

# Pull the pinned embedding model once Ollama is Ready:
kubectl -n rahbar rollout status deploy/ollama --timeout=600s
kubectl -n rahbar exec deploy/ollama -- ollama pull nomic-embed-text
```

The indexer image now bakes **both** corpora into `/corpus` and
`/corpus/batch_b` (see `indexer/Dockerfile`), so no corpus ConfigMap is needed.

## Part A — data management

Run in this exact order; do not start the next Job until the previous one is
`Completed`. Use `kubectl -n rahbar get jobs` to check.

```powershell
# 0) Batch A first: the 2026-06 handbook is the version Batch B supersedes.
kubectl -n rahbar create job --from=cronjob/reindex ingest-batch-a
kubectl -n rahbar logs -f job/ingest-batch-a

# 1) A1: verification report for Batch B (no indexing).  <-- SCREENSHOT A1
kubectl delete job a3-verify-batch-b --ignore-not-found
kubectl apply -f k8s/jobs/verify-batch-b.yaml
kubectl -n rahbar logs -f job/a3-verify-batch-b

# 2) A2: first Batch B ingest, then the SAME corpus again.
kubectl delete job a3-ingest-batch-b --ignore-not-found
kubectl apply -f k8s/jobs/ingest-batch-b.yaml
kubectl -n rahbar logs -f job/a3-ingest-batch-b

#    <-- SCREENSHOT A2 goes here (after the NEXT command finishes)
kubectl delete job a3-ingest-batch-b-unchanged --ignore-not-found
kubectl apply -f k8s/jobs/ingest-batch-b-unchanged.yaml
kubectl -n rahbar logs -f job/a3-ingest-batch-b-unchanged

# 3) orphan proof / deleted-doc count (optional A3)
kubectl -n rahbar exec deploy/api -- python -c "import os,chromadb; c=chromadb.HttpClient(host=os.environ['CHROMA_HOST'],port=int(os.environ['CHROMA_PORT'])).get_collection(os.environ['CHROMA_COLLECTION']); print(c.get(include=['metadatas'])['metadatas'])"

# 4) rollback of the archived 2026-06 handbook (metadata-only flip; optional A4)
kubectl delete job a3-rollback --ignore-not-found
kubectl -n rahbar create job a3-rollback --image=rahbar-indexer:latest -- python -m pipeline.lake academic-rules-handbook 2026-06
kubectl -n rahbar logs -f job/a3-rollback
```

Local equivalents (no cluster; needs Ollama at `http://127.0.0.1:11434` for
the near-duplicate check; use `--no-embed` with `pipeline.verify` offline):

```powershell
.\.venv\Scripts\python.exe -m pipeline.verify --corpus corpus/batch_b --data data
.\.venv\Scripts\python.exe -m pipeline.run                       # Batch A
$env:CORPUS_DIR = "corpus/batch_b"; .\.venv\Scripts\python.exe -m pipeline.run
.\.venv\Scripts\python.exe -m pipeline.run                       # unchanged re-run -> 0 chunks
.\.venv\Scripts\python.exe -m pipeline.lake academic-rules-handbook 2026-06 --data data
```

## Part D — tracing and observability

```powershell
# Ask with the trace volume configured (in-cluster TRACE_PATH is already set)
kubectl -n rahbar port-forward svc/api 8000:8000
curl.exe -s -X POST http://localhost:8000/api/ask -H 'Content-Type: application/json' `
  -d '{"question":"What CGPA puts a student on academic warning?"}' | .\.venv\Scripts\python.exe -m json.tool
# the X-Trace-Id response header equals the trace id  <-- SCREENSHOT D1

.\.venv\Scripts\python.exe scripts/trace_tool.py <trace-id> --path traces/trace.jsonl   # <-- D2
.\.venv\Scripts\python.exe scripts/trace_summary.py --hours 24 --path traces/trace.jsonl  # -> eval/results/trace-summary.json

# Copy the in-cluster trace file out when needed:
kubectl -n rahbar logs deploy/api | Select-String TRACE
$p = kubectl -n rahbar get pods -l app.kubernetes.io/name=api -o jsonpath='{.items[0].metadata.name}'
kubectl -n rahbar cp "${p}:/data/traces/trace.jsonl" traces/trace.jsonl
```

## Part F — data plane (manifests owned by Ashar)

```powershell
kubectl -n rahbar get all,pvc,configmap,cronjob,job              # <-- SCREENSHOT F1
kubectl -n rahbar get deploy api -o yaml | Select-String -Pattern 'data|mountPath|claimName' -Context 0,2
```

`k8s/base/data-pvc.yaml` provides `/data` for the lake, quarantine, archive,
verification reports and trace lines. The ingestion Job and CronJob are the
only writers to Chroma; the API only reads it. `TRACE_PATH` comes from the
ConfigMap, so no image rebuild is needed to change the trace location.

## Result artifacts committed from this cluster run

* `eval/results/verify/20261124T055121Z.json` — A1 (12 documents, 7 index,
  5 quarantine, near-duplicate threshold 0.92).
* `eval/results/verify/20261124T055148Z.json` — the unchanged A2 run
  (93 rows reused, 0 embedded).
* `traces/trace.jsonl` — real `/api/ask` lines copied from `/data/traces` on the
  data PVC.
* `eval/results/trace-summary.json` — written by `trace_summary.py`.

Measured A2 sequence: Batch A builds 389 chunks; the first Batch B ingest
embeds 93 and prunes 389; the identical re-ingest embeds **0** and reuses 93.
Unchanged files stay in the duplicate-hash map, so exact and near copies are
still quarantined on later runs (`indexer/pipeline/verify.py`).

## Screenshot timing and placement (manual, uncropped, command + output)

| ID | Take it exactly when | Where it goes in `A3-Report-Template.docx` |
|---|---|---|
| A1 | Right after `kubectl -n rahbar logs -f job/a3-verify-batch-b` prints the pass/fail table and `SUMMARY …` | Appendix, replace `A1 — Batch B verification report…` placeholder |
| A2 | Right after `kubectl -n rahbar logs -f job/a3-ingest-batch-b-unchanged` prints `INCREMENTAL RESULT chunks_embedded=0 …` | Appendix, replace `A2 — Second ingestion…` placeholder |
| A3* | After the deleted-doc `count_by_doc_id` output | Add a new Appendix entry after A2 |
| A4* | After archive/rollback logs | Add a new Appendix entry after A3 |
| D1 | After one `/api/ask` showing the JSON response + trace id | Appendix, `D1 — One full trace record…` |
| D2 | After `trace_tool.py <trace-id>` prints context + prompt | Appendix, `D2 — Trace tool…` |
| F1 | After `kubectl -n rahbar get all,pvc,configmap,cronjob,job` | Appendix, `F1 — kubectl get all,pvc…` |

\* Recommended, not required by the submission screenshot list.

**How to capture:** keep the whole VS Code window (terminal tab title + typed
command + full output) visible; do **not** crop the command line or output.
Widen the terminal so table columns are not wrapped. Use `Win + Shift + S`
(Snipping Tool) → *Full window* → save as PNG in `report/screenshots/A1.png`,
`A2.png`, etc. Then in the DOCX, click the `[ PASTE UNCROPPED TERMINAL
SCREENSHOT HERE ]` line under the matching ID and paste. The main report must
stay ≤ 8 pages; the appendix does not count.
