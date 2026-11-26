"""The shapes passed between stages. Metadata fields are required (section 3.1)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Page:
    """Text of one page, as extraction produced it."""

    source: str              # file name, e.g. Academic_Rules_Handbook_2026.pdf
    page: int                # 1-based
    text: str
    ocr: bool = False        # True when this page had no text layer and was OCR'd


@dataclass
class Document:
    """A source document plus its RAGOps identity.

    `doc_id` is the stable identity across versions: the 2026-06 handbook and
    the 2026-11 handbook share a doc_id but have different doc_version values.
    It defaults to the source name so the A2 fixtures keep working unchanged.
    """

    source: str
    doc_version: str
    superseded: bool
    pages: list[Page] = field(default_factory=list)
    doc_id: str = ""


@dataclass
class Chunk:
    id: str                  # stable: same document + same position gives the same id
    text: str
    source: str
    section: str             # the heading this chunk sits under, e.g. "5.4 Academic Warning"
    page: int
    doc_version: str
    superseded: bool
    doc_id: str = ""         # A2: empty for dummy chunks; A3: stable across versions

    def metadata(self) -> dict[str, str | int | bool]:
        """Chroma metadata values must be str, int, float or bool."""
        meta: dict[str, str | int | bool] = {
            "source": self.source,
            "section": self.section,
            "page": self.page,
            "doc_version": self.doc_version,
            "superseded": self.superseded,
        }
        # Kept out of the A2 dummy index so the starter test's exact metadata
        # assertion stays valid; every A3 chunk carries it.
        if self.doc_id:
            meta["doc_id"] = self.doc_id
        return meta
