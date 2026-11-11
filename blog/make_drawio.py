"""Generate the eval-harness architecture diagram as drawio XML."""
from __future__ import annotations

from html import escape
from pathlib import Path


NAVY = "#16324F"
PALE = "#EAF1F8"
AMBER = "#B7791F"
AMBER_FILL = "#FFF8E8"
TEAL = "#2F6B5E"
TEAL_FILL = "#E8F4EF"
GRAY = "#5B6770"

W, H = 1200, 700
STAGE_W, STAGE_H, GAP = 175, 82, 22
STAGE_Y = 95


def esc(value: str) -> str:
    return escape(value, quote=True).replace("\n", "&#10;")


def text_cell(cid: str, parent: str, x: int, y: int, w: int, h: int, value: str,
              size: int = 12, color: str = "#333333", bold: bool = False,
              align: str = "center", valign: str = "middle",
              italic: bool = False) -> str:
    style = (
        "text;html=0;whiteSpace=wrap;rounded=0;"
        f"fontSize={size};fontColor={color};"
        f"align={align};verticalAlign={valign};"
    )
    if bold:
        style += "fontStyle=1;"
    elif italic:
        style += "fontStyle=2;"
    return (
        f'<mxCell id="{cid}" value="{esc(value)}" style="{style}" vertex="1" parent="{parent}">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
        "</mxCell>"
    )


def stage(cid: str, x: int, title: str, sub: str) -> list[str]:
    gid = f"g_{cid}"
    return [
        f'<mxCell id="{gid}" value="" style="group" vertex="1" connectable="0" parent="1">'
        f'<mxGeometry x="{x}" y="{STAGE_Y}" width="{STAGE_W}" height="{STAGE_H}" as="geometry"/></mxCell>',
        f'<mxCell id="{cid}" value="" style="rounded=1;whiteSpace=wrap;html=1;'
        f'fillColor={PALE};strokeColor={NAVY};strokeWidth=2;" vertex="1" parent="{gid}">'
        f'<mxGeometry x="0" y="0" width="{STAGE_W}" height="{STAGE_H}" as="geometry"/></mxCell>',
        text_cell(f"{cid}_t", gid, 0, 8, STAGE_W, 30, title, 18, NAVY, True),
        text_cell(f"{cid}_b", gid, 10, 40, STAGE_W - 20, 34, sub, 12, "#33414D"),
    ]


def band(cid: str, x: int, y: int, w: int, h: int, title: str,
         stroke: str, fill: str, title_color: str) -> list[str]:
    return [
        f'<mxCell id="{cid}" value="" style="rounded=1;whiteSpace=wrap;html=1;dashed=1;'
        f'fillColor={fill};strokeColor={stroke};strokeWidth=2;" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>',
        text_cell(f"{cid}_t", "1", x + 12, y + 8, w - 24, 24, title, 14, title_color, True,
                  "left", "middle"),
    ]


def chip(cid: str, x: int, y: int, label: str) -> str:
    return (
        f'<mxCell id="{cid}" value="{esc(label)}" style="rounded=1;whiteSpace=wrap;html=1;'
        f'fillColor=#FFFFFF;strokeColor={AMBER};strokeWidth=1.5;fontSize=13;fontColor={NAVY};'
        'fontStyle=1;" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="196" height="34" as="geometry"/></mxCell>'
    )


def edge(cid: str, source: str, target: str, dashed: bool = False,
         label: str = "", ex: float = 0.5, ey: float = 0.5,
         nx: float = 0.5, ny: float = 0.5) -> str:
    style = (
        "edgeStyle=none;rounded=0;html=1;endArrow=block;endFill=1;"
        f"strokeColor={NAVY};strokeWidth=2;"
        f"exitX={ex};exitY={ey};entryX={nx};entryY={ny};"
    )
    if dashed:
        style += "dashed=1;"
    return (
        f'<mxCell id="{cid}" value="{esc(label)}" style="{style}" edge="1" parent="1" '
        f'source="{source}" target="{target}">'
        '<mxGeometry relative="1" as="geometry"/>'
        "</mxCell>"
    )


stages = [
    ("spec", "SPEC", "one YAML file"),
    ("plan", "PLAN", "candidate x case\nx replica"),
    ("exec", "EXECUTE", "bounded concurrency"),
    ("score", "SCORE", "checks, intervals"),
    ("record", "RECORD", "rows + snapshots"),
    ("read", "READ", "console + receipt"),
]

cells: list[str] = []
cells.append(text_cell("title", "1", 25, 14, 900, 36, "ONE RUN, END TO END", 24, NAVY, True,
                       "left", "middle"))
