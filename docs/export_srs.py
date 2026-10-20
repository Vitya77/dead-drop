#!/usr/bin/env python3
"""Render the controlled SRS Markdown subset and check coverage/content parity.

Requires Python, ReportLab, pypdf, and Graphviz's dot executable. Unsupported
Markdown fails explicitly instead of silently disappearing from the PDF.
"""

from __future__ import annotations

import argparse
from collections import Counter
from html import escape, unescape
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/srs.md"
OUTPUT = ROOT / "output/pdf/dead-drop-srs.pdf"
LINK = re.compile(r"(!?)\[([^\]]+)\]\(([^)]+)\)")
REQ = re.compile(r"^### (REQ-[A-Z]+-\d{3}): (.+)$", re.M)
BLUE = colors.HexColor("#164E85")
INK = colors.HexColor("#1E293B")
LIGHT = colors.HexColor("#EAF1F8")


def slug(value: str) -> str:
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", value.lower()))


def inline(value: str) -> str:
    """Escape literal text before adding the supported inline markup."""
    def token(match: re.Match) -> str:
        raw = match.group(0)
        if raw.startswith("**"):
            return "<b>" + escape(raw[2:-2]) + "</b>"
        if raw.startswith("`"):
            return '<font name="Courier" size="8">' + escape(raw[1:-1]) + "</font>"
        link = LINK.fullmatch(raw)
        assert link and not link.group(1), raw
        label, target = link.group(2), link.group(3)
        if not target.startswith(("#", "https://", "http://")):
            target = os.path.relpath((SOURCE.parent / target).resolve(), OUTPUT.parent)
        return '<a href="' + escape(target, quote=True) + '" color="#164E85">' + escape(label) + "</a>"

    pattern = r"\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)"
    parts, previous = [], 0
    for match in re.finditer(pattern, value):
        parts.extend((escape(value[previous:match.start()]), token(match)))
        previous = match.end()
    parts.append(escape(value[previous:]))
    return "".join(parts)


def visible(value: str) -> str:
    value = LINK.sub(lambda match: match.group(2), value)
    return value.replace("**", "").replace("`", "")


def validate_source(text: str) -> tuple[int, int]:
    headings = re.findall(r"^#{1,4} (.+)$", text, re.M)
    anchors = [slug(heading) for heading in headings]
    assert len(anchors) == len(set(anchors)), "Duplicate heading anchors"
    assert re.findall(r"^## (\d+)\.", text, re.M) == [str(i) for i in range(1, 10)], "Chapter order"
    for title in ("Actors/Personas", "High-level Architecture", "Functional Requirements", "Non-functional Requirements"):
        assert any(heading.endswith(title) for heading in headings), title
    for match in LINK.finditer(text):
        target = match.group(3)
        if target.startswith("#"):
            assert target[1:] in anchors, f"Broken anchor: {target}"
        elif not target.startswith(("https://", "http://")):
            assert (SOURCE.parent / target).is_file(), f"Missing source link: {target}"
    requirements = list(REQ.finditer(text))
    ids = [match.group(1) for match in requirements]
    assert len(ids) == len(set(ids)), "Duplicate requirement ID"
    functional = sum(item.startswith("REQ-FUNC-") for item in ids)
    assert functional >= 25 and len(ids) - functional >= 12, "Requirement minimums"
    assert functional == 30 and len(ids) - functional == 15, "Update stated inventory if counts change"
    for match in requirements:
        following = text[match.end():]
        boundary = re.search(r"^#{1,4} ", following, re.M)
        block = following[:boundary.start()] if boundary else following
        for field in ("**Priority:** Must", "**Statement:**", "**Rationale:**", "**Acceptance criteria:**", "**Verification:**"):
            assert field in block, f"{match.group(1)} missing {field}"
        assert "shall" in block.split("**Rationale:**")[0], match.group(1)
        assert re.search(r"\[SC-\d{2}\]\(#", block), f"Missing verification link: {match.group(1)}"
    matrix = text.split("### 8.3 Requirement-to-scenario matrix", 1)[1].split("## 9.", 1)[0]
    rows = re.findall(r"^\| (REQ-[A-Z]+-\d{3}) \|(.+)$", matrix, re.M)
    assert Counter(item[0] for item in rows) == Counter(ids), "Traceability matrix coverage"
    assert all("| Planned | Not collected |" in row for _, row in rows), "Invented verification result"
    assert len(re.findall(r"^#### SC-\d{2}:", text, re.M)) == 20, "Scenario inventory"
    unknown_ids = set(re.findall(r"\bREQ-[A-Z]+-\d{3}\b", text)) - set(ids)
    assert not unknown_ids, f"Unknown requirement references: {unknown_ids}"
    for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", (ROOT / "README.md").read_text()):
        if not target.startswith(("https://", "http://", "#")):
            assert (ROOT / target).is_file() or (ROOT / target).resolve() == OUTPUT, f"README link: {label}"
    return functional, len(ids) - functional


