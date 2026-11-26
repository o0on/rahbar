"""Generate the A3 report skeleton (docx). Fill in measured values and paste screenshots.

Run:  .venv/Scripts/python.exe report/make_report_template.py
"""

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

OUT = Path(__file__).with_name("A3-Report-Template.docx")

doc = Document()
sec = doc.sections[0]
sec.top_margin = sec.bottom_margin = Inches(0.55)
sec.left_margin = sec.right_margin = Inches(0.6)

style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(9)
style.paragraph_format.space_after = Pt(3)
for name, size in (("Heading 1", 12), ("Heading 2", 10.5), ("Heading 3", 9.5)):
    doc.styles[name].font.size = Pt(size)
    doc.styles[name].font.color.rgb = None


def para(text: str = "", bold: bool = False, italic: bool = False) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = bold
    r.italic = italic


def table(headers: list[str], rows: list[list[str]], widths: list[float] | None = None) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = h
        for p in cell.paragraphs:
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(8)
    for row in rows:
        cells = t.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value
            for p in cells[i].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(8)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    doc.add_paragraph()


def todo(n: int = 1) -> None:
    for _ in range(n):
        para("_________________________________________________________________________________________")


# ---------------------------------------------------------------- title page
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("CS4032 — Assignment 03: RAGOps on Kubernetes")
r.bold = True
r.font.size = Pt(17)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.add_run("RAHBAR RAGOps layer — report").italic = True

para()
table(
    ["Member", "Name", "Roll number", "Area owned (see docs/A3-WORK-SPLIT.md)"],
    [
        ["1", "Ashar ______________", "____________", "A (data/verification), D (tracing/observability), F data plane"],
        ["2", "Aun ______________", "____________", "B (3-level eval), C (gate/release), E (coverage/drift), F release"],
    ],
    [0.6, 1.7, 1.2, 3.4],
)

para("Repository: https://github.com/o0on/rahbar   |   Branch: a3   |   Cluster: local Kubernetes (kind/minikube)")
para(
    "TEMPLATE — replace every <TODO> with a number from your own pipeline run, then export to PDF. "
    "Main report must be ≤ 8 pages; the screenshot appendix does not count. Commit all result files under "
    "eval/results/ and traces/. Paste screenshots only in the Appendix at the labelled placeholders."
)

doc.add_page_break()

# ---------------------------------------------------------------- 1
doc.add_heading("1. Figure 3 lifecycle mapping", level=1)
para("Map every stage of the paper's Figure 3 to our implementation. Be explicit about what is missing.")
table(
    ["Figure 3 stage", "What our pipeline does", "File / K8s object", "What is missing"],
    [
        ["Ingest", "Batch A CronJob reindex plus explicit Batch B Jobs; corpora baked into the indexer image at /corpus and /corpus/batch_b", "reindex-cronjob.yaml, jobs/verify-batch-b.yaml, jobs/ingest-batch-b.yaml", "Batch B is a manual demo Job, not a schedule"],
        ["Verify", "Four checks (quality, completeness, recency, uniqueness) with a reason per failure; 12 Batch B sources -> 7 index, 5 quarantine", "indexer/pipeline/verify.py, eval/results/verify/", "No human sign-off on quarantined files"],
        ["Update data lake", "Versioned active bytes, quarantine and archive on the /data PVC; archive is a metadata-only version flip", "indexer/pipeline/lake.py, data-pvc.yaml", "No retention/compaction policy for old versions"],
        ["Update retrieval sources", "Incremental sync: 93 embedded, 93 reused, 389 pruned on the first Batch B run; 0 embedded on the identical rerun; superseded rows filtered at query time", "indexer/pipeline/store.py, api/app/retriever.py", "No cross-version reranking"],
        ["Offline testing", "Module / component / e2e evaluation Jobs and the release gate (owner: Aun, Part B/C)", "eval/run_eval.py, k8s/jobs (Aun)", "LLM-judge/human agreement not yet measured"],
        ["Live testing", "Canary rollout plus one JSON trace line per query, with latency and guardrail fields", "api/app/tracing.py, k8s/overlays (Aun)", "No automated alerting on the trace rail"],
        ["Check coverage", "Live query log compared with the golden set and a threshold (owner: Aun, Part E)", "eval/golden.yaml, scripts (Aun)", "Coverage measured offline, not continuously in-cluster"],
    ],
    [1.3, 2.5, 1.7, 1.4],
)

