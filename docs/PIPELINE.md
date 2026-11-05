# Indexing and retrieval pipeline

Five stages run by the `indexer` Job (and locally by `make index`): **extract → clean → chunk → embed → store** (`indexer/pipeline/run.py`). Every number below comes from a committed measurement file: `eval/chunk-tuning.json`, `eval/cache-thresholds.json`, `eval/results.json`, `docs/evidence/03-kubectl-top.txt`.

## Extraction

Which documents needed OCR:

- `undergraduate-academic-rules-aug-2023.pdf` — pdfplumber extracts **0 characters from pages 2-25**; only the cover (p. 1) and back page (p. 26) carry a text layer (`pdffonts` lists 3 embedded fonts, all cover artwork). A page whose extracted text is under `MIN_TEXT_LAYER_CHARS = 20` is rasterised at `OCR_DPI = 200` (poppler `pdftoppm`) and OCR'd with tesseract; recovered text must itself reach 20 characters or the page stays empty and the extractor logs `OCR recovered nothing`.
- `faculty-entrepreneurship-and-technical-start-ups-policy.pdf` — scanned on all 8 pages, OCR'd 8/8.

Measured outcome for the 2023 rules: 24 scanned pages attempted, 23 returned usable text — the document contributes **254 chunks**; page 3 came back under the 20-character floor and produced no chunks. Without OCR the same document contributes **2 chunks** (cover + back text only), which would collapse the two-versions property: golden questions q10/q11 ask for clause 4.24's "last day of classes" in exactly the pages that are image-only.

Before/after, a real table (`fee-structure-page.html`, tuition table). Before — cells collected as flat text in document order, column identity gone (and on the program-wise fee schedule the neighbouring columns interleave):

```
Program Fee BBA / BS Rs. 12,000 MBA / MS Rs. 12,000 PhD Rs. 12,000
```

After — extraction renders every table to Markdown rows before chunking, so the stored chunk keeps each row's cells together (verbatim from the live index, section "Tuition Fee (w.e.f. Fall 2026 semester)"):

```
| Program | Fee |
| --- | --- |
| BBA / BS | Rs. 12,000 |
| MBA / MS | Rs. 12,000 |
| … |
```

What could not be recovered: page 3 of the 2023 rules (OCR text below the floor, logged and left empty); page artwork and scanned logos carry no text by nature; and a structurally damaged PDF cannot be rescued here at all — extraction fails the whole run before anything is written (docs/FAILURE-ANALYSIS.md, Stage 1), so the remedies are a re-fetch (`python corpus/fetch.py --force`, checked by `--verify` against the SHA-256s in `corpus/MANIFEST.md`) or dropping the URL from `sources.yaml`. Hostel/transport material was never fetched — a deliberate corpus exclusion, not an extraction loss (docs/CORPUS.md).

## Cleaning

Rules applied (`indexer/pipeline/clean.py`):

1. **Repeating headers/footers** — a line that sits among the first or last two lines of pages long enough to have margins (≥ 6 lines) and repeats on ≥ 60% of pages (minimum 3) is boilerplate and is dropped everywhere: page titles, "Page 4 of 53", the university URL. Page numbers are normalised away before counting, so "Page 4 of 53" and "Page 12 of 53" count as the same line. Pages of four lines or fewer are left untouched — their "edges" are body text, and deleting body text costs more than keeping a stray header.
2. **Control characters** — control characters, zero-width characters and NUL bytes are replaced with a space (never glued shut: `line\0one` must not become `lineone`); CRLF becomes LF.
3. **Whitespace runs** — runs of blank lines collapse to one blank line; trailing whitespace goes.

One thing deliberately **not** stripped: **clause numbers** ("5.4", "4.24", "1.21"). A citation is `{source, section: "4.25"}`, and the recall/grounding matchers compare that section against chunk text — deleting clause numbers would orphan every exact-term citation the golden set grades (q05, q12 style). For the same reason Markdown table pipes, heading capitalisation ("VIII - COMPUTER LAB RULES") and in-word hyphenation are preserved: chunking keys off headings and citations quote them.

## Chunking

