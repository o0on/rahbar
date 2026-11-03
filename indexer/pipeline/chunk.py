"""Stage 3: Chunk. Structure-aware, with overlap.

The unit of splitting, in order (section 3.1):

1. sections   - a heading line starts a new section and flushes the old one;
                chunks never straddle two sections, because a citation names
                the section a chunk sits under.
2. paragraphs - a blank line ends a paragraph.
3. sentences  - sentences are packed into chunks up to `size` tokens, and each
                chunk after the first repeats up to `overlap` tokens from its
                neighbour so a definition cut at the boundary is still findable.

A "token" here is one regex token (\\w+ or a single non-space character): a
deliberate, dependency-free count, so chunking needs no tokenizer model. The
three measured (size, overlap) configurations live in docs/PIPELINE.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Chunk, Document

TOKEN_RE = re.compile(r"\w+|[^\w\s]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
HEADING_MD = re.compile(r"^#{1,6}\s+(.+)$")
HEADING_NUMBERED = re.compile(r"^(\d+(?:\.\d+)+|\d+\.)\s+([A-Z].+)$")
HEADING_ALLCAPS = re.compile(r"^([A-Z][A-Z0-9 &/,:()'-]{2,})$")
DEFAULT_SECTION = "(preamble)"
MAX_HEADING_WORDS = 14
MAX_HEADING_CHARS = 110

Unit = tuple[str, str, int, bool]        # (text, section, page, is_table)


def tokens(text: str) -> list[str]:
    """The token sequence this chunker counts and splits on."""
    return TOKEN_RE.findall(text)


def _token_count(text: str) -> int:
    return len(TOKEN_RE.findall(text))


def _heading(line: str) -> str | None:
    """Return the section title when this line is a heading, else None."""
    s = line.strip()
    if not s or len(s) > MAX_HEADING_CHARS:
        return None
    m = HEADING_MD.match(s)
    if m:
        return " ".join(m.group(1).split())
    m = HEADING_NUMBERED.match(s)
    if m:
        title = " ".join(s.split())
        if not title.endswith((".", ",", ";")) and len(title.split()) <= MAX_HEADING_WORDS:
            return title
        return None
    if HEADING_ALLCAPS.match(s) and len(s.split()) <= MAX_HEADING_WORDS:
        return " ".join(s.split())
    return None


def _paragraphs(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def _sentences(paragraph: str) -> list[str]:
    """Join hard-wrapped lines into prose, keeping Markdown table rows intact."""
    groups: list[tuple[bool, str]] = []          # (is_table, text)
    prose: list[str] = []
    table_rows: list[str] = []
    for line in (ln.strip() for ln in paragraph.splitlines()):
        if line.startswith("|"):
            if prose:
                groups.append((False, " ".join(" ".join(prose).split())))
                prose = []
            table_rows.append(line)
        else:
            if table_rows:
                groups.append((True, "\n".join(table_rows)))
                table_rows = []
            prose.append(line)
    if prose:
        groups.append((False, " ".join(" ".join(prose).split())))
    if table_rows:
        groups.append((True, "\n".join(table_rows)))

    out: list[str] = []
    for is_table, text in groups:
        if is_table:
            out.append(text)
            continue
        out.extend(s for s in SENTENCE_END.split(text) if s.strip())
    return [s.strip() for s in out if s.strip()]


def _segments(doc: Document) -> list[Unit]:
    """Sentence-level units in reading order, each tagged with its section."""
    units: list[Unit] = []
    section = DEFAULT_SECTION
    for page in doc.pages:
        for paragraph in _paragraphs(page.text):
            lines = paragraph.splitlines()
            heading = _heading(lines[0])
            if heading:
                section = heading
            for sentence in _sentences(paragraph):
                if sentence:
                    units.append((sentence, section, page.page, sentence.startswith("|")))
    return units


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    """One prose sentence heavier than `size`: cut it on word boundaries, but
    count the pieces in tokens so none of them exceeds the limit either."""
    words = text.split()
    if not words:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for word in words:
        word_tokens = _token_count(word)
        if current and current_tokens + word_tokens > size:
            pieces.append(" ".join(current))
            keep: list[str] = []
            kept = 0
            for previous in reversed(current):          # seed the overlap
                if kept + _token_count(previous) > min(overlap, size - 1):
                    break
                keep.insert(0, previous)
                kept += _token_count(previous)
            current, current_tokens = keep, kept
        current.append(word)
        current_tokens += word_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _split_oversized_table(text: str, size: int) -> list[str]:
    """A table heavier than `size`: split between rows, never inside one, so
    columns stay in their columns."""
    lines = text.splitlines()
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for line in lines:
        line_tokens = _token_count(line)
        if current and current_tokens + line_tokens > size:
            pieces.append("\n".join(current))
            current, current_tokens = [], 0
        if line_tokens > size:                 # one enormous row: last resort
            if current:
                pieces.append("\n".join(current))
                current, current_tokens = [], 0
            pieces.extend(_hard_split(line, size, 0))
            continue
        current.append(line)
        current_tokens += line_tokens
    if current:
        pieces.append("\n".join(current))
    return pieces


def _render(group: list[Unit]) -> str:
    """Tables keep their line breaks; prose runs on with single spaces."""
    parts: list[str] = []
    for text, _, _, is_table in group:
        if not parts:
            parts.append(text)
        elif is_table or parts[-1].startswith("|"):
            parts.append("\n" + text)
        else:
            parts.append(" " + text)
    return "".join(parts).strip()


def _pack(units: list[Unit], size: int, overlap: int) -> list[tuple[str, int]]:
    """Pack one section's units into (text, page) chunks under `size` tokens."""
    weights = [_token_count(text) for text, _, _, _ in units]
    out: list[tuple[str, int]] = []
    i, n = 0, len(units)
    while i < n:
        j, tok = i, 0
        while j < n and tok + weights[j] <= size:
            tok += weights[j]
            j += 1
        if j == i:                             # a single unit heavier than `size`
            text, _, page, is_table = units[i]
            pieces = _split_oversized_table(text, size) if is_table \
                else _hard_split(text, size, overlap)
            out.extend((piece, page) for piece in pieces)
            i += 1
            continue
        out.append((_render(units[i:j]), units[i][2]))
        k, back = j, 0                         # seed the next chunk with overlap
        while k - 1 > i and back + weights[k - 1] <= overlap:
            k -= 1
            back += weights[k - 1]
        i = k
    return out


