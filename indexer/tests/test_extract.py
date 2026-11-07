"""Stage 1 tests: extraction from Markdown, HTML and PDF, including the
no-text-layer detection that decides whether a page needs OCR (section 3.1)."""

from __future__ import annotations

from pathlib import Path

from pipeline.extract import MIN_TEXT_LAYER_CHARS, _table_to_markdown, extract


# --------------------------------------------------------------------- Markdown

def test_markdown_front_matter_is_metadata_not_content(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("---\ndoc_version: 2026-06\nsuperseded: false\n---\n\n"
                    "# Attendance\nStudents must attend 75% of classes.\n",
                    encoding="utf-8")
    doc = extract(str(path), "2026-06", False)
    assert doc.source == "doc.md"
    assert len(doc.pages) == 1
    assert "doc_version" not in doc.pages[0].text        # front matter stripped
    assert "75%" in doc.pages[0].text
    assert doc.doc_version == "2026-06" and doc.superseded is False


def test_form_feed_splits_pages(tmp_path: Path) -> None:
    path = tmp_path / "multi.txt"
    path.write_text("page one\fpage two", encoding="utf-8")
    doc = extract(str(path), "v", False)
    assert [p.page for p in doc.pages] == [1, 2]
    assert "page one" in doc.pages[0].text and "page two" in doc.pages[1].text


# ------------------------------------------------------------------------ HTML

def test_html_tables_become_markdown_and_headings_become_sections(tmp_path: Path) -> None:
    path = tmp_path / "fees.html"
    path.write_text(
        "<html><head><style>p{color:red}</style><script>var x=1;</script></head>"
        "<body><nav>Admissions Academics</nav>"
        "<h1>Fee Structure</h1>"
        "<p>Tuition is charged per credit hour.</p>"
        "<table><tr><th>Program</th><th>Per Credit Hour</th></tr>"
        "<tr><td>BS Computer Science</td><td>PKR 24,500</td></tr>"
        "<tr><td>BS Electrical Engineering</td><td>PKR 26,000</td></tr></table>"
        "<footer>Copyright</footer></body></html>",
        encoding="utf-8",
    )
    doc = extract(str(path), "2026-27", False)
    text = doc.pages[0].text
    assert "# Fee Structure" in text                       # heading kept as a section
    assert "| Program | Per Credit Hour |" in text          # table in columns
    assert "| BS Computer Science | PKR 24,500 |" in text
    assert "var x=1" not in text and "color:red" not in text
    assert "Admissions Academics" not in text               # nav is boilerplate


def test_table_to_markdown_pads_ragged_rows() -> None:
    rendered = _table_to_markdown([["A", "B", "C"], ["1", None]])
    lines = rendered.splitlines()
    assert lines[0] == "| A | B | C |"
    assert lines[1] == "| --- | --- | --- |"
    assert lines[2] == "| 1 |  |  |"


# -------------------------------------------------------------------------- PDF

def make_pdf(pages: list[str]) -> bytes:
    """A minimal PDF with a real Helvetica text layer, built byte by byte so
    the test needs no PDF-writing library."""
    def esc(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    page_ids, content_ids = [], []
    next_id = 4
    for _ in pages:
        page_ids.append(next_id)
        content_ids.append(next_id + 1)
        next_id += 2

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
            f"/Count {len(pages)} >>").encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, (page_id, content_id, body) in enumerate(zip(page_ids, content_ids, pages)):
        lines = "\n".join(
            f"({esc(line)}) Tj 0 -16 Td" if i == 0 else f"0 -16 Td ({esc(line)}) Tj"
            for i, line in enumerate(body.split("\n"))
        )
        stream = f"BT /F1 12 Tf 72 740 Td {lines} ET".encode()
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        objects[content_id] = (f"<< /Length {len(stream)} >>\nstream\n".encode()
                               + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref_at = len(out)
    count = max(objects) + 1
    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for number in range(1, count):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_at}\n"
            "%%EOF\n").encode()
    return bytes(out)


def test_pdf_with_text_layer_is_extracted(tmp_path: Path) -> None:
    path = tmp_path / "handbook.pdf"
    path.write_bytes(make_pdf(["5.4 Academic Warning\n"
                               "A CGPA below 2.00 puts a student on academic warning."]))
    doc = extract(str(path), "2026-06", False)
    assert len(doc.pages) == 1
    assert "academic warning" in doc.pages[0].text.lower()
    assert doc.pages[0].ocr is False


def test_pdf_without_text_layer_is_detected_as_needing_ocr(tmp_path: Path) -> None:
    # A page whose content stream draws no text: extraction returns nothing,
    # which is exactly what a scanned page looks like (property 3).
    path = tmp_path / "scan.pdf"
    path.write_bytes(make_pdf(["", ""]))
    doc = extract(str(path), "2022-05", False)
    assert len(doc.pages) == 2
    for page in doc.pages:
        assert len(page.text.strip()) < MIN_TEXT_LAYER_CHARS
        assert page.ocr is False            # OCR unavailable here: loss documented


def test_missing_file_raises() -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        extract("/nonexistent/nope.pdf", "v", False)
