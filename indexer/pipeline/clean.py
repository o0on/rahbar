"""Stage 2: Clean. Remove artefacts without destroying structure.

Rules applied (recorded in docs/PIPELINE.md):

1. Repeating running headers and footers: a line that sits in the first or last
   two lines of pages that are long enough to have margins (>= 6 lines, and at
   least 60% of pages, minimum 3) is boilerplate - page titles, "Page 4 of 53",
   the university URL - and is dropped everywhere. Page numbers are normalised
   away before counting so "Page 4 of 53" on one page and "Page 12 of 53" on
   another count as the same line. Pages of four lines or fewer are left
   untouched: their "edges" are body text, and deleting body text is far more
   expensive than keeping a stray header.
2. Control characters, zero-width characters and NUL bytes are replaced with a
   space (never glued shut - "line\0one" must not become "lineone"); CRLF
   becomes LF.
3. Runs of blank lines collapse to one blank line, trailing whitespace goes.

Deliberately NOT stripped: clause numbers ("5.4", "1.21"), Markdown table
pipes, heading capitalisation and hyphenated line breaks - chunking and
citations depend on all four.
"""

from __future__ import annotations

import re

from .models import Document, Page

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[​-‏﻿⁠]")
_TRAILING = re.compile(r"[ \t]+$", re.M)
_BLANK_RUN = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_PAGE_NOISY = re.compile(r"\d+")          # digits vary between pages of one header
_HEADER_LINES = 2                        # lines sampled at each end of a page
_MIN_LINES_FOR_MARGINS = 6               # below this a page has no real margins
_MIN_PAGES_TO_REPEAT = 3
_MIN_REPEAT_RATIO = 0.6


def clean(doc: Document) -> Document:
    """Strip repeating headers/footers and control noise; keep the structure."""
    if not doc.pages:
        return doc
    boilerplate = _repeating_lines(doc.pages)
    pages = [
        Page(source=page.source, page=page.page, ocr=page.ocr,
             text=_clean_page(page.text, boilerplate))
        for page in doc.pages
    ]
    return Document(source=doc.source, doc_version=doc.doc_version,
                    superseded=doc.superseded, pages=pages)


def _normalise(line: str) -> str:
    """A comparison form: lower-case, digits masked, whitespace collapsed."""
    return _MULTI_SPACE.sub(" ", _PAGE_NOISY.sub("#", line.strip().lower()))


def _repeating_lines(pages: list[Page]) -> set[str]:
    """Normalised forms of lines that repeat in header/footer position.

    Only the first and last two lines of pages with at least six lines are
    sampled: on a shorter page every line is "edge", and removing an edge of a
    short page means removing body text.
    """
    if len(pages) < _MIN_PAGES_TO_REPEAT:
        return set()
    counts: dict[str, int] = {}
    for page in pages:
        lines = [ln for ln in page.text.splitlines() if ln.strip()]
        if len(lines) < _MIN_LINES_FOR_MARGINS:
            continue
        sampled = lines[:_HEADER_LINES] + lines[-_HEADER_LINES:]
        for line in {ln for ln in sampled if len(ln.strip()) <= 120}:
            counts[_normalise(line)] = counts.get(_normalise(line), 0) + 1
    threshold = max(_MIN_PAGES_TO_REPEAT, int(len(pages) * _MIN_REPEAT_RATIO))
    # A form made only of "#" is a bare page number ("4" -> "#"), which is
    # exactly the kind of footer we want gone.
    return {form for form, n in counts.items() if n >= threshold}


def _clean_page(text: str, boilerplate: set[str]) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH.sub("", text)
    text = _CONTROL.sub(" ", text)
    kept: list[str] = []
    for line in text.splitlines():
        if boilerplate and _normalise(line) in boilerplate:
            continue                                  # running header or footer
        kept.append(_TRAILING.sub("", _MULTI_SPACE.sub(" ", line)))
    text = "\n".join(kept)
    return _BLANK_RUN.sub("\n\n", text).strip() + "\n"
