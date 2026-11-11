
"""Build the corporate blog .docx from the audited markdown."""

from __future__ import annotations

import os
import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
MD = ROOT / os.environ.get("BLOG_MD", "eval-harness-blog.md")
OUT = ROOT / os.environ.get("BLOG_OUT", "Eval-Harness-Blog.docx")
FIG = ROOT / "architecture.png"

NAVY = RGBColor(0x16, 0x32, 0x4F)
TEAL = RGBColor(0x2F, 0x6F, 0x62)
INK = RGBColor(0x1B, 0x27, 0x33)
MUTED = RGBColor(0x5B, 0x6B, 0x7B)
RULE = "C9D4DE"
TICK = chr(96)


def shade(run, fill: str) -> None:
    rpr = run._element.get_or_add_rPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    rpr.append(shd)


def bottom_rule(paragraph, color: str = RULE, size: int = 6) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "8")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    ppr.append(pbdr)


def add_page_field(paragraph) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    num = OxmlElement("w:t")
    num.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(sep)
    run._r.append(num)
    run._r.append(end)


def add_runs(paragraph, text: str, size: float = 11, color: RGBColor = INK,
             italic: bool = False, bold: bool = False) -> None:
    pattern = re.compile(
        "(" + TICK + "[^" + TICK + "]+" + TICK + r"|\*\*[^*]+\*\*|\*[^*]+\*)"
    )
    for token in pattern.split(text):
        if not token:
            continue
        run = paragraph.add_run()
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.italic = italic
        run.bold = bold
        if token.startswith(TICK) and token.endswith(TICK):
            run.text = token[1:-1]
            run.font.name = "Consolas"
            run.font.size = Pt(size - 1.0)
            run.font.color.rgb = RGBColor(0x2A, 0x4A, 0x66)
            shade(run, "EDF1F4")
        elif token.startswith("**") and token.endswith("**"):
            run.text = token[2:-2]
            run.bold = True
        elif token.startswith("*") and token.endswith("*"):
            run.text = token[1:-1]
            run.italic = True
        else:
            run.text = token


def body_paragraph(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(7)
    p.paragraph_format.line_spacing = 1.16
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    add_runs(p, text)


def bullet(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.32)
    p.paragraph_format.first_line_indent = Inches(-0.19)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.12
    mark = p.add_run("\u2022  ")
    mark.font.name = "Calibri"
    mark.font.bold = True
    mark.font.color.rgb = TEAL
    add_runs(p, text)


def add_hyperlink(paragraph, url: str, text: str) -> None:
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run_el = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), "Calibri")
    fonts.set(qn("w:hAnsi"), "Calibri")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "2F6F62")
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), "19")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(fonts)
    rpr.append(color)
    rpr.append(size)
    rpr.append(underline)
    run_el.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    run_el.append(t)
    link.append(run_el)
    paragraph._p.append(link)


