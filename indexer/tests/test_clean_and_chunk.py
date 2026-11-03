"""Stage 2 and 3 tests: cleaning rules, and the structure-aware chunker's
promises - section boundaries, size, overlap, stable ids and metadata."""

from __future__ import annotations

from pipeline.chunk import _heading, _token_count, chunk, tokens
from pipeline.clean import clean
from pipeline.models import Document, Page


def make_doc(pages: list[str], **kwargs: object) -> Document:
    return Document(source="Academic_Rules_Handbook_2026.pdf",
                    doc_version=str(kwargs.get("doc_version", "2026-06")),
                    superseded=bool(kwargs.get("superseded", False)),
                    pages=[Page(source="Academic_Rules_Handbook_2026.pdf",
                                page=i + 1, text=text) for i, text in enumerate(pages)])


# ------------------------------------------------------------------------ clean

def test_repeating_headers_and_footers_are_removed() -> None:
    header = "FAST-NUCES Student Handbook"
    # Each page discusses a different topic: only the header and the page
    # number repeat across pages, so only they may be deleted.
    topics = ["attendance", "grading", "examinations", "student conduct"]
    pages = []
    for i, topic in enumerate(topics, start=1):
        body = "\n".join(
            f"The {topic} policy states that rule {n} applies to every student."
            for n in range(1, 6)
        )
        pages.append(f"{header}\n\n{body}\n\nPage {i} of 60")
    cleaned = clean(make_doc(pages))
    for page, topic in zip(cleaned.pages, topics):
        assert header not in page.text             # running header gone
        assert "Page" not in page.text             # page-number footer gone
        assert f"The {topic} policy" in page.text  # the content survives


def test_control_characters_and_blank_runs_are_collapsed() -> None:
    doc = make_doc(["line\x00one​\r\n\r\n\r\n\r\n\r\nline two   spaced"])
    text = clean(doc).pages[0].text
    assert "\x00" not in text and "​" not in text
    assert "\n\n\n" not in text
    assert "line one" in text and "line two spaced" in text


def test_cleaning_keeps_clause_numbering_and_table_pipes() -> None:
    doc = make_doc(["5.4 Academic Warning\n\nA CGPA below 2.00 triggers it.\n\n"
                    "| Year | Graduates |\n| --- | --- |\n| 2024 | 1,204 |"])
    text = clean(doc).pages[0].text
    assert "5.4" in text                            # clause numbers are structure
    assert "| Year | Graduates |" in text            # table rows stay rows


def test_short_documents_have_no_header_detection() -> None:
    doc = make_doc(["only one page here"])
    assert clean(doc).pages[0].text.strip() == "only one page here"


# ------------------------------------------------------------------------ chunk

def test_heading_detection_patterns() -> None:
    assert _heading("5.4 Academic Warning") == "5.4 Academic Warning"
    assert _heading("# Attendance") == "Attendance"
    assert _heading("GENERATIVE AI POLICY") == "GENERATIVE AI POLICY"
    assert _heading("1. Attendance") == "1. Attendance"
    assert _heading("A normal sentence that ends with a period.") is None
    assert _heading("") is None


def test_chunks_carry_required_metadata_and_stable_ids() -> None:
    doc = make_doc(["# Attendance\n\nStudents must attend 75% of classes.\n\n"
                    "A shortage leads to an F grade.\n\n# Grading\n\n"
                    "Grade points run from 4.00 to 0.00."],
                   doc_version="2026-06", superseded=True)
    chunks = chunk(doc, size=50, overlap=10)
    assert chunks
    for piece in chunks:
        assert piece.source == "Academic_Rules_Handbook_2026.pdf"
        assert piece.doc_version == "2026-06"
        assert piece.superseded is True
        assert piece.section and piece.page == 1
        assert piece.id.startswith("academic-rules-handbook-2026-p001-")
    assert chunk(doc, size=50, overlap=10)[0].id == chunks[0].id      # stable


def test_chunks_never_straddle_sections() -> None:
    doc = make_doc(["# Attendance\n\n" + "Attendance rule text. " * 40 +
                    "\n\n# Grading\n\n" + "Grade rule text. " * 40])
    chunks = chunk(doc, size=40, overlap=10)
    sections = {c.section for c in chunks}
    assert sections == {"Attendance", "Grading"}
    first_of_section: dict[str, object] = {}
    for piece in chunks:
        first_of_section.setdefault(piece.section, piece)
    for section, piece in first_of_section.items():
        # the chunk that opens a section carries its heading, so a citation
        # naming that section points at text the reader can verify
        assert section.split()[0] in piece.text


def test_chunk_size_is_respected() -> None:
    body = "\n\n".join(f"Sentence number {i} carries a little weight." for i in range(60))
    doc = make_doc(["# Rules\n\n" + body])
    for size in (25, 60, 120):
        for piece in chunk(doc, size=size, overlap=5):
            assert _token_count(piece.text) <= size, f"{_token_count(piece.text)} > {size}"


def test_overlap_repeats_tokens_between_neighbours() -> None:
    body = "\n\n".join(f"Sentence number {i} is here to be measured." for i in range(40))
    doc = make_doc(["# Rules\n\n" + body])
    chunks = chunk(doc, size=40, overlap=12)
    assert len(chunks) >= 3
    first, second = chunks[0], chunks[1]
    tail = tokens(first.text)[-12:]
    head = set(tokens(second.text)[:20])
    assert any(token in head for token in tail), "neighbours must share context"


def test_long_definitions_are_split_without_losing_text() -> None:
    definition = ("Academic probation means " + "that the student must improve " * 80)
    doc = make_doc(["# Definitions\n\n" + definition])
    pieces = chunk(doc, size=50, overlap=10)
    assert len(pieces) >= 3
    for piece in pieces:
        assert _token_count(piece.text) <= 50
    joined = " ".join(p.text for p in pieces)
    for word in ("probation", "improve", "student"):
        assert word in joined


def test_tables_split_between_rows_not_inside_them() -> None:
    rows = "\n".join(f"| 202{i} | Student {i} | Faculty {i} |" for i in range(60))
    doc = make_doc(["# Annual Report\n\n| Year | Students | Faculty |\n"
                    "| --- | --- | --- |\n" + rows])
    pieces = chunk(doc, size=60, overlap=0)
    assert len(pieces) > 1
    for piece in pieces:
        for line in piece.text.splitlines():
            if line.startswith("|"):
                assert line.count("|") == 4          # a whole row, columns intact


def test_tokens_counts_words_and_punctuation() -> None:
    assert tokens("CGPA below 2.00!") == ["CGPA", "below", "2", ".", "00", "!"]