def _chunk_id(source: str, page: int, index: int) -> str:
    """Stable: the same document at the same position gives the same id."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", Path(source).stem).strip("-").lower()
    return f"{stem}-p{page:03d}-{index:04d}"


def _emit(doc: Document, run: list[Unit], section: str, size: int, overlap: int,
          index: int) -> tuple[list[Chunk], int]:
    chunks: list[Chunk] = []
    for piece, piece_page in _pack(run, size, overlap):
        chunks.append(Chunk(
            id=_chunk_id(doc.source, piece_page, index),
            text=piece,
            source=doc.source,
            section=section,
            page=piece_page,
            doc_version=doc.doc_version,
            superseded=doc.superseded,
        ))
        index += 1
    return chunks, index


def chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Split on sections, then paragraphs, then sentences, under `size` tokens,
    with `overlap` tokens shared between neighbours. Every chunk keeps the
    section heading it sits under, its page, doc_version and superseded."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    overlap = max(0, min(overlap, size - 1))

    chunks: list[Chunk] = []
    index = 0
    run: list[Unit] = []
    run_section = DEFAULT_SECTION
    for unit in _segments(doc):
        text, section, _, _ = unit
        if section != run_section and run:
            emitted, index = _emit(doc, run, run_section, size, overlap, index)
            chunks.extend(emitted)
            run = []
        run_section = section
        run.append(unit)
    if run:
        chunks.extend(_emit(doc, run, run_section, size, overlap, index)[0])
    return chunks
