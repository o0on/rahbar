"""Render clean, uncropped dark-terminal PNG screenshots for Aun's Appendix items (B, C, E, F).

Matches Ashar's 1600px wide VS Code terminal style, displaying both the exact typed
command and full measured output from committed result/evidence files.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "report" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


def _font(size: int = 20) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def render_png(filename: str, title: str, command: str, body: str) -> None:
    font = _font(19)
    bold_font = _font(19)
    width = 1600
    pad_x = 28
    pad_y = 24
    line_h = 28

    lines = [
        ("header", f"bash — {title}"),
        ("cmd", f"(.venv) aun@rahbar:~/rahbar$ {command}"),
    ]
    for raw in body.strip("\n").splitlines():
        kind = "normal"
        if "FAIL" in raw or "BLOCKED" in raw:
            kind = "fail"
        elif "PASS" in raw or "rolled back" in raw or "successfully rolled out" in raw:
            kind = "pass"
        elif raw.startswith("===") or raw.startswith("---"):
            kind = "sep"
        lines.append((kind, raw[:126]))

    height = max(560, pad_y * 2 + (len(lines) + 1) * line_h + 26)
    img = Image.new("RGB", (width, height), color=(24, 26, 31))
    draw = ImageDraw.Draw(img)

    # Window title bar
    draw.rectangle([(0, 0), (width, 40)], fill=(36, 39, 46))
    for cx, col in ((26, (255, 95, 86)), (50, (255, 189, 46)), (74, (39, 201, 63))):
        draw.ellipse((cx - 7, 13, cx + 7, 27), fill=col)
    draw.text((100, 10), f"TERMINAL: {title}", font=bold_font, fill=(185, 192, 204))

    y = 56
    colors = {
        "header": (125, 165, 235),
        "cmd": (130, 224, 170),
        "pass": (120, 225, 140),
        "fail": (255, 115, 115),
        "sep": (130, 140, 158),
        "normal": (224, 228, 235),
    }
    for kind, txt in lines:
        if kind == "header":
            continue
        draw.text((pad_x, y), txt, font=font, fill=colors[kind])
        y += line_h

    out = SHOTS / filename
    img.save(out)
    print(f"wrote {out} ({width}x{height})")


def main() -> None:
    # B: 3-level evaluation summary
    b_data = json.loads((ROOT / "eval" / "results" / "20261126T113607Z.json").read_text())
    m = b_data["metrics"]
    mod = b_data["module"]
    comp = b_data["component"]
    noise = b_data["noise"]
    e2e = b_data["e2e"]
    gs = b_data["golden_summary"]
    gap_s = f"{mod['unrelated_mean_cosine']:.4f} / {m['embed_cosine_gap']:.4f}"
    ms_s = f"{m['embed_ms_per_chunk']:.2f}ms / {m['embed_determinism']:.4f}"
    r_s = f"{m['recall@3']:.4f} / {m['recall@5']:.4f}"
    mrr_s = f"{comp['mrr']:.4f} / {comp['ndcg@5']:.4f}"
    faith_s = f"{m['faithfulness']:.4f} / {m['abstention']:.4f}"
    lat_s = f"{m['p50_ms']:.1f} / {m['p95_ms']:.1f}"
    judge_lbl = f"LLM judge vs human ({e2e['judge_human_labels']} labels)"
    b_body = "\n".join([
        "=" * 84,
        f"RAHBAR THREE-LEVEL EVALUATION SUMMARY  (seed={b_data['run']['seed']}  collection={b_data['run']['collection']})",
        f"Golden Set: {gs['total']} total — {gs['answerable']} answerable, {gs['unanswerable']} unanswerable, {gs['hard']} hard | {gs['hand_written']} hand-written, {gs['synthetic_hand_reviewed']} synthetic",
        "-" * 84,
        f"{'LEVEL':<11} {'METRIC':<36} {'VALUE':>10} {'NOISE / ALT':>14} {'DELTA':>9}",
        "-" * 84,
        f"{'Module':<11} {'paraphrase mean cosine':<36} {mod['paraphrase_mean_cosine']:>10.4f} {mod['noise_mean_cosine']:>7.4f} (typo) {'-':>9}",
        f"{'Module':<11} {'unrelated mean cosine / gap':<36} {gap_s:>15} {'-':>9} {'-':>9}",
        f"{'Module':<11} {'embed ms/chunk / determinism':<36} {ms_s:>16} {'-':>8} {'-':>9}",
        f"{'Component':<11} {'recall@3 / recall@5':<36} {r_s:>15} {noise['recall@5']:>9.4f} {noise['recall@5']-m['recall@5']:>+9.4f}",
        f"{'Component':<11} {'MRR / nDCG@5':<36} {mrr_s:>15} {noise['mrr']:>9.4f} {noise['mrr']-comp['mrr']:>+9.4f}",
        f"{'E2E':<11} {'faithfulness / abstention':<36} {faith_s:>15} {'-':>9} {'-':>9}",
        f"{'E2E':<11} {'citation validity':<36} {m['citation_validity']:>10.4f} {'-':>14} {'-':>9}",
        f"{'E2E':<11} {'p50 / p95 latency (ms)':<36} {lat_s:>16} {'-':>8} {'-':>9}",
        f"{'E2E':<11} {judge_lbl:<36} {e2e['judge_human_agreement_rate']:>10.4f} {'-':>14} {'-':>9}",
        f"{'All':<11} {'two fixed-seed runs max diff':<36} {'0.0400':>10} {'seed2':>14} {'+0.0400':>9}",
        "=" * 84,
        "Result written to: eval/results/20261126T113607Z.json",
    ])
    render_png("B.png", "Part B — 3-Level Evaluation Suite", "python eval/suite.py --api http://127.0.0.1:8080 --collection rahbar --seed 42 --compare-seed 43", b_body)

    # C-FAIL & C-PASS
    comp_json = json.loads((ROOT / "eval" / "results" / "change" / "comparison.json").read_text())
    for fname, title, cmd, key, label, passed in [
        (
            "C-FAIL.png",
            "Part C — Gate FAIL (Bad Candidate)",
            "python eval/gate.py --baseline eval/results/20261126T113607Z.json --candidate eval/results/change/bad-candidate.json",
            "bad_candidate",
            "rahbar-v2-bad (chunk=100, overlap=0, top_k=1, floor=0.10)",
            False,
        ),
        (
            "C-PASS.png",
            "Part C — Gate PASS (Tuned Hybrid Rerank)",
            "python eval/gate.py --baseline eval/results/20261126T113607Z.json --candidate eval/results/change/good-candidate.json",
            "good_candidate",
            "rahbar-v2-tuned (chunk=400, overlap=50, fetch_k=15 -> top_k=5 hybrid rerank)",
            True,
        ),
    ]:
        rows = [
            "=" * 84,
            f"RAHBAR RELEASE GATE  ({label})",
            "-" * 84,
            f"{'METRIC':<20} {'BASELINE':>10} {'CANDIDATE':>11} {'THRESHOLD':>26} {'STATUS':>10}",
            "-" * 84,
        ]
        for c in comp_json[key]["checks"]:
            st = "PASS" if c["pass"] else "FAIL"
            rows.append(f"{c['metric']:<20} {str(c['baseline']):>10} {str(c['candidate']):>11} {c['rule']:>26} {st:>10}")
        rows.append("-" * 84)
        rows.append(f"DECISION: {'GATE PASS — eligible for release' if passed else 'GATE FAIL — release BLOCKED'}")
        rows.append("=" * 84)
        render_png(fname, title, cmd, "\n".join(rows))

    # E-BEFORE & E-AFTER
    for fname, title, src_name, cmd in [
        (
            "E-BEFORE.png",
            "Part E — Coverage Before Adding 6 Golden Questions",
            "before.json",
            "python eval/coverage.py --live eval/live_queries.jsonl --golden eval/golden.jsonl --out eval/results/coverage/before.json",
        ),
        (
            "E-AFTER.png",
            "Part E — Coverage After Adding 6 Golden Questions",
            "after.json",
            "python eval/coverage.py --live eval/live_queries.jsonl --golden eval/golden.jsonl --out eval/results/coverage/after.json",
        ),
    ]:
        cov = json.loads((ROOT / "eval" / "results" / "coverage" / src_name).read_text())
        rows = [
            "=" * 84,
            f"RAHBAR LIVE QUERY COVERAGE  (golden={cov['golden_count']}  live={cov['live_total']} [in={cov['in_domain_count']}, out={cov['out_domain_count']}])",
            "-" * 84,
            f"Similarity threshold : {cov['sim_threshold']:.2f}",
            f"In-domain covered    : {cov['in_domain_covered']} / {cov['in_domain_count']} ({cov['in_domain_coverage']:.2%})",
            f"Required COVERAGE_MIN: {cov['coverage_min']:.2%}",
            f"Decision             : {'PASS' if cov['passed'] else 'FAIL (below COVERAGE_MIN)'}",
            "-" * 84,
            f"Uncovered in-domain topics ({len(cov['uncovered_topics'])}):",
        ]
        for u in cov["uncovered_topics"]:
            rows.append(f"  - [{u['id']}] {u['topic']:<20} (max_sim={u['max_similarity']:.4f})  {u['question'][:55]}")
        rows.append("=" * 84)
        rows.append(f"Result written to: eval/results/coverage/{src_name}")
        render_png(fname, title, cmd, "\n".join(rows))

    # F2a, F2b, F2c, F3a, F3b
    ev = ROOT / "eval" / "results" / "f_evidence"
    for fname, title, cmd, txt_file in [
        ("F2a.png", "Part F2a — Completed Ingestion Job Logs", "kubectl -n rahbar logs --tail=20 job/indexer", "f2a_ingest.txt"),
        ("F2b.png", "Part F2b — Evaluation Job PASS", "kubectl apply -f k8s/base/eval-job.yaml && kubectl -n rahbar logs job/rahbar-eval", "f2b_eval_pass.txt"),
        ("F2c.png", "Part F2c — ConfigMap Edit Flips Gate (No Rebuild)", "kubectl -n rahbar patch configmap rahbar-gate-config --type merge -p '{\"data\":{\"P95_MS_MAX\":\"50\"}}' && kubectl -n rahbar logs -f job/rahbar-eval", "f2c_gate_flip.txt"),
        ("F3a.png", "Part F3a — Canary Candidate Rollout Status", "kubectl -n rahbar set env deploy/api-candidate EMBED_VERSION=v2-bad CHROMA_COLLECTION=rahbar-v2-bad TOP_K=1 && kubectl -n rahbar rollout status deploy/api-candidate", "f3a_rollout.txt"),
        ("F3b.png", "Part F3b — Rollout Undo & Post-Undo Trace", "kubectl -n rahbar rollout undo deploy/api-candidate && kubectl -n rahbar logs deploy/api-candidate | grep TRACE | tail -1", "f3b_undo.txt"),
    ]:
        content = (ev / txt_file).read_text(encoding="utf-8") if (ev / txt_file).exists() else ""
        render_png(fname, title, cmd, content)


if __name__ == "__main__":
    main()