Shipped configuration: **size 400 tokens, overlap 50 tokens → 807 chunks**, storage amplification **1.1473×** — 108,525 stored tokens over 94,594 corpus tokens (stored / original; the >1 comes from overlap repeats and the heading re-echoed onto each chunk). Splitting is structure-aware: a heading starts a new chunk (a chunk never straddles two sections, because a citation names the section it sits under), blank lines end paragraphs, and sentences are packed up to the size with each following chunk repeating up to `overlap` tokens from its neighbour so a definition cut at the boundary stays findable. A "token" is one regex token (`\w+` or a single non-space character) — a deliberate dependency-free count, so chunking needs no tokenizer model.

Three configurations measured by full re-index + golden recall over the 13 answerable questions (q13-q15 expect abstention and are not recall-scored), `eval/chunk-tuning.json`:

| Size (tokens) | Overlap (tokens) | recall@1 | recall@5 | Chunks stored | Storage amplification |
| --- | --- | --- | --- | --- | --- |
| 200 | 40 | 0.6923 | 1.0000 | 1039 | 1.1674x |
| **400** | **50** | 0.6923 | **1.0000** | **807** | **1.1473x** |
| 800 | 100 | 0.8462 | 1.0000 | 927 | 1.3337x |

Why 400/50 ships: all three tie at recall@5 = 1.0 — the metric the CI gate holds — so the tie breaks on cost, and the lowest storage amplification wins. 800/100 edges recall@1 (0.8462 vs 0.6923) but stores 16% more tokens than 400/50 (126,159 vs 108,525) and dilutes the section focus a citation needs; 200/40 stores 1039 chunks for no recall@5 gain. The shipped pair's mean chunk is 134.5 tokens (max 400), so a top-5 answer prompt carries roughly 670 tokens of evidence.

## Embedding

- **Model:** `nomic-embed-text` (Ollama), **vector dimension 768**, cosine space, pinned — there is no fallback to any other model.
- **Where the pin lives:** one ConfigMap key, `EMBED_MODEL`, feeds both processes (`indexer/pipeline/config.py`, `api/app/config.py`, default `nomic-embed-text`). The indexer writes `embed_model` and `dimension` into the Chroma collection's metadata (`store.py`); on a re-index, a mismatch between the stored value and the runtime pin rebuilds the collection instead of mixing two vector spaces.
- **How the API checks it:** (1) fail-fast at startup — the lifespan reads the collection metadata and refuses to start on a mismatch (`main.py`), because queries embedded with a different model would silently return garbage; (2) `/readyz` reports `embedding_pin: "ok" | "mismatch"` (`health.py`) and CI gates on it — measured: `{"status":"ready","checks":{"chroma":"ok","collection_count":807,"embedding_pin":"ok","redis":"ok"}}`; (3) every query and cache embedding is posted with `{"model": settings.embed_model}` (`retrieval/chroma.py`, `services/cache.py`), so index and query vectors always come from the same model.

## Semantic cache

Redis-backed: a question's embedding is compared against cached entries and a cosine similarity at or above the threshold serves the cached answer (`cached: true`). `eval/cache_threshold.py` measured both regions a single threshold must separate (16 golden questions + 16 hand-written paraphrases, `eval/cache-thresholds.json`):

- **Paraphrase pairs** (should hit): min **0.6132**, mean **0.8710**, max **0.9531**.
- **Cross pairs** (a hit would be a false hit): exactly **one** pair sits at or above the shipped 0.88 — q09 "current rules, withdraw by when?" vs q10 "under the August 2023 rules, withdraw by when?" at **0.9442**; the next-highest cross pair of any kind is **0.6897** (q08 security deposit vs q12 admission fee).

Thresholds considered, from those two distributions:

- **0.95 — ruled out:** only 1 of 16 paraphrases (0.9531) reaches it; the cache would almost never hit. A dead cache buys nothing over no cache.
- **≤ 0.69 — ruled out:** it sits under the strongest ordinary cross pair (0.6897); unrelated fee questions would trade answers.
- **Raising past the observed false hit (≥ 0.945) — ruled out:** this is the same dead cache as 0.95 (1 of 16 paraphrases). This is the false hit that shaped the design: eval run 2 served a version question the *other* version's cached answer (q09 ↔ q10 at 0.9442 ≥ 0.88).
- **Shipped: 0.88, unchanged** — with a surgical fix for the false hit instead of a threshold bump: **version questions bypass the cache in both directions** (`is_version_question` reuses the `_OLDER_VERSION` pattern, so a lookup neither reads nor writes the cache for them, commit `9c1edfa`). At 0.88, 8 of 16 paraphrases hit, no non-version pair can collide (0.6897 vs 0.88 — a 0.19 margin), and the final `eval/results.json` shows `cached: false` everywhere: every gate run was scored live.

