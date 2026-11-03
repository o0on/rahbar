"""Stage 1: Extract. PDF (or HTML, or Markdown) to text, page by page.

Three shapes of source arrive here:

* PDF   - pdfplumber pulls the text layer. Pages with no text layer (a scan)
          are detected and sent to OCR (tesseract via pdf2image). Pages that
          contain tables get their prose and their tables separated, because
          letting pdfplumber flow a table's cells produces jumbled columns.
* HTML  - the Fee Structure and Grading pages: a stdlib parser that turns
          <table> rows into Markdown and headings into section markers.
* MD/TXT- the CI fixtures and any plain text: front matter is metadata, not
          content, so it is stripped; form feeds separate pages.

Which documents needed OCR, a before/after table and what could not be
recovered are reported in docs/PIPELINE.md.
"""

from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

from .log import log
from .models import Document, Page

# A page with fewer extractable characters than this has no usable text layer:
# it is a scan. Below this we try OCR; OCR output below this is a failure.
MIN_TEXT_LAYER_CHARS = 20
OCR_DPI = 200


def extract(path: str, doc_version: str, superseded: bool) -> Document:
    """Return the document's text page by page, OCR-ing pages that need it."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        pages = _extract_pdf(p)
    elif suffix in {".html", ".htm"}:
        pages = _extract_html(p)
    else:
        pages = _extract_plain(p)
    log("extracted", source=p.name, pages=len(pages),
        ocr_pages=sum(1 for pg in pages if pg.ocr), doc_version=doc_version)
    return Document(source=p.name, doc_version=doc_version, superseded=superseded, pages=pages)


# --------------------------------------------------------------------------- PDF

def _extract_pdf(path: Path) -> list[Page]:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        raw: list[tuple[str, list[str]]] = []
        for number, page in enumerate(pdf.pages, start=1):
            try:
                raw.append(_page_text_and_tables(page))
            except Exception as exc:  # one broken page must not lose the document
                log("page extraction failed", source=path.name, page=number, error=str(exc))
                raw.append(("", []))

    # OCR only the pages that came back empty, in contiguous runs so the PDF is
    # opened once per run instead of once per page.
    scanned = [i for i, (text, _) in enumerate(raw) if len(text.strip()) < MIN_TEXT_LAYER_CHARS]
    ocr = _ocr_pages(path, [i + 1 for i in scanned]) if scanned else {}

    pages: list[Page] = []
    for number, (text, tables) in enumerate(raw, start=1):
        ocr_used = False
        if number in ocr:
            recovered = ocr[number]
            if len(recovered.strip()) >= MIN_TEXT_LAYER_CHARS:
                text, ocr_used = recovered, True
            else:
                log("OCR recovered nothing", source=path.name, page=number)
        if tables:
            text = (text.rstrip() + "\n\n" if text.strip() else "") + "\n\n".join(tables)
        pages.append(Page(source=path.name, page=number, text=text, ocr=ocr_used))
    return pages


def _page_text_and_tables(page: object) -> tuple[str, list[str]]:
    """Prose from the regions outside the tables, plus each table as Markdown.

    pdfplumber's extract_text() flows a table's cells in y-then-x order, which
    interleaves columns when they have different heights: the jumbled-table
    failure mode (section 2, property 4). Cropping the table regions out first
    keeps the prose readable, and rendering the cells row by row keeps the
    columns in their columns.
    """
    tables = page.find_tables()  # type: ignore[attr-defined]
    if not tables:
        return (page.extract_text() or ""), []  # type: ignore[attr-defined]
    marks = [t.bbox for t in tables]
    prose = _prose_outside(page, marks)
    rendered = [_table_to_markdown(t.extract()) for t in tables]  # type: ignore[attr-defined]
    return prose, [r for r in rendered if r]


def _remainder(full: tuple[float, ...], cut: tuple[float, ...]) -> list[tuple[float, ...]]:
    """The rectangles of `full` left after removing `cut` (up to four pieces)."""
    x0, y0, x1, y1 = full
    cx0, cy0, cx1, cy1 = cut
    rects: list[tuple[float, ...]] = []
    if cy0 > y0:
        rects.append((x0, y0, x1, min(cy0, y1)))
    if cy1 < y1:
        rects.append((x0, max(cy1, y0), x1, y1))
    top, bottom = max(y0, cy0), min(y1, cy1)
    if cx0 > x0:
        rects.append((x0, top, min(cx0, x1), bottom))
    if cx1 < x1:
        rects.append((max(cx1, x0), top, x1, bottom))
    return [r for r in rects if r[2] - r[0] > 4 and r[3] - r[1] > 4]


def _prose_outside(page: object, marks: list[tuple[float, ...]]) -> str:
    regions = [page.bbox]  # type: ignore[attr-defined]
    for mark in marks:
        next_regions: list[tuple[float, ...]] = []
        for region in regions:
            next_regions.extend(_remainder(region, mark))
        regions = next_regions
        if not regions:
            return ""
    pieces: list[tuple[float, str]] = []
    for region in regions:
        try:
            text = page.crop(region).extract_text()  # type: ignore[attr-defined]
        except Exception:
            text = None
        if text and text.strip():
            pieces.append((region[1], text))
    pieces.sort(key=lambda item: item[0])
    return "\n\n".join(text for _, text in pieces)


def _table_to_markdown(rows: list[list[str]]) -> str:
    """Render a table's cells as a Markdown table, one row per line."""
    grid = [[" ".join((cell or "").split()) for cell in row] for row in rows]
    grid = [row for row in grid if any(cell for cell in row)]
    if not grid:
        return ""
    width = max(len(row) for row in grid)
    grid = [row + [""] * (width - len(row)) for row in grid]
    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join("---" for _ in range(width)) + " |"]
    for row in grid[1:]:
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    return "\n".join(lines)