cells.append(text_cell("subtitle", "1", 25, 46, 900, 22,
                       "A declarative spec becomes a receipt you can defend.", 12, GRAY, False,
                       "left", "middle", True))
cells.append(text_cell("version", "1", 985, 20, 190, 20, "eval-harness 0.4", 11, GRAY, False,
                       "right", "middle"))

for i, (cid, title, sub) in enumerate(stages):
    cells.extend(stage(cid, 25 + i * (STAGE_W + GAP), title, sub))

# Repository band under SPEC and PLAN.
cells.extend(band("repo", 25, 225, 360, 130, "REPOSITORY", AMBER, AMBER_FILL, "#8A5A12"))
cells.append(text_cell("repo_b", "1", 39, 255, 332, 92,
                       "local root or HTTPS url\n"
                       "snapshot <digest>\n"
                       "cases + fixtures from it\n"
                       "copied before cell 1",
                       14, "#333333", False, "left", "top"))

# Adapter seam under EXECUTE and SCORE.
cells.extend(band("adapters", 450, 225, 420, 190, "ADAPTER SEAM", TEAL, TEAL_FILL, "#245247"))
chip_labels = ["Candidate", "Judge", "Metric", "Source", "Sink", "ContextSelector"]
for i, label in enumerate(chip_labels):
    col, row = i % 2, i // 2
    cells.append(chip(f"chip{i}", 466 + col * 204, 260 + row * 44, label))
cells.append(text_cell("adapter_note", "1", 466, 398, 388, 14,
                       "protocols + registry only", 11, "#245247", False, "center", "middle"))

# Storage band under SCORE, RECORD and READ.
cells.extend(band("storage", 25, 470, 1160, 120, "STORAGE", NAVY, "#F4F7FA", NAVY))
cells.append(text_cell("store_left", "1", 41, 503, 320, 72,
                       "PostgreSQL / SQLite\n"
                       "runs, cells, checks, gates,\n"
                       "events, source snapshot",
                       13, "#333333", False, "left", "top"))
cells.append(text_cell("store_mid", "1", 430, 503, 300, 72,
                       "artifacts on disk\n"
                       "spec + price snapshots",
                       13, "#333333", False, "left", "top"))
cells.append(text_cell("store_right", "1", 820, 503, 340, 72,
                       "repo snapshots <digest>\n"
                       "encrypted vault (BYOK)",
                       13, "#333333", False, "left", "top"))

# Main pipeline.
for i in range(len(stages) - 1):
    cells.append(edge(f"e{i}", stages[i][0], stages[i + 1][0], False, "", 1, 0.5, 0, 0.5))

# Supporting edges, straight, with explicit ports.
cells.append(edge("e_repo", "repo", "plan", True, "", 0.5, 0, 0.5, 1))
cells.append(edge("e_adapt_exec", "adapters", "exec", True, "", 0.25, 0, 0.5, 1))
cells.append(edge("e_adapt_score", "adapters", "score", True, "", 0.75, 0, 0.5, 1))
cells.append(edge("e_store", "record", "storage", True, "", 0.5, 1, 0.754, 0))

# Footer.
cells.append(text_cell("foot_left", "1", 25, 630, 680, 30,
                       "REDACTION  one redactor on logs, events, artifacts, cell records, HTML",
                       11, GRAY, False, "left", "middle"))
cells.append(text_cell("foot_right", "1", 730, 630, 445, 30,
                       "GATES  0 pass | 1 fail | 2 spec error | 3 budget",
                       11, GRAY, False, "right", "middle"))

xml = (
    '<mxfile host="app.diagrams.net" modified="2026-11-03T00:00:00.000Z" '
    'agent="eval-harness blog" version="24.7.17">\n'
    '  <diagram id="architecture" name="Architecture">\n'
    f'    <mxGraphModel dx="{W}" dy="{H}" grid="0" gridSize="10" guides="1" tooltips="1" '
    f'connect="1" arrows="1" fold="1" page="0" pageScale="1" pageWidth="{W}" pageHeight="{H}" '
    'math="0" shadow="0">\n'
    "      <root>\n"
    '        <mxCell id="0"/>\n'
    '        <mxCell id="1" parent="0"/>\n'
    + "".join("        " + c + "\n" for c in cells) +
    "      </root>\n"
    "    </mxGraphModel>\n"
    "  </diagram>\n"
    "</mxfile>\n"
)

out = Path(__file__).with_name("architecture.drawio")
out.write_text(xml, encoding="utf-8")
print(f"wrote {out} ({len(xml)} bytes, {len(cells)} cells)")
