
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.family"] = "DejaVu Sans"

W, H = 8.0, 4.5
fig = plt.figure(figsize=(W, H), dpi=300)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")

NAVY = "#16324F"
TEAL = "#2F6F62"
AMBER = "#B7791F"
INK = "#1B2733"
MUTED = "#5B6B7B"
LIGHT = "#F3F6F9"


def box(x, y, w, h, title, body, edge=NAVY, fill=LIGHT, tsize=11.2, bsize=8.2, tcolor=NAVY):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.0",
                       linewidth=1.1, edgecolor=edge, facecolor=fill, zorder=3)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h - 2.0, title, ha="center", va="top",
            fontsize=tsize, fontweight="bold", color=tcolor, zorder=4)
    if body:
        ax.text(x + w / 2, y + h - 6.4, body, ha="center", va="top",
                fontsize=bsize, color=INK, linespacing=1.42, zorder=4)


def arrow(x1, y1, x2, y2, color="#6B7C8F", lw=1.5, ls="-"):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=11,
                        linewidth=lw, color=color, shrinkA=1, shrinkB=1,
                        linestyle=ls, zorder=2)
    ax.add_patch(a)


ax.text(1.3, 97.4, "ONE RUN, END TO END", fontsize=8.4, color=MUTED,
        fontweight="bold", va="top")
ax.text(1.3, 93.4, "A declarative spec becomes a receipt you can defend.",
        fontsize=13.2, color=INK, va="top")

top, bh = 61.0, 25.0
bw, gap, x0 = 14.0, 2.6, 1.2
stages = [
    ("1  SPEC",    "YAML: cases,\nmodels, metrics,\nprices, gates"),
    ("2  PLAN",    "cells: candidate\nx case x replica"),
    ("3  EXECUTE", "bounded\nconcurrency,\nretries, budget,\nresume"),
    ("4  SCORE",   "metrics and\njudges write\nchecks per cell"),
    ("5  RECORD",  "n, 95% Wilson\ninterval, pass@k,\ngates, receipt"),
    ("6  READ",    "heatmap,\ncompare, receipt,\nexit 0/1/2/3"),
]
xs = []
for i, (t, b) in enumerate(stages):
    x = x0 + i * (bw + gap)
    xs.append(x)
    box(x, top, bw, bh, t, b)
    if i:
        arrow(xs[i - 1] + bw + 0.3, top + bh / 2, x - 0.3, top + bh / 2)

band_y, band_h = 41.5, 15.6
bx = xs[2] - 0.4
bw2 = (xs[3] + bw) - bx + 0.4
p = FancyBboxPatch((bx, band_y), bw2, band_h, boxstyle="round,pad=0.3,rounding_size=0.9",
                   linewidth=1.0, edgecolor=AMBER, facecolor="#FDF6EA", zorder=3)
ax.add_patch(p)
ax.text(bx + bw2 / 2, band_y + band_h - 1.4, "ADAPTER SEAM", ha="center", va="top",
        fontsize=8.8, fontweight="bold", color=AMBER)
ax.text(bx + bw2 / 2, band_y + band_h - 4.8,
        "candidate (RAG, HTTP, provider)\njudge  |  metric  |  source  |  sink",
        ha="center", va="top", fontsize=8.2, color=INK, linespacing=1.45)
ax.text(bx + bw2 / 2, band_y + 1.2,
        "core imports protocols only",
        ha="center", va="bottom", fontsize=7.6, color=MUTED, style="italic")
arrow(xs[2] + bw / 2, band_y + band_h + 0.2, xs[2] + bw / 2, top - 0.4, color=AMBER, lw=1.0, ls=(0, (2, 2)))
arrow(xs[3] + bw / 2, band_y + band_h + 0.2, xs[3] + bw / 2, top - 0.4, color=AMBER, lw=1.0, ls=(0, (2, 2)))

sy, sh = 20.5, 16.5
p = FancyBboxPatch((bx, sy), (xs[4] + bw) - bx + 0.4, sh,
                   boxstyle="round,pad=0.3,rounding_size=0.9",
                   linewidth=1.0, edgecolor=TEAL, facecolor="#EEF5F2", zorder=3)
ax.add_patch(p)
ax.text(bx + 0.8, sy + sh - 1.4, "STORAGE", fontsize=8.8, fontweight="bold",
        color=TEAL, va="top")
ax.text(bx + 0.8, sy + sh - 4.8,
        "PostgreSQL / SQLite: specs, runs, cells, checks, gates, events\n"
        "artifacts on disk    |    encrypted vault (BYOK)",
        fontsize=8.0, color=INK, va="top", linespacing=1.5)
ax.text(bx + 0.8, sy + 1.2,
        "a run snapshots the spec, the price table and the judge configuration",
        fontsize=7.6, color=MUTED, style="italic", va="bottom")

ax.text(1.3, 18.4, "REDACTION", fontsize=8.4, fontweight="bold", color=NAVY, va="top")
ax.text(1.3, 14.8,
        "one filter redacts every log line, event, artifact, cell and run receipt",
        fontsize=8.2, color=INK, va="top")
ax.text(1.3, 9.4, "GATES", fontsize=8.4, fontweight="bold", color=NAVY, va="top")
ax.text(1.3, 5.8,
        "thresholds on aggregates, evaluated after the run:   0 success   |   1 gate failure   |   "
        "2 spec error   |   3 budget abort",
        fontsize=8.2, color=INK, va="top")

fig.savefig(r"D:\projects\eval-harness\blog\architecture.png", dpi=300,
            facecolor="white", bbox_inches="tight", pad_inches=0.1)
print("saved")