def render_diagrams() -> None:
    dot = shutil.which("dot")
    if not dot:
        raise RuntimeError("Graphviz dot is required; install Graphviz before exporting")
    assets = ROOT / "docs/assets"
    assets.mkdir(exist_ok=True)
    for name in ("context", "components", "lifecycle"):
        subprocess.run([dot, "-Tpng", "-Gdpi=180", str(ROOT / "docs/diagrams" / f"{name}.dot"), "-o", str(assets / f"{name}.png")], check=True)


def make_styles() -> dict:
    styles = getSampleStyleSheet()
    options = dict(fontName="Helvetica", fontSize=9.5, leading=14, textColor=INK, spaceAfter=8)
    body = ParagraphStyle("SRSBody", **options)
    return {
        "body": body,
        "title": ParagraphStyle("SRSTitle", parent=body, fontSize=36, leading=44, textColor=BLUE, spaceBefore=50, spaceAfter=14),
        "h2": ParagraphStyle("SRSChapter", parent=body, fontSize=20, leading=25, textColor=BLUE, spaceAfter=17, keepWithNext=True),
        "h3": ParagraphStyle("SRSSection", parent=body, fontSize=12.5, leading=17, textColor=BLUE, spaceBefore=12, spaceAfter=8, keepWithNext=True),
        "h4": ParagraphStyle("SRSScenario", parent=body, fontSize=11, leading=15, textColor=BLUE, spaceBefore=10, spaceAfter=6, keepWithNext=True),
        "bullet": ParagraphStyle("SRSBullet", parent=body, leftIndent=11, firstLineIndent=0, bulletIndent=0, spaceAfter=5),
        "cell": ParagraphStyle("SRSCell", parent=body, fontSize=8, leading=11, spaceAfter=0, splitLongWords=True),
        "headcell": ParagraphStyle("SRSHeadCell", parent=body, fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=BLUE, spaceAfter=0),
        "caption": ParagraphStyle("SRSCaption", parent=body, fontSize=8, leading=11, textColor=colors.HexColor("#475569"), spaceBefore=5, spaceAfter=12),
    }


