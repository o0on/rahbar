"""Paste the captured terminal screenshots into the report Appendix.

    .venv/Scripts/python.exe report/paste_screenshots.py

Each PNG replaces the `[ PASTE UNCROPPED TERMINAL SCREENSHOT HERE ]` line
immediately below its matching `A1 — ...`, `D2 — ...` heading. The script is
idempotent: running it again regenerates the images from report/screenshots/.
Aun's placeholders are left untouched until his PNGs exist.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.shared import Inches

ROOT = Path(__file__).resolve().parents[1]
DOCXS = [ROOT / "report" / "A3-Report-Template.docx", ROOT / "report" / "A3-Report.docx"]
SHOTS = ROOT / "report" / "screenshots"
PLACEHOLDER = "[ PASTE UNCROPPED TERMINAL SCREENSHOT HERE ]"
WIDTH = Inches(6.6)


def main() -> None:
    for docx_path in DOCXS:
        if not docx_path.exists():
            continue
        doc = Document(docx_path)
        paragraphs = doc.paragraphs
        pasted: list[str] = []

        for i, para in enumerate(paragraphs):
            has_placeholder = PLACEHOLDER in para.text
            has_blip = bool(para._element.xpath('.//*[local-name()="blip"]'))
            if not (has_placeholder or has_blip):
                continue
            # The ID heading is the closest earlier bold paragraph like "A1 — ...".
            shot_id = None
            for earlier in reversed(paragraphs[:i]):
                text = earlier.text.strip()
                if not text:
                    continue
                match = re.match(r"^([^\s]+) —", text)
                if match:
                    shot_id = match.group(1)
                    break
            if not shot_id:
                continue

            png = SHOTS / f"{shot_id}.png"
            if not png.exists():
                continue

            para.clear()
            para.add_run().add_picture(str(png), width=WIDTH)
            pasted.append(shot_id)

        doc.save(docx_path)
        print(f"{docx_path.name}: embedded {len(pasted)} screenshots: {', '.join(pasted)}")
    available = sorted(p.stem for p in SHOTS.glob("*.png"))
    print(f"available PNGs: {', '.join(available) or 'none'}")


if __name__ == "__main__":
    main()