# ---------------------------------------------------------------- 2
doc.add_heading("2. Quality attributes (paper Table 2)", level=1)
table(
    ["Quality", "What we measure", "Threshold", "Evidence (result file + screenshot ID)"],
    [
        ["Adaptability", "<TODO: data/chunk config changes>", "<TODO>", "<TODO>"],
        ["Monitorability", "<TODO: drift, latency, retrieval quality>", "<TODO>", "<TODO>"],
        ["Observability", "<TODO: injection flags, top-1 similarity, traces>", "<TODO>", "<TODO>"],
        ["Traceability", "<TODO: versions, collection, grounded source>", "<TODO>", "<TODO>"],
        ["Reliability", "<TODO: resources, rollout/undo, Job failure>", "<TODO>", "<TODO>"],
    ],
    [1.2, 2.4, 1.4, 1.9],
)

# ---------------------------------------------------------------- 3
doc.add_heading("3. Results (Parts A–F)", level=1)

doc.add_heading("A. Data management", level=2)
para("A1 verification — Batch B (one row per document; pass/fail + reason for each check):")
table(
    ["Document", "Quality", "Completeness", "Recency", "Uniqueness", "Action"],
    [
        ["student-services-guide.md", "PASS", "PASS", "PASS", "PASS", "INDEX"],
        ["student-services-guide-copy-a.md", "PASS", "PASS", "PASS", "FAIL: exact sha256 3fc17d0685bd", "QUARANTINE"],
        ["library-services-guide-near.md", "PASS", "PASS", "PASS", "FAIL: cosine 0.999 >= 0.92", "QUARANTINE"],
        ["lab-safety-manual-truncated.md", "PASS", "FAIL: 1 of 4 pages", "PASS", "PASS", "QUARANTINE"],
        ["scholarship-policy-missing-meta.md", "PASS", "FAIL: no title", "PASS", "PASS", "QUARANTINE"],
        ["academic-rules-handbook-nov-2026.md", "PASS", "PASS", "PASS", "PASS", "INDEX"],
    ],
    [1.4, 1.1, 1.2, 1.0, 1.2, 1.0],
)
para("A2 incremental update (counts from the runs, not estimates):")
table(
    ["Run", "Docs in", "Chunks embedded", "Chunks removed", "Archive / rollback"],
    [
        ["Batch A first build", "9 (7 indexed)", "389", "0", "1 archived (2023-08 handbook), 1 quarantined (near-dup report)"],
        ["first Batch B ingest", "12 (7 indexed)", "93", "389", "0 archived, 5 quarantined"],
        ["unchanged re-ingest", "12 (7 indexed)", "0", "0", "0 archived, 5 quarantined; 93 rows reused"],
        ["deleted document", "6 removed from corpus", "0", "291", "chunks removed with no orphan rows"],
        ["superseded + rollback", "newer 2026-11 handbook", "12", "98 old-version rows", "metadata-only rollback implemented; not exercised in this run"],
    ],
    [1.6, 1.0, 1.4, 1.3, 1.6],
)
para("Batch B planted problems: 2 exact duplicates, 1 near-duplicate, 1 corrupt/truncated, 1 missing metadata, 1 newer version, 1 injected instruction. Verification caught 6/7 (the injected instruction is a valid document); the query-time guardrail dropped its 2 injection chunks (trace 4337e509dfb04defb00d6d1a5bad707a), so 7/7 were caught across the two stages.")

doc.add_heading("B. Offline testing at three levels", level=2)
para("Golden set: <TODO> total — <TODO> answerable, <TODO> unanswerable, <TODO> hard; <TODO> hand-written, <TODO> synthetic hand-reviewed.")
table(
    ["Level", "Metric", "Value", "Noise / repeat run", "Delta"],
    [
        ["Module", "paraphrase mean cosine", "<TODO>", "<TODO>", "<TODO>"],
        ["Module", "unrelated mean cosine / gap", "<TODO>", "<TODO>", "<TODO>"],
        ["Module", "embed ms per chunk / determinism", "<TODO>", "<TODO>", "<TODO>"],
        ["Component", "recall@3 / recall@5", "<TODO>", "<TODO>", "<TODO>"],
        ["Component", "MRR / nDCG@5", "<TODO>", "<TODO>", "<TODO>"],
        ["E2E", "faithfulness / abstention / citation validity", "<TODO>", "<TODO>", "<TODO>"],
        ["E2E", "p50 / p95 latency (ms)", "<TODO>", "<TODO>", "<TODO>"],
        ["E2E", "LLM judge vs human agreement (≥8 labels)", "<TODO>", "—", "—"],
        ["All", "two runs with fixed seeds — max difference", "<TODO>", "<TODO>", "<TODO>"],
    ],
    [1.1, 2.4, 1.1, 1.3, 1.0],
)
para("Stochasticity explanation: <TODO>")