class SRSDocument(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and hasattr(flowable, "srs_anchor"):
            self.canv.bookmarkPage(flowable.srs_anchor)
            level = min(flowable.srs_level - 1, 2)
            # Only chapter bookmarks: requirement anchors remain clickable in-body.
            if level == 1:
                self.canv.addOutlineEntry(flowable.getPlainText(), flowable.srs_anchor, level=0)


def page_decoration(canvas, doc):
    width, height = A4
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.setLineWidth(0.5)
    canvas.line(48, height - 40, width - 48, height - 40)
    canvas.setFillColor(BLUE)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(48, height - 30, "DEAD DROP  /  SOFTWARE REQUIREMENTS SPECIFICATION")
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(48, 27, "Version 0.1  |  2026-10-20  |  Course MVP")
    canvas.drawRightString(width - 48, 27, str(doc.page))
    canvas.restoreState()


def table_widths(header: list[str], available: float) -> list[float]:
    if header[0] == "Requirement":
        weights = [.18, .18, .39, .10, .15]
    elif header[0] == "Action":
        weights = [.28, .16, .18, .18, .20]
    elif len(header) == 2:
        weights = [.25, .75]
    elif len(header) == 3:
        weights = [.26, .34, .40]
    elif header[0] == "Version":
        weights = [.09, .16, .19, .56]
    else:
        weights = [1 / len(header)] * len(header)
    assert len(weights) == len(header), header
    return [available * item for item in weights]


def build_pdf(text: str) -> list[str]:
    styles = make_styles()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SRSDocument(str(OUTPUT), pagesize=A4, leftMargin=48, rightMargin=48,
                      topMargin=58, bottomMargin=48, title="Dead Drop - Software Requirements Specification",
                      author="Dead Drop Team", subject="Course MVP SRS, version 0.1")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="SRS", frames=frame, onPage=page_decoration))
    story, fragments = [], []
    lines, index = text.splitlines(), 0

    def paragraph(value, style="body", **kwargs):
        fragments.append(visible(value))
        item = Paragraph(inline(value), styles[style], **kwargs)
        story.append(item)
        return item

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = re.fullmatch(r"(#{1,4}) (.+)", line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            if level == 2 and (title == "Contents" or re.match(r"\d+\.", title)):
                story.append(PageBreak())
            item = paragraph(title, "title" if level == 1 else f"h{level}")
            item.srs_anchor, item.srs_level = slug(title), level
            index += 1
        elif line.startswith("!["):
            match = LINK.fullmatch(line)
            if not match or not match.group(1):
                raise ValueError(f"Unsupported image: {line}")
            path = SOURCE.parent / match.group(3)
            width, height = ImageReader(str(path)).getSize()
            scale = min(doc.width / width, (doc.height - 85) / height)
            story.append(Image(str(path), width=width * scale, height=height * scale, hAlign="CENTER"))
            paragraph(match.group(2), "caption")
            index += 1
        elif line.startswith("| "):
            rows = []
            while index < len(lines) and lines[index].startswith("| "):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                    rows.append(cells)
                index += 1
            assert all(len(row) == len(rows[0]) for row in rows), "Uneven table"
            data = []
            for row_index, row in enumerate(rows):
                fragments.extend(visible(cell) for cell in row)
                style = styles["headcell" if row_index == 0 else "cell"]
                data.append([Paragraph(inline(cell), style) for cell in row])
            table = Table(data, colWidths=table_widths(rows[0], doc.width), repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("LINEBELOW", (0, 0), (-1, 0), .7, colors.HexColor("#94A3B8")),
                ("LINEBELOW", (0, 1), (-1, -1), .25, colors.HexColor("#E2E8F0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.extend((table, Spacer(1, 10)))
        elif line.startswith("- "):
            paragraph(line[2:], "bullet", bulletText="\u2022")
            index += 1
        elif re.match(r"\s+[-*] |```|>|\d+\. ", line):
            raise ValueError(f"Unsupported Markdown block: {line}")
        else:
            parts = []
            while index < len(lines) and lines[index].strip():
                current = lines[index]
                if re.match(r"#{1,4} |\| |!\[|- ", current):
                    break
                parts.append(current.rstrip())
                index += 1
            if not parts:
                raise ValueError(f"Unsupported Markdown: {line}")
            # Metadata is authored as hard-break lines, retained as separate paragraphs.
            if len(parts) > 1 and all(item.startswith("**") for item in parts):
                for part in parts:
                    paragraph(part)
            else:
                paragraph(" ".join(parts))
    # A requirement is short enough to fit on one page. Keep its statement,
    # acceptance criteria, and verification together instead of leaving widows.
    grouped, requirement = [], []
    for item in story:
        is_heading = isinstance(item, Paragraph) and hasattr(item, "srs_anchor")
        if requirement and (is_heading or isinstance(item, PageBreak)):
            grouped.append(KeepTogether(requirement))
            requirement = []
        if is_heading and item.getPlainText().startswith("REQ-"):
            requirement = [item]
        elif requirement:
            requirement.append(item)
        else:
            grouped.append(item)
    if requirement:
        grouped.append(KeepTogether(requirement))
    doc.build(grouped)
    return fragments


def validate_pdf(fragments: list[str]) -> int:
    reader = PdfReader(OUTPUT)
    # Canvas furniture is extracted first on each page. Remove it so a source
    # paragraph split over a page boundary can still be compared contiguously.
    bodies = []
    for number, page in enumerate(reader.pages, start=1):
        lines = (page.extract_text() or "").splitlines()
        assert lines[:3] == ["DEAD DROP  /  SOFTWARE REQUIREMENTS SPECIFICATION",
                            "Version 0.1  |  2026-10-20  |  Course MVP", str(number)], "Page furniture mismatch"
        bodies.append("\n".join(lines[3:]))
    text = "\n".join(bodies)
    compact = lambda value: re.sub(r"\s+", "", unescape(value))
    normalized = compact(text)
    missing = [fragment for fragment in fragments if compact(fragment) not in normalized]
    assert not missing, "PDF lost source text: " + repr(missing[:5])
    assert all(page.extract_text().strip() for page in reader.pages), "Empty page"
    assert len(re.findall(r"REQ-FUNC-\d{3}:", text)) == 30, "PDF functional inventory"
    assert len(re.findall(r"REQ-(?!FUNC)[A-Z]+-\d{3}:", text)) == 15, "PDF non-functional inventory"
    image_count = sum(len(page.images) for page in reader.pages)
    assert image_count == 3, f"Expected three diagram placements, found {image_count}"
    uris = {str(annotation.get_object().get("/A", {}).get("/URI", ""))
            for page in reader.pages for annotation in page.get("/Annots", [])}
    urls = {match.group(3) for match in LINK.finditer(SOURCE.read_text()) if match.group(3).startswith("https://")}
    assert urls <= uris, f"Missing PDF source URLs: {urls - uris}"
    return len(reader.pages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate source and existing PDF without regenerating artifacts")
    args = parser.parse_args()
    if not args.check:
        render_diagrams()
    text = SOURCE.read_text()
    functional, nonfunctional = validate_source(text)
    if args.check:
        assert OUTPUT.is_file(), "PDF missing; run export first"
        # Text parity is fully checked during export; check mode checks inventory.
        pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(OUTPUT).pages)
        assert len(re.findall(r"REQ-FUNC-\d{3}:", pdf_text)) == functional
        assert len(re.findall(r"REQ-(?!FUNC)[A-Z]+-\d{3}:", pdf_text)) == nonfunctional
        pages = len(PdfReader(OUTPUT).pages)
    else:
        fragments = build_pdf(text)
        pages = validate_pdf(fragments)
    print(f"Validated {functional} functional requirements, {nonfunctional} non-functional requirements, 20 scenarios, all source anchors and traceability rows.")
    print(f"PDF: {OUTPUT.relative_to(ROOT)} ({pages} pages).")
    if not args.check:
        print("Source/PDF text, external-link, and three-diagram parity checks passed. Visual page inspection is still required.")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, RuntimeError, ValueError) as error:
        sys.exit(f"SRS export failed: {error}")
