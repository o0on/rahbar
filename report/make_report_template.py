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
        ["Ingest", "<TODO: batch + CronJob>", "<TODO>", "<TODO>"],
        ["Verify", "<TODO: 4 checks + quarantine>", "<TODO>", "<TODO>"],
        ["Update data lake", "<TODO: versioned lake + archive>", "<TODO>", "<TODO>"],
        ["Update retrieval sources", "<TODO: incremental chunks, superseded filter>", "<TODO>", "<TODO>"],
        ["Offline testing", "<TODO: module / component / e2e + gate>", "<TODO>", "<TODO>"],
        ["Live testing", "<TODO: canary + traces + metrics>", "<TODO>", "<TODO>"],
        ["Check coverage", "<TODO: live log vs golden + threshold>", "<TODO>", "<TODO>"],
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
    [["<TODO>", "<TODO>", "<TODO>", "<TODO>", "<TODO>", "<TODO>"] for _ in range(3)],
    [1.4, 1.1, 1.2, 1.0, 1.2, 1.0],
)
para("A2 incremental update (counts from the runs, not estimates):")
table(
    ["Run", "Docs in", "Chunks embedded", "Chunks removed", "Archive / rollback"],
    [
        ["unchanged re-ingest", "<TODO>", "0", "<TODO>", "<TODO>"],
        ["changed document", "<TODO>", "<TODO>", "<TODO>", "<TODO>"],
        ["deleted document", "<TODO>", "<TODO>", "<TODO>", "<TODO>"],
        ["superseded + rollback", "<TODO>", "<TODO>", "<TODO>", "<TODO>"],
    ],
    [1.6, 1.0, 1.4, 1.3, 1.6],
)
para("Batch B planted problems: 2 exact duplicates, 1 near-duplicate, 1 corrupt/truncated, 1 missing metadata, 1 newer version, 1 injected instruction. Detection rate: <TODO>/7.")

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
para("Trace fields (D1) present: <TODO — tick every required field>. Trace ID used in the appendix: <TODO>")
table(
    ["Metric (D3)", "Value over window", "Source"],
    [
        ["top-1 similarity distribution", "<TODO>", "<TODO>"],
        ["abstention rate", "<TODO>", "<TODO>"],
        ["cache hit rate", "<TODO>", "<TODO>"],
        ["p95 latency per stage", "<TODO>", "<TODO>"],
        ["injection attempts flagged (D4)", "<TODO>", "<TODO>"],
    ],
    [2.6, 1.8, 2.5],
)
para("D5 root cause from the trace only (5 lines):")
todo(5)

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
        ["API Deployment", "<TODO>", "<TODO>"],
        ["Chroma StatefulSet + PVC", "<TODO>", "<TODO>"],
        ["Ingestion Job + CronJob", "<TODO>", "<TODO>"],
        ["Eval Job + ConfigMap", "<TODO>", "<TODO>"],
        ["Candidate Deployment + Service", "<TODO>", "<TODO>"],
    ],
    [2.0, 2.7, 2.2],
)
para("Fresh-cluster commands: see k8s/README.md. Rollout / undo evidence: <TODO>")

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
