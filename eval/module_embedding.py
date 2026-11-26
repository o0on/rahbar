"""Part B (Level 1 — Module evaluation): Embedding quality, separation, latency, determinism & noise."""

from __future__ import annotations

import math
import os
import time
from typing import Any

import httpx

PARAPHRASE_PAIRS: list[tuple[str, str]] = [
    ("What CGPA places a student on academic warning?", "Minimum GPA required to avoid warning status"),
    ("When is the deadline to withdraw from a course?", "Last date for dropping a registered course with W grade"),
    ("How much is the tuition fee per credit hour?", "Per credit hour semester fee charges for students"),
    ("What happens if library books are returned late?", "Fine and penalty for overdue library material"),
    ("What is the minimum attendance required for exams?", "Percentage of classes required to sit in final examination"),
    ("When can a student apply for a retake exam?", "Eligibility rules for retaking a missed final exam"),
    ("Are security deposits refundable after graduation?", "Policy for refunding admission security deposit"),
    ("What are the rules on using ChatGPT for assignments?", "Policy on generative AI tools in academic coursework"),
]

UNRELATED_PAIRS: list[tuple[str, str]] = [
    ("What is the tuition fee per credit hour?", "Rules for keeping lab computers clean and seated properly"),
    ("When can a student withdraw from a course?", "Faculty entrepreneurship start-up deliverables to ORIC"),
    ("How many grade points is an A- grade worth?", "Minimum attendance required to sit in final examination"),
    ("What is the penalty for overdue library books?", "University admission eligibility criteria and merit list"),
    ("How long is the summer semester?", "Fee payment instructions through Kuickpay gateway"),
    ("What are the grounds for academic dishonesty?", "Campus transfer fee and duplicate transcript fee"),
    ("How does a student appeal a final course grade?", "Student activities fund co-curricular budget"),
    ("When is an academic warning reset to zero?", "Intellectual property rights transfer in faculty startups"),
]

NOISE_PAIRS: list[tuple[str, str]] = [
    ("What CGPA puts a student on academic warning?", "what cgpa puts studnt on academc warnning??"),
    ("What is the tuition fee per credit hour?", "TUITION FEE PER CREDIT HOUR PLS!!"),
    ("When can a student withdraw from a course?", "when can a stduent withdraw form a corse"),
    ("How many grade points is an A- worth?", "how many grde points is an A- wrth?"),
]

LATENCY_SAMPLES = [
    "A student whose CGPA falls below 2.00 is placed on academic warning for that semester.",
    "Tuition fee is Rs. 12,000 per credit hour payable in full before the start of classes.",
    "The deadline to withdraw from a course is the last day of the makeup week.",
    "An admission fee of Rs. 30,000 is payable once at admission and is non-refundable.",
    "Borrowers must return or renew all library material before the due date or face fines.",
    "Generative AI tools may not be used to fabricate citations or bypass academic integrity.",
    "Undergraduate credit transfers require a minimum C grade earned within the last five years.",
    "Retake examination requests are reviewed for medical emergencies or immediate family bereavement.",
]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def embed_batch(texts: list[str], ollama_url: str, model: str, timeout: float = 60.0) -> list[list[float]]:
    url = f"{ollama_url.rstrip('/')}/api/embed"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json={"model": model, "input": texts})
        resp.raise_for_status()
        data = resp.json()
        if "embeddings" in data:
            return [[float(x) for x in v] for v in data["embeddings"]]
        if "embedding" in data:
            return [[float(x) for x in data["embedding"]]]
        raise RuntimeError(f"Unexpected embed payload keys: {list(data.keys())}")


def run_module_eval(
    ollama_url: str | None = None,
    embed_model: str | None = None,
) -> dict[str, Any]:
    ollama_url = ollama_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    embed_model = embed_model or os.environ.get("EMBED_MODEL", "nomic-embed-text")

    para_a = [a for a, _ in PARAPHRASE_PAIRS]
    para_b = [b for _, b in PARAPHRASE_PAIRS]
    unrel_a = [a for a, _ in UNRELATED_PAIRS]
    unrel_b = [b for _, b in UNRELATED_PAIRS]
    noise_clean = [a for a, _ in NOISE_PAIRS]
    noise_dirty = [b for _, b in NOISE_PAIRS]

    vecs_pa = embed_batch(para_a, ollama_url, embed_model)
    vecs_pb = embed_batch(para_b, ollama_url, embed_model)
    vecs_ua = embed_batch(unrel_a, ollama_url, embed_model)
    vecs_ub = embed_batch(unrel_b, ollama_url, embed_model)
    vecs_nc = embed_batch(noise_clean, ollama_url, embed_model)
    vecs_nd = embed_batch(noise_dirty, ollama_url, embed_model)

    para_scores = [cosine_similarity(a, b) for a, b in zip(vecs_pa, vecs_pb)]
    unrel_scores = [cosine_similarity(a, b) for a, b in zip(vecs_ua, vecs_ub)]
    noise_scores = [cosine_similarity(a, b) for a, b in zip(vecs_nc, vecs_nd)]

    para_mean = round(sum(para_scores) / len(para_scores), 4)
    unrel_mean = round(sum(unrel_scores) / len(unrel_scores), 4)
    cosine_gap = round(para_mean - unrel_mean, 4)
    noise_mean = round(sum(noise_scores) / len(noise_scores), 4)

    # Throughput + determinism over two identical passes
    t0 = time.perf_counter()
    pass1 = embed_batch(LATENCY_SAMPLES, ollama_url, embed_model)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    ms_per_chunk = round(elapsed_ms / len(LATENCY_SAMPLES), 2)

    pass2 = embed_batch(LATENCY_SAMPLES, ollama_url, embed_model)
    det_cos = [cosine_similarity(a, b) for a, b in zip(pass1, pass2)]
    max_diff = max(abs(x - y) for va, vb in zip(pass1, pass2) for x, y in zip(va, vb))

    return {
        "embed_model": embed_model,
        "dimension": len(pass1[0]) if pass1 else 0,
        "paraphrase_mean_cosine": para_mean,
        "unrelated_mean_cosine": unrel_mean,
        "embed_cosine_gap": cosine_gap,
        "embed_ms_per_chunk": ms_per_chunk,
        "embed_determinism": round(min(det_cos), 6),
        "embed_max_abs_diff": round(max_diff, 8),
        "noise_mean_cosine": noise_mean,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_module_eval(), indent=2))