def heading(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(17)
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.font.name = "Calibri"
    run.font.size = Pt(15)
    run.font.bold = True
    run.font.color.rgb = NAVY



def style_header(section, tab_width: float = 6.5) -> None:
    """Add the running header to one section."""
    section.header.is_linked_to_previous = False
    hp = section.header.paragraphs[0]
    hp.clear()
    hp.paragraph_format.tab_stops.clear_all()
    hp.paragraph_format.tab_stops.add_tab_stop(
        Inches(tab_width), WD_TAB_ALIGNMENT.RIGHT
    )
    left = hp.add_run("EVAL HARNESS")
    left.font.name = "Calibri"
    left.font.size = Pt(7.5)
    left.font.bold = True
    left.font.color.rgb = MUTED
    hp.add_run("\t")
    right = hp.add_run("ENGINEERING BLOG")
    right.font.name = "Calibri"
    right.font.size = Pt(7.5)
    right.font.color.rgb = MUTED
    bottom_rule(hp, color=RULE, size=4)


def style_footer(section) -> None:
    """Add the centered page number to one section."""
    section.footer.is_linked_to_previous = False
    fp = section.footer.paragraphs[0]
    fp.clear()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_page_field(fp)
    for run in fp.runs:
        run.font.name = "Calibri"
        run.font.size = Pt(8)
        run.font.color.rgb = MUTED


def portrait(section) -> None:
    """Apply the portrait page geometry used by the body."""
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    section.top_margin = Inches(0.9)
    section.bottom_margin = Inches(0.85)
    section.header_distance = Inches(0.45)
    section.footer_distance = Inches(0.45)


def landscape(section) -> None:
    """Apply the full-width geometry used by the architecture figure."""
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Inches(11)
    section.page_height = Inches(8.5)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.header_distance = Inches(0.4)
    section.footer_distance = Inches(0.4)



def cell_shade(cell, fill: str) -> None:
    """Fill one table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.insert_element_before(
        shd, "w:noWrap", "w:tcMar", "w:textDirection",
        "w:tcFitText", "w:vAlign", "w:hideMark"
    )


def table_borders(table, color: str = RULE, size: int = 4) -> None:
    """Draw thin rules around and inside the table."""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tbl_pr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar",
        "w:tblLook", "w:tblCaption", "w:tblDescription"
    )


def cell_margins(cell, top: int = 70, bottom: int = 70,
                 left: int = 110, right: int = 110) -> None:
    """Set cell padding in twentieths of a point."""
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for side, value in (("top", top), ("left", left),
                        ("bottom", bottom), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tc_pr.insert_element_before(
        mar, "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"
    )


def add_table(doc, lines: list[str]) -> None:
    """Render one pipe table as a real Word table."""
    rows = []
    for raw in lines:
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if all(set(c) <= {"-", ":"} and c for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return

    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    if len(rows[0]) == 4:
        widths = [Inches(1.5), Inches(1.8), Inches(1.75), Inches(1.45)]
    else:
        widths = [Inches(1.2), Inches(1.05), Inches(1.05), Inches(1.0), Inches(2.2)]
    table_borders(table)

    for r_idx, row in enumerate(rows):
        for c_idx, value in enumerate(row[: len(widths)]):
            cell = table.cell(r_idx, c_idx)
            cell.width = widths[c_idx]
            cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            p.alignment = (
                WD_ALIGN_PARAGRAPH.LEFT if c_idx < (2 if len(rows[0]) == 4 else 1)
                else WD_ALIGN_PARAGRAPH.RIGHT
            )
            run = p.add_run(value)
            run.font.name = "Calibri"
            run.font.size = Pt(9)
            if r_idx == 0:
                run.font.bold = True
                run.font.size = Pt(8.5)
                run.font.color.rgb = NAVY
                cell_shade(cell, "EAF1F8")
            else:
                run.font.color.rgb = INK
                if r_idx % 2 == 0:
                    cell_shade(cell, "F7FAFC")

    for c_idx, column in enumerate(table.columns):
        column.width = widths[c_idx]

    gap = doc.add_paragraph()
    gap.paragraph_format.space_before = Pt(0)
    gap.paragraph_format.space_after = Pt(0)
    gap.paragraph_format.line_spacing = 0.5


def add_code_block(doc, lines: list[str]) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after = Pt(11)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    ppr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F4F7FA")
    ppr.append(shd)
    pbdr = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "6")
        el.set(qn("w:color"), "D8E0E8")
        pbdr.append(el)
    ppr.append(pbdr)
    for i, text in enumerate(lines):
        run = p.add_run(text)
        run.font.name = "Consolas"
        run.font.size = Pt(8)
        run.font.color.rgb = INK
        if i < len(lines) - 1:
            run.add_break()


def build() -> None:
    doc = Document()
    section = doc.sections[0]
    portrait(section)
    doc_title = "Your Demo Is Not Evidence"

    normal = doc.styles["Normal"]
    normal.font.name = "Georgia"
    normal.font.size = Pt(11)
    normal.font.color.rgb = INK
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Georgia")

    style_header(section)
    style_footer(section)

    lines = MD.read_text(encoding="utf-8").splitlines()
    title_done = False
    standfirst_done = False
    pending_caption = False
    table_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if in_code:
            if line == "~~~":
                add_code_block(doc, code_lines)
                in_code = False
            else:
                code_lines.append(line)
            continue

        if line == "~~~text":
            in_code = True
            code_lines = []
            continue

        if line.startswith("|"):
            table_lines.append(line)
            continue

        if table_lines:
            add_table(doc, table_lines)
            table_lines = []

        if line.startswith("# ") and not title_done:
            kicker = doc.add_paragraph()
            kicker.paragraph_format.space_before = Pt(4)
            kicker.paragraph_format.space_after = Pt(6)
            kr = kicker.add_run("ENGINEERING")
            kr.font.name = "Calibri"
            kr.font.size = Pt(7.5)
            kr.font.bold = True
            kr.font.color.rgb = TEAL

            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 0.98
            title_text = line[2:].strip()
            if title_text == "Two Arms, One Score, Opposite Failures":
                title_text = "Two Arms, One Score,\nOpposite Failures"
            title_run = p.add_run(title_text)
            title_run.font.name = "Georgia"
            title_run.font.size = Pt(27)
            title_run.font.bold = True
            title_run.font.color.rgb = NAVY
            doc_title = title_text
            title_done = True
            continue

        if line.startswith("## "):
            heading(doc, line[3:].strip())
            continue

        if line == "[[FIGURE 1]]":
            land = doc.add_section(WD_SECTION.NEW_PAGE)
            landscape(land)
            land.header.is_linked_to_previous = False
            style_footer(land)

            pic_p = doc.add_paragraph()
            pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic_p.paragraph_format.space_before = Pt(54)
            pic_p.paragraph_format.space_after = Pt(2)
            pic_p.paragraph_format.keep_with_next = True
            shape = pic_p.add_run().add_picture(str(FIG), width=Inches(9.4))
            shape._inline.docPr.set(
                "descr",
                "Architecture diagram: one run through six stages (spec, plan, "
                "execute, score, record, read); a repository snapshot and a context "
                "selector feed the run, six protocol adapters sit under execute and "
                "score, and storage holds runs, cells, checks, gates, artifacts and "
                "repo snapshots.",
            )
            pending_caption = True
            continue

        if pending_caption and line.startswith("Figure 1."):
            cap = doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.paragraph_format.space_before = Pt(0)
            cap.paragraph_format.space_after = Pt(0)
            a = cap.add_run("Figure 1.")
            a.font.name = "Calibri"
            a.font.size = Pt(8.5)
            a.font.bold = True
            a.font.color.rgb = NAVY
            b = cap.add_run(" " + line[len("Figure 1."):].strip())
            b.font.name = "Calibri"
            b.font.size = Pt(8.5)
            b.font.italic = True
            b.font.color.rgb = MUTED

            back = doc.add_section(WD_SECTION.NEW_PAGE)
            portrait(back)
            style_header(back)
            style_footer(back)
            pending_caption = False
            continue

        if line.startswith("> "):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(14)
            p.paragraph_format.line_spacing = 1.1
            q = p.add_run(line[2:].strip())
            q.font.name = "Georgia"
            q.font.size = Pt(13.5)
            q.font.italic = True
            q.font.color.rgb = NAVY
            continue

        if line.startswith("*") and line.endswith("*") and not standfirst_done:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(13)
            p.paragraph_format.line_spacing = 1.15
            add_runs(p, line, size=12.5, color=RGBColor(0x40, 0x51, 0x62), italic=True)
            bottom_rule(p, color=RULE, size=4)
            standfirst_done = True
            continue

        if line.startswith("- "):
            bullet(doc, line[2:].strip())
            continue

        if line.startswith("URL: "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(9)
            p.paragraph_format.line_spacing = 1.1
            lead = p.add_run("URL: ")
            lead.font.name = "Calibri"
            lead.font.size = Pt(9.5)
            lead.font.color.rgb = MUTED
            add_hyperlink(p, line[5:].strip(), line[5:].strip())
            continue

        body_paragraph(doc, line)

    if table_lines:
        add_table(doc, table_lines)

    props = doc.core_properties
    props.title = doc_title
    props.subject = "Why an evaluation harness, and what one run has to prove"
    props.author = "Ken"
    props.keywords = "evaluation harness, LLM evaluation, adapters, Wilson interval, receipts"
    props.comments = "Corporate engineering blog. Draft."

    doc.save(OUT)
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    build()