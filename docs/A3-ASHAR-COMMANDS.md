# Ashar's A3 commands (A, D, F data plane)

Run from the repository root with the venv active (Windows PowerShell):
`.venv\Scripts\python.exe` is written as `python` below for readability.

## Part A — data management

```powershell
# 0) first ingestion: Batch A (PDF corpus) is ingested first so the 2026-06
#    handbook is the version that Batch B's 2026-11 handbook supersedes.
kubectl -n rahbar create job --from=cronjob/reindex ingest-batch-a
kubectl -n rahbar logs -f job/ingest-batch-a

# 1) A1 on Batch B: verification report with a pass/fail + reason per check.
#    In the pod the image already contains corpus/batch_b; CORPUS_DIR selects it.
kubectl -n rahbar create job a3-verify-batch-b --image=rahbar-indexer:latest -- `
  python -m pipeline.run --verify-only
#    exact env override, if the Job defaults differ:
#    kubectl -n rahbar create job a3-verify-batch-b --image=rahbar-indexer:latest `
#      --env=CORPUS_DIR=/corpus/batch_b -- python -m pipeline.run --verify-only
kubectl -n rahbar logs job/a3-verify-batch-b

# 2) A2: ingest both batches, then re-run the SAME corpus. The second run is
#    the screenshot: chunks_embedded=0.
kubectl -n rahbar create job --from=cronjob/reindex ingest-both
kubectl -n rahbar logs -f job/ingest-both
kubectl -n rahbar create job --from=cronjob/reindex ingest-both-unchanged
kubectl -n rahbar logs -f job/ingest-both-unchanged

# 3) orphan proof (count by doc_id)
kubectl -n rahbar exec deploy/api -- python -c "import os,chromadb; c=chromadb.HttpClient(host=os.environ['CHROMA_HOST'],port=int(os.environ['CHROMA_PORT'])).get_collection(os.environ['CHROMA_COLLECTION']); print(c.get(include=['metadatas'])['metadatas'])"

# 4) rollback of the archived 2026-06 handbook (metadata-only flip)
kubectl -n rahbar create job a3-rollback --image=rahbar-indexer:latest -- `
  python -m pipeline.lake academic-rules-handbook 2026-06
kubectl -n rahbar logs job/a3-rollback
```

Local equivalents (no cluster; needs Ollama at `http://127.0.0.1:11434` for the
near-duplicate check; use `--no-embed` with `pipeline.verify` for an offline smoke test):

```powershell
python -m pipeline.verify --corpus corpus/batch_b --data data
python -m pipeline.run                       # Batch A; CORPUS_DIR=corpus/raw
$env:CORPUS_DIR = "corpus/batch_b"; python -m pipeline.run   # Batch B
python -m pipeline.run                       # unchanged re-run -> 0 chunks
python -m pipeline.lake academic-rules-handbook 2026-06 --data data
```

## Part D — tracing and observability

```powershell
# Ask with the trace volume configured (local: traces/trace.jsonl)
$env:TRACE_PATH = "traces/trace.jsonl"
curl.exe -s -X POST http://localhost:8000/api/ask -H 'Content-Type: application/json' `
  -d '{"question":"What CGPA puts a student on academic warning?"}' | python -m json.tool
# the X-Trace-Id response header equals the trace id

python scripts/trace_tool.py <trace-id>            # exact context + prompt
python scripts/trace_summary.py --hours 24         # D3 metrics -> eval/results/trace-summary.json

# On the cluster (copy the trace volume out, then use the tool locally)
kubectl -n rahbar logs deploy/api | Select-String TRACE
kubectl -n rahbar cp rahbar/<api-pod>:/data/traces/trace.jsonl traces/trace.jsonl
python scripts/trace_tool.py <trace-id>
```

## Part F — data plane (manifests owned by Ashar)

```powershell
kubectl apply -k k8s/overlays/dev
kubectl -n rahbar get all,pvc,configmap,cronjob,job
kubectl -n rahbar port-forward svc/api 8000:80
```

`k8s/base/data-pvc.yaml` provides `/data` for the lake, quarantine, archive,
verification reports and trace lines. The ingestion Job and CronJob are the
only writers to Chroma; the API only reads it. `TRACE_PATH` comes from the
ConfigMap, so no image rebuild is needed to change the trace location.

## Screenshot timing (manual, uncropped, command + output)

| ID | Take it when | Folder in `A3-Report-Template.docx` |
|---|---|---|
| A1 | Right after the Batch B verification command above | Appendix, placeholder `A1` |
| A2 | Immediately after the second, unchanged ingestion | Appendix, placeholder `A2` |
| A3* | After deleting a document and running `count_by_doc_id` | Add after A2 in Appendix |
| A4* | After archive + rollback | Add after A3 in Appendix |
| D1 | After one `/api/ask` with `TRACE_PATH` set | Appendix, placeholder `D1` |
| D2 | After `trace_tool.py <trace-id>` | Appendix, placeholder `D2` |

\* recommended evidence for the A2 table and short answer (c); not required by
the submission screenshot list.