def _ocr_pages(path: Path, numbers: list[int]) -> dict[int, str]:
    """OCR the given pages. Returns {page number: text}; empty when unavailable.

    The OCR binaries (tesseract, poppler's pdftoppm) are system packages: they
    exist in the indexer image, and when they are missing we say so instead of
    pretending the page was empty all along.
    """
    if not numbers:
        return {}
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        log("OCR unavailable: tesseract/pdftoppm not installed - these pages stay empty",
            source=path.name, pages=numbers)
        return {}
    import pytesseract
    from pdf2image import convert_from_path

    out: dict[int, str] = {}
    numbers = sorted(numbers)
    start = previous = numbers[0]
    runs = []
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
        else:
            runs.append((start, previous))
            start = previous = number
    runs.append((start, previous))

    for first, last in runs:
        try:
            images = convert_from_path(str(path), dpi=OCR_DPI, first_page=first, last_page=last)
        except Exception as exc:
            log("OCR conversion failed", source=path.name, first=first, last=last, error=str(exc))
            continue
        for offset, image in enumerate(images):
            page_number = first + offset
            try:
                out[page_number] = pytesseract.image_to_string(image)
            except Exception as exc:
                log("OCR failed", source=path.name, page=page_number, error=str(exc))
    log("OCR finished", source=path.name, pages=len(out))
    return out


# -------------------------------------------------------------------------- HTML

_BLOCK_TAGS = {"p", "div", "section", "article", "li", "ul", "ol", "tr", "br",
               "h1", "h2", "h3", "h4", "h5", "h6", "table", "header", "footer",
               "nav", "main", "form", "blockquote", "pre", "hr"}


class _HtmlToMarkdown(HTMLParser):
    """Collect page text as Markdown: headings, paragraphs, tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._stack: list[str] = []
        self._skip = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._block: list[str] = []

    # -- helpers
    def _flush_block(self) -> None:
        text = " ".join("".join(self._block).split())
        self._block = []
        if text:
            self.parts.append(text + "\n\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush_block()
            level = int(tag[1])
            self._block = ["#" * level + " "]
            self._stack.append("heading")
            return
        if tag == "table":
            self._flush_block()
            self._table, self._row, self._cell = [], None, None
            self._stack.append("table")
            return
        if self._table is not None:
            if tag == "tr":
                self._row = []
            elif tag in {"td", "th"}:
                self._cell = []
            return
        if tag in {"br", "hr"}:        # void elements: break the block, push nothing
            self._flush_block()
        elif tag in _BLOCK_TAGS:
            self._flush_block()
            if tag in {"header", "footer", "nav"}:
                self._skip += 1
                self._stack.append("skip")
            else:
                self._stack.append("block")
        elif not self._stack or self._stack[-1] != "heading":
            self._stack.append("inline")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip = max(0, self._skip - 1)
            return
        if tag in {"header", "footer", "nav"} and self._skip and \
                self._stack and self._stack[-1] == "skip":
            self._skip -= 1                    # leaves the skipped region
            self._stack.pop()
            return
        if self._skip:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._stack and \
                self._stack[-1] == "heading":
            self._flush_block()
            self._stack.pop()
            return
        if tag == "table" and self._table is not None:
            self.parts.append(_table_to_markdown([row for row in self._table]) + "\n\n")
            self._table = None
            if self._stack and self._stack[-1] == "table":
                self._stack.pop()
            return
        if self._table is not None:
            if tag == "tr" and self._row is not None:
                if any(cell.strip() for cell in self._row):
                    self._table.append(self._row)
                self._row = None
            elif tag in {"td", "th"} and self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            return
        if tag in _BLOCK_TAGS:
            self._flush_block()
            if self._stack:
                self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._table is not None:
            if self._cell is not None:
                self._cell.append(data)
            return
        if self._stack and self._stack[-1] == "heading":
            self._block.append(data)
        elif data.strip():
            self._block.append(data)

    def text(self) -> str:
        self._flush_block()
        return "".join(self.parts)


def _extract_html(path: Path) -> list[Page]:
    parser = _HtmlToMarkdown()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    text = _tidy_html_text(parser.text())
    return [Page(source=path.name, page=1, text=text)]


def _tidy_html_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# ----------------------------------------------------------------------- plain text

def _extract_plain(path: Path) -> list[Page]:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = _strip_front_matter(text)
    sheets = text.split("\f")          # form feed = page break, when present
    if len(sheets) == 1:
        sheets = [text]
    return [Page(source=path.name, page=i, text=sheet) for i, sheet in enumerate(sheets, 1)]


def _strip_front_matter(text: str) -> str:
    """Drop a leading `--- key: value ---` block: it is metadata (run.py reads
    doc_version and superseded from it), not content worth embedding."""
    if not text.startswith("---"):
        return text
    lines = text.splitlines()
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    return text