doc.add_heading("C. Change experiment and release gate", level=2)
para("Chosen change: <TODO (chunk size/overlap, embedding model, reranker, top-k, prompt)>")
table(
    ["Metric", "Baseline", "Candidate", "Gate threshold", "Decision"],
    [["<TODO>", "<TODO>", "<TODO>", "<TODO>", "<TODO>"] for _ in range(5)],
    [1.8, 1.2, 1.2, 1.5, 1.2],
)
para("Rejected candidate: <TODO>. Released candidate: <TODO>. Justification for each threshold: <TODO>")

doc.add_heading("D. Observability and traceability", level=2)
para("Trace fields present: trace_id, timestamp, query, rewritten_query, embedding model/version, collection, retrieved chunk ids and scores, filters, reranked order, prompt_hash, exact prompt messages and context text, model + parameters, response, citations, guardrail_actions, per-stage latency, cache_hit. Trace ID used in the appendix: see Appendix D1/D2 (rebuild with scripts/trace_tool.py).")
table(
    ["Metric (D3)", "Value over window", "Source"],
    [
        ["top-1 similarity distribution", "min 0.5941, mean 0.7852, p50 0.8348, p95 0.8688 (7 traces / 24 h)", "traces/trace.jsonl -> eval/results/trace-summary.json"],
        ["abstention rate", "0.1429 (1 of 7 queries)", "eval/results/trace-summary.json"],
        ["cache hit rate", "0.1429 (1 of 7 queries)", "eval/results/trace-summary.json"],
        ["p95 latency per stage", "generation 3.05 ms, grounding 1.86 ms, retrieval 225.64 ms, total 8727.88 ms (one cold-query outlier; the other six are 50–346 ms)", "eval/results/trace-summary.json"],
        ["injection attempts flagged (D4)", "1 query, 2 chunks dropped", "trace 4337e509dfb04defb00d6d1a5bad707a"],
    ],
    [2.6, 1.8, 2.5],
)
para("D5 root cause from the trace only: the query asking about the retrieval-system note retrieved exam-conduct-injected-p001-0009/0010; both passages matched the injection pattern, guardrail_actions records chunk_dropped for each, and the answer was an abstention rather than a leaked system prompt. The injected text was data in the corpus, not a user instruction, so the fix belongs in the retrieval/grounding rail, not in the model prompt alone.")

doc.add_heading("E. Drift and coverage", level=2)
table(
    ["Measurement", "Before", "After", "Threshold / note"],
    [
        ["Query coverage (share above similarity threshold)", "<TODO>", "<TODO>", "0.85 (paper example)"],
        ["Uncovered topics", "<TODO>", "<TODO>", "<TODO>"],
        ["Batch B drift — metric changes + cause", "<TODO>", "<TODO>", "<TODO>"],
    ],
    [2.5, 1.3, 1.3, 1.8],
)

doc.add_heading("F. Kubernetes run", level=2)
table(
    ["K8s object", "RAGOps responsibility", "Requests / limits (measured)"],
    [
        ["API Deployment", "Read-only RAG service; writes one JSON trace line per query to the /data PVC", "requests 200m / 256Mi, limits 1000m / 512Mi; 1 replica locally (HPA min 3 kept in the manifest)"],
        ["Chroma StatefulSet + PVC", "Vector store; only the ingestion Job writes to it", "requests 250m / 512Mi, limits 1000m / 1Gi; 5Gi PVC"],
        ["Ingestion Job + CronJob", "Sole writer to Chroma: verify, lake state, embed, sync, invalidate Redis cache", "requests 500m / 1Gi, limits 1500m / 3Gi (raised after an OOMKill at 1536Mi)"],
        ["Eval Job + ConfigMap", "Offline evaluation and the pins the gate compares (owner: Aun)", "ConfigMap rahbar-config: 22 keys including EMBED_VERSION=v1, NEAR_DUP_THRESHOLD=0.92"],
        ["Candidate Deployment + Service", "Rollout / undo of the release candidate (owner: Aun)", "Service api 8000/TCP; canary manifests in k8s/overlays"],
    ],
    [2.0, 2.7, 2.2],
)
para("Fresh-cluster commands: docs/A3-ASHAR-COMMANDS.md and k8s/README.md. Rollout / undo evidence: Appendix F2/F3 (Aun); F1 shows the namespace state captured on the local kind cluster.")