## Evaluation method

Golden set: `eval/golden.yaml`, 16 questions typed ordinary (9), version (2), exact (2), abstain (2), injection (1). `eval/run_eval.py` scores each `/api/ask` response:

- **recall@k** = fraction of the question's golden `{source, section}` pairs found among the top-k retrieved passages (`recall_at_k`). A pair matches when `source` is byte-identical and the `section` matches by `_section_matches`: normalise both sides (lowercase, non-alphanumerics → spaces) and accept equality **or substring in either direction** — `"5.4"` matches `"5.4 Academic Warning"` whether golden or the model shortened the heading. It is the same rule `api/app/services/grounding.py` uses to validate a citation's section, so a citation the API counts as valid is a passage eval counts as retrieved. A golden entry may also name a `chunk_id`, which must match exactly. Questions with no relevant passages (must-abstain) return `None` and are excluded from recall means.
- **citation validity** = citations that match some passage in the same response's `retrieved` by that rule; gate ≥ 0.95 (measured **1.0, 22/22**).
- **abstention correctness** = must-abstain questions answered with `answered_from_context: false`; gate 100% (measured **2/2**, q13 pets and q14 marine biology).
- **Overall gate:** `recall@5 ≥ baseline − 0.05` (`eval/baseline.json` = 1.0 → floor 0.95), citation validity ≥ 0.95, abstention 100%, 240 s per question. Run: `python eval/run_eval.py --golden eval/golden.yaml --baseline eval/baseline.json --out eval/results.json`. Final: recall@3 = recall@5 = **1.0** (every type 1.0), 11 answers from context, 5 abstentions, 22/22 valid citations. CI runs the same scorer against the fixture corpus in kind (`k8s/overlays/ci` + `eval/fixtures/`).

## How documents reach the indexer Job

**Decision: baked into the image at build time.** The build runs from the repo root (`docker build -f indexer/Dockerfile .`, which the `images` target and CI both do):

1. `COPY corpus/` ships `sources.yaml`, `fetch.py`, `MANIFEST.md` — and `corpus/raw/` (gitignored, 35 MB) on any machine that has fetched.
2. A guarded `RUN python corpus/fetch.py` downloads **only what is missing** (2 s pause between requests, clear User-Agent, 3 attempts with backoff; verified bytes are never re-downloaded). The step is guarded (`|| echo WARN`) so a site outage or an offline build cannot fail the image, and CI passes `FETCH_CORPUS=0` to skip it outright — CI must not depend on a third-party university website, and CI indexes fixtures anyway.
3. The bytes are staged at `/corpus` (including `_index.json`, which maps file → `doc_version` / `superseded`) and the duplicate under `/app` is removed.

At runtime the ConfigMap sets `CORPUS_DIR=/corpus` and `discover()` reads `/corpus/_index.json`. Zero documents exits `no documents found in /corpus` **before** any write, so an empty run can never wipe a good index.

- **Why not fetched at start-up:** every Job and the weekly CronJob (`0 0 * * 0`) would inherit the university site's availability as a runtime dependency, the pod would need egress at start, and CI would need a command override to dodge the fetch. A Job should be reproducible from its image alone.
- **Why not a volume:** no manifest populates one (no init container, no writer) — an empty volume is exactly the `no documents found` failure, with the extra confusion that the image looks healthy.
- **CI:** `eval/fixtures/indexer-job.yaml` mounts the `rahbar-fixtures` ConfigMap at `/corpus`, shadowing whatever the image baked — the e2e eval is hermetic (3 documents, 32 chunks) and network-free.

## Resource numbers

Real output, not estimates: `kubectl top pods -n rahbar --containers` under load is committed as `docs/evidence/03-kubectl-top.txt` (analysis in docs/ENGINEERING-NOTES.md). API replicas sit at 38-45m CPU / 178-186Mi against 256Mi requests and 512Mi limits; Chroma 12m / 310Mi; the indexer Job peaks at **612m CPU / 740Mi** during extract+embed against 512Mi requests and 1536Mi limits — headroom chosen so a full-corpus OCR + embed run is never OOMKilled (exit 137).