# ---------------------------------------------------------------- 4
doc.add_heading("4. Short answers", level=1)
for label, question in [
    ("(a)", "Which verification check caught the most problems in Batch B? Which problem did you not catch, and what would catch it?"),
    ("(b)", "Example where a module-level metric looked fine but a component/e2e metric did not — or why you saw none."),
    ("(c)", "Did you archive or delete old versions? What breaks if you delete?"),
    ("(d)", "Which metric in your suite do you trust least, and why?"),
    ("(e)", "Which paper idea was hardest to apply, and what did you change from the paper?"),
    ("(f)", "Which Kubernetes object carries which RAGOps responsibility (e.g. the single writer to the vector store)?"),
]:
    para(f"{label} {question}", bold=True)
    if label == "(a)":
        para("Uniqueness caught the most: 3 of 7 planted problems (2 exact sha256 copies and 1 cosine-0.999 near-copy), ahead of completeness (truncated file, missing title) and recency (older handbook version). The injected instruction passed all four source checks because the document is otherwise well formed; the query-time guardrail caught it (2 chunks dropped, trace 4337e509dfb04defb00d6d1a5bad707a). A source-only scanner for injection phrases would catch it at verification time too.")
    elif label == "(c)":
        para("We archive, we do not delete: the superseded 2023-08 handbook was moved to /data/archive and the 2026-11 Batch B replacement keeps the older rows flagged superseded until the metadata-only rollback flips them. Deleting old versions destroys provenance, makes rollback impossible, breaks citations to any in-flight answer, and turns a bad new version into an unrecoverable incident.")
    elif label == "(f)":
        para("The ingestion Job and its CronJob are the only writers to Chroma (indexer/pipeline/store.py); the API Deployment only reads the collection. The rahbar-data PVC carries the lake, quarantine, archive, verification reports and trace lines; the rahbar-config ConfigMap carries the pins (EMBED_VERSION, NEAR_DUP_THRESHOLD, collection, top-k). Services/Ingress provide the stable endpoint, and the Deployment/HPA/candidate objects carry the release responsibilities owned by Aun.")
    else:
        todo(2)

# ---------------------------------------------------------------- 5
doc.add_heading("5. Contributions and AI statement", level=1)
table(
    ["Member", "Parts owned", "Commits / files", "Reviewed partner's work (for demo)"],
    [
        ["Ashar", "A, D, F1/F4/F5 data plane", "<TODO>", "<TODO>"],
        ["Aun", "B, C, E, F2/F3/F5 release", "<TODO>", "<TODO>"],
    ],
    [1.0, 2.1, 1.8, 2.0],
)
para("AI statement (2–3 lines): ", bold=True)
para(
    "<TODO — e.g. AI assistants were used to draft code and report text; every number in this report "
    "was produced by running our own pipeline on our own cluster and is committed under eval/results/ and traces/. "
    "Both members verified and can explain all parts.>"
)

doc.add_page_break()

# ---------------------------------------------------------------- appendix
doc.add_heading("Appendix — screenshots (do not crop; show command + output)", level=1)
shots = [
    ("A1", "Batch B verification report — pass/fail with reasons per check"),
    ("A2", "Second ingestion on an unchanged corpus — 0 chunks embedded"),
    ("B", "Evaluation summary with all three levels"),
    ("C-FAIL", "Release gate rejects the bad candidate"),
    ("C-PASS", "Release gate passes the good candidate"),
    ("D1", "One full trace record (JSON line)"),
    ("D2", "Trace tool rebuilding the exact context and prompt for a trace id"),
    ("E-BEFORE", "Coverage output before adding the 6 new golden questions"),
    ("E-AFTER", "Coverage output after adding them — coverage rises"),
    ("F1", "kubectl get all,pvc,configmap,cronjob,job for the namespace"),
    ("F2a", "Logs of a completed ingestion Job"),
    ("F2b", "Logs of a completed evaluation Job"),
    ("F2c", "Gate result changes after a ConfigMap edit (no image rebuild)"),
    ("F3a", "kubectl rollout status of the candidate release"),
    ("F3b", "kubectl rollout undo + trace showing previous collection and embedding version"),
]
for sid, desc in shots:
    para(f"{sid} — {desc}", bold=True)
    para("[ PASTE UNCROPPED TERMINAL SCREENSHOT HERE ]", italic=True)
    todo(2)

doc.save(OUT)
print(f"wrote {OUT}")
