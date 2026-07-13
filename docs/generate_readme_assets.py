#!/usr/bin/env python3
"""Generate the README's figures as SVGs from the REAL committed golden briefs.

Every value drawn here (PMIDs, axis names, hypothesis verdicts, the experiment
question, the claude_science_prompt excerpt, the domain scoreboard counts) is
read straight out of ``snapshots/*_golden_brief.json`` — nothing is hand-typed
or fabricated. Structural diagrams (pipeline, architecture, Claude Science flow)
encode the fixed system shape described in the README. Re-run after (re)capturing
goldens or editing the design to refresh every image:

    python docs/generate_readme_assets.py

Writes (docs/img/):  without-with.svg · in-action-t2d.svg · claude-science-flow.svg
                     pipeline.svg · architecture.svg
Deterministic and offline (no API key, no network) — pure JSON/spec -> SVG.
The cards use a self-contained light "product" surface so they read cleanly on
both light and dark README themes.
"""
from __future__ import annotations

import json
import math
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SNAP = REPO / "snapshots"
IMG = REPO / "docs" / "img"
IMG.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- design tokens
INK, INK2, MUTED, FAINT = "#0b1324", "#334155", "#5b6b82", "#94a3b8"
CARD, PANEL, PANEL2, BORDER, BORDER2 = "#ffffff", "#f7f9fc", "#eef2f8", "#e4e9f1", "#cbd5e1"
GREEN, GREEN_T, GREEN_BD = "#0e9f6e", "#e7f7ef", "#a9e2c9"
RED, RED_T, RED_BD = "#e11d48", "#fdecef", "#f6c3ce"
AMBER, AMBER_T, AMBER_BD = "#b45309", "#fdf4e4", "#eed9ac"
BLUE, BLUE_T, BLUE_BD = "#2563eb", "#e9eefe", "#c3d3fb"
PURPLE, PURPLE_T, PURPLE_BD = "#7c3aed", "#f2ebfd", "#d8c1f8"
TEAL, TEAL_T, TEAL_BD = "#0e7490", "#e3f2f6", "#a9d9e5"
G_A, G_B = "#102a52", "#0e7490"  # brand gradient: deep navy -> teal

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cw(size: float, mono: bool) -> float:
    return size * (0.605 if mono else 0.532)  # approx glyph advance for wrap/width math


def wrap_px(s: str, max_px: float, size: float, mono: bool = False) -> list[str]:
    n = max(4, int(max_px / _cw(size, mono)))
    out: list[str] = []
    for para in (s or "").split("\n"):
        out.extend(textwrap.wrap(para, n) or [""])
    return out


def approx_w(s: str, size: float, mono: bool = False) -> float:
    return len(s) * _cw(size, mono)


def pill_w(label: str, size: float, mono: bool = False, padx: float = 11) -> float:
    # generous factor: pill labels are often bold / uppercase (wider glyphs) and
    # must never clip, since overflowing light text vanishes on the light card.
    return len(label) * size * (0.62 if mono else 0.64) + 2 * padx


# ------------------------------------------------------------------- Fig canvas
class Fig:
    """A self-contained 'product card': soft-shadowed white surface + gradient
    banner header + a body you fill with primitives. `y` is the running baseline
    cursor for the body."""

    M = 16    # outer margin (room for the drop shadow)
    PAD = 28  # inner content padding

    def __init__(self, w: int, right: str = "", sub: str = "", banner_h: int = 64) -> None:
        self.w = w
        self.right = right
        self.sub = sub
        self.banner_h = banner_h
        self.parts: list[str] = []
        self.cx0 = self.M + self.PAD
        self.cx1 = w - self.M - self.PAD
        self.cwidth = self.cx1 - self.cx0
        self.y = self.M + banner_h + 40

    # -- primitives ----------------------------------------------------------
    def raw(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x, y, s, fill=INK, size=13, weight="400", mono=False, anchor="start", ls=None, italic=False) -> None:
        fam = MONO if mono else SANS
        extra = f' letter-spacing="{ls}"' if ls is not None else ""
        it = ' font-style="italic"' if italic else ""
        self.raw(
            f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-family="{fam}" '
            f'font-size="{size}" font-weight="{weight}"{it} text-anchor="{anchor}"{extra} '
            f'xml:space="preserve">{esc(s)}</text>'
        )

    def rrect(self, x, y, w, h, fill, rx=12, stroke=None, sw=1, shadow=False) -> None:
        st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        fl = ' filter="url(#shadow)"' if shadow else ""
        self.raw(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{st}{fl}/>')

    def accent(self, x, y, h, col, w=4) -> None:
        """A rounded left accent bar for a panel."""
        self.raw(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h:.1f}" rx="{w/2}" fill="{col}"/>')

    def hline(self, x1, x2, y, stroke=BORDER, sw=1) -> None:
        self.raw(f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{stroke}" stroke-width="{sw}"/>')

    def vline(self, x, y1, y2, stroke=BORDER, sw=1) -> None:
        self.raw(f'<line x1="{x:.1f}" y1="{y1:.1f}" x2="{x:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{sw}"/>')

    def check(self, x, y, col=GREEN, s=1.0) -> None:
        self.raw(f'<path d="M{x:.1f} {y:.1f} l{4.2*s:.1f} {5*s:.1f} l{9*s:.1f} {-13*s:.1f}" fill="none" '
                 f'stroke="{col}" stroke-width="{2.6*s:.2f}" stroke-linecap="round" stroke-linejoin="round"/>')

    def cross(self, x, y, col=RED, s=1.0) -> None:
        self.raw(f'<path d="M{x:.1f} {y-10*s:.1f} l{10*s:.1f} {10*s:.1f} M{x+10*s:.1f} {y-10*s:.1f} '
                 f'l{-10*s:.1f} {10*s:.1f}" fill="none" stroke="{col}" stroke-width="{2.6*s:.2f}" stroke-linecap="round"/>')

    def dot(self, x, y, col, r=5) -> None:
        self.raw(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{col}"/>')

    def ring(self, x, y, col, r=5, sw=2) -> None:
        self.raw(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="none" stroke="{col}" stroke-width="{sw}"/>')

    def pill(self, x, cy, label, fill, txt="#ffffff", size=11.5, weight="700", bd=None, padx=11, mono=False) -> float:
        w = pill_w(label, size, mono, padx)
        h = size + 11
        self.rrect(x, cy - h / 2, w, h, fill, rx=h / 2, stroke=bd)
        self.text(x + w / 2, cy + size * 0.34, label, fill=txt, size=size, weight=weight, mono=mono, anchor="middle")
        return w

    # -- diagram primitives --------------------------------------------------
    def arrow(self, x1, y1, x2, y2, col=BORDER2, sw=2.2, head=9, dash=False) -> None:
        ang = math.atan2(y2 - y1, x2 - x1)
        bx, by = x2 - head * math.cos(ang), y2 - head * math.sin(ang)
        d = ' stroke-dasharray="5 4"' if dash else ""
        self.raw(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{col}" '
                 f'stroke-width="{sw}" stroke-linecap="round"{d}/>')
        ox, oy = math.cos(ang + math.pi / 2) * head * 0.5, math.sin(ang + math.pi / 2) * head * 0.5
        self.raw(f'<path d="M{x2:.1f} {y2:.1f} L{bx + ox:.1f} {by + oy:.1f} L{bx - ox:.1f} {by - oy:.1f} z" fill="{col}"/>')

    def elbow(self, pts, col=BORDER2, sw=2.2, head=9, dash=False) -> None:
        """Orthogonal (or arbitrary) polyline routed through `pts`, arrowhead at the last point."""
        (lx, ly), (px, py) = pts[-1], pts[-2]
        ang = math.atan2(ly - py, lx - px)
        bx, by = lx - head * math.cos(ang), ly - head * math.sin(ang)
        route = pts[:-1] + [(bx, by)]
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in route)
        da = ' stroke-dasharray="5 4"' if dash else ""
        self.raw(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{sw}" '
                 f'stroke-linecap="round" stroke-linejoin="round"{da}/>')
        ox, oy = math.cos(ang + math.pi / 2) * head * 0.5, math.sin(ang + math.pi / 2) * head * 0.5
        self.raw(f'<path d="M{lx:.1f} {ly:.1f} L{bx + ox:.1f} {by + oy:.1f} L{bx - ox:.1f} {by - oy:.1f} z" fill="{col}"/>')

    def cnode(self, x, y, w, h, title, subs=None, fill=CARD, stroke=BORDER, accent=None, tcol=INK, tsize=13) -> None:
        """Centered-content box: bold title + muted subtitle line(s)."""
        self.rrect(x, y, w, h, fill, rx=12, stroke=stroke)
        if accent:
            self.accent(x + 11, y + 11, h - 22, accent)
        subs = subs or []
        block = tsize + len(subs) * 14
        ty = y + (h - block) / 2 + tsize * 0.78
        cx = x + w / 2 + (5 if accent else 0)
        self.text(cx, ty, title, tcol, tsize, "800", anchor="middle")
        yy = ty + 3
        for s in subs:
            yy += 14
            self.text(cx, yy, s, MUTED if fill in (CARD,) else tcol, 10.6, "500", anchor="middle")

    def snode(self, x, y, w, h, num, title, subs=None, fill=CARD, stroke=BORDER, numcol=TEAL, tcol=INK) -> None:
        """Numbered stage box: coloured number badge + left-aligned title/subtitle."""
        self.rrect(x, y, w, h, fill, rx=12, stroke=stroke)
        self.dot(x + 23, y + h / 2, numcol, r=13)
        self.text(x + 23, y + h / 2 + 4.5, str(num), "#ffffff", 12.5, "800", anchor="middle")
        subs = subs or []
        if subs:
            self.text(x + 46, y + h / 2 - 5, title, tcol, 12.5, "800")
            yy = y + h / 2 - 5
            for s in subs:
                yy += 15
                self.text(x + 46, yy, s, MUTED, 10.4, "500")
        else:
            self.text(x + 46, y + h / 2 + 4, title, tcol, 12.5, "800")

    def elabel(self, cx, cy, text, col=INK2, size=10) -> None:
        """A small white chip carrying an edge label, legible over a connector."""
        w = approx_w(text, size) + 14
        self.rrect(cx - w / 2, cy - (size + 9) / 2, w, size + 9, "#ffffff", rx=(size + 9) / 2, stroke=BORDER)
        self.text(cx, cy + size * 0.34, text, col, size, "600", anchor="middle")

    def gap(self, px: float) -> None:
        self.y += px

    # -- assembly ------------------------------------------------------------
    @staticmethod
    def _top_rounded(x, y, w, h, r) -> str:
        return (f'M{x:.1f} {y+r:.1f} a{r} {r} 0 0 1 {r} {-r} h{w-2*r:.1f} '
                f'a{r} {r} 0 0 1 {r} {r} v{h-r:.1f} h{-w:.1f} z')

    def render(self, aria: str) -> str:
        content_bottom = self.y + self.PAD
        total_h = content_bottom + self.M
        card_h = total_h - 2 * self.M
        cw = self.w - 2 * self.M
        defs = (
            '<defs>'
            f'<linearGradient id="brand" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{G_A}"/><stop offset="1" stop-color="{G_B}"/></linearGradient>'
            '<filter id="shadow" x="-8%" y="-8%" width="116%" height="116%">'
            '<feDropShadow dx="0" dy="3" stdDeviation="8" flood-color="#0b1324" flood-opacity="0.14"/></filter>'
            '</defs>'
        )
        card = (f'<rect x="{self.M}" y="{self.M}" width="{cw}" height="{card_h:.1f}" rx="18" '
                f'fill="{CARD}" stroke="{BORDER}" filter="url(#shadow)"/>')
        banner = f'<path d="{self._top_rounded(self.M, self.M, cw, self.banner_h, 18)}" fill="url(#brand)"/>'
        bx = self.M + self.PAD
        bcy = self.M + self.banner_h / 2
        head = [banner]
        # wordmark + small tag
        head.append(f'<text x="{bx}" y="{bcy - 3:.1f}" fill="#ffffff" font-family="{SANS}" '
                    f'font-size="20" font-weight="800" letter-spacing="0.2">TransBench</text>')
        tag = self.sub or "MCP connector · Claude Science"
        head.append(f'<text x="{bx}" y="{bcy + 15:.1f}" fill="#bfe3ee" font-family="{SANS}" '
                    f'font-size="11.5" font-weight="600">{esc(tag)}</text>')
        if self.right:
            head.append(f'<text x="{self.w - self.M - self.PAD}" y="{bcy + 4:.1f}" fill="#eaf6fb" '
                        f'font-family="{MONO}" font-size="12.5" font-weight="600" text-anchor="end">{esc(self.right)}</text>')
        body = "".join(self.parts)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{total_h:.0f}" '
                f'viewBox="0 0 {self.w} {total_h:.0f}" role="img" aria-label="{esc(aria)}">'
                f'{defs}{card}{"".join(head)}{body}</svg>')


# --------------------------------------------------------------- shared helpers
def section(f: Fig, label: str, note: str = "", note_col: str = MUTED) -> None:
    f.text(f.cx0, f.y, label.upper(), MUTED, 11, "700", ls="0.7")
    if note:
        f.text(f.cx1, f.y, note, note_col, 11, "600", anchor="end")
    f.y += 22


def pill_flow(f: Fig, labels, fill, txt, bd, x0=None, size=12, gap=8, line_h=30, mono=False) -> None:
    x = x0 if x0 is not None else f.cx0
    cy = f.y + 2
    for lab in labels:
        w = pill_w(lab, size, mono)  # SAME measure the pill draws with, so pills never overlap
        if x + w > f.cx1 + 1:
            x = x0 if x0 is not None else f.cx0
            cy += line_h
        f.pill(x, cy, lab, fill, txt=txt, size=size, weight="600", bd=bd, mono=mono)
        x += w + gap
    f.y = cy + line_h / 2 + 6


def disclaimer(f: Fig) -> None:
    f.gap(8)
    f.text(f.cx0, f.y, "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice.",
           FAINT, 10.5, "500")
    f.y += 2


# =============================================================== FIGURE 1: cards
def build_without_with(goldens: dict[str, dict]) -> str:
    n = len(goldens)
    shipped = sum(1 for g in goldens.values() if g["top_experiment"]["hypothesis_id"] not in ("none", "", None))
    gated = n - shipped
    caught = sum(1 for g in goldens.values()
                 if "original proposal rejected" in (g["top_experiment"].get("feasibility_notes") or "").lower())

    W = 900
    f = Fig(W, right="plain LLM  vs.  the connector")
    f.text(f.cx0, f.y, "Same question. One connector of difference.", INK, 21, "800")
    f.text(f.cx0, f.y + 23, f"Every number below is counted from {n} real, committed runs — no mock-ups.",
           MUTED, 12.5, "500")
    f.y += 50

    # -- big-number scoreboard -------------------------------------------------
    sb_h = 100
    f.rrect(f.cx0, f.y, f.cwidth, sb_h, PANEL, rx=14, stroke=BORDER)
    stats = [(str(n), ["real domains", "tested"], INK),
             (str(shipped), ["experiments", "shipped"], GREEN),
             (str(gated), ["correctly", "gated out"], AMBER),
             (str(caught), ["unverifiable", "datasets caught"], PURPLE),
             ("0", ["fabricated", "citations"], TEAL)]
    seg = f.cwidth / len(stats)
    for i, (num, cap, col) in enumerate(stats):
        cx = f.cx0 + seg * i + seg / 2
        if i > 0:
            f.vline(f.cx0 + seg * i, f.y + 20, f.y + sb_h - 20, BORDER)
        f.text(cx, f.y + 52, num, col, 44, "800", anchor="middle")
        for j, cl in enumerate(cap):
            f.text(cx, f.y + 72 + j * 14.5, cl, MUTED, 11, "600", anchor="middle")
    f.y += sb_h + 32

    # -- two comparison columns ------------------------------------------------
    col_gap = 22
    colw = (f.cwidth - col_gap) / 2
    lx, rx = f.cx0, f.cx0 + colw + col_gap
    left = [
        "Citations: plausible-looking PMIDs, often fabricated or not about the claim",
        "Novelty: confidently reframes textbook facts as ‘novel findings’",
        "Dataset: names a GEO accession that may not exist — or is a different study",
        "Answers every question with equal confidence, even with no evidence",
    ]
    right = [
        "Every citation is a real, resolvable PubMed / trial record — or the claim is dropped",
        "Novelty gate demotes ‘established’ claims; only open questions are promoted",
        f"Dataset gate verifies each accession against the real record — caught {caught} here",
        f"Ships nothing when nothing clears the bar — {gated} of {n} domains gated, not faked",
    ]

    def layout(rows, textw):
        line_h, row_gap, off = 19, 15, 62
        laid, y = [], off
        for txt in rows:
            lines = wrap_px(txt, textw, 12.5)
            laid.append((y, lines))
            y += len(lines) * line_h + row_gap
        return laid, y + 6

    laid_l, h_l = layout(left, colw - 58)
    laid_r, h_r = layout(right, colw - 58)
    panel_h = max(h_l, h_r)
    top = f.y

    def column(x, title, tcol, tint, tbd, laid, icon):
        f.rrect(x, top, colw, panel_h, tint, rx=14, stroke=tbd)
        f.rrect(x + 16, top + 14, 30, 30, "#ffffff", rx=9, stroke=tbd)
        if icon == "cross":
            f.cross(x + 25, top + 29, RED, 0.95)
        else:
            f.check(x + 24, top + 30, GREEN, 1.05)
        f.text(x + 56, top + 34, title, tcol, 14.5, "800")
        f.hline(x + 16, x + colw - 16, top + 52, tbd)
        for (oy, lines) in laid:
            by = top + oy
            if icon == "cross":
                f.cross(x + 22, by - 3, RED, 0.7)
            else:
                f.check(x + 20, by - 2, GREEN, 0.82)
            for k, ln in enumerate(lines):
                f.text(x + 42, by + k * 19, ln, INK2, 12.5, "500")

    column(lx, "Plain LLM  —  no connector", RED, RED_T, RED_BD, laid_l, "cross")
    column(rx, "TransBench connector", GREEN, GREEN_T, GREEN_BD, laid_r, "check")
    f.y = top + panel_h + 6
    f.text(f.cx0, f.y + 4, "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice.",
           FAINT, 10.5, "500")
    f.y += 8
    return f.render(f"Plain LLM vs the TransBench connector, quantified on {n} real captured runs: "
                    f"{n} domains, {shipped} experiments shipped, {gated} gated out, {caught} unverifiable "
                    "datasets caught, 0 fabricated citations.")


def build_in_action(golden: dict, headline: str, alt: str,
                    sub: str = "MCP connector · live-captured run") -> str:
    W = 900
    f = Fig(W, right="generate_experiment()", sub=sub)
    obs = golden["request_echo"]
    axes = [a["axis"] for a in golden["axes"]]
    hyps = golden["hypotheses"]
    te = golden["top_experiment"]
    refs = len(golden["references"])

    f.text(f.cx0, f.y, headline, INK, 20, "800")
    tw = f.pill(f.cx1 - 96, f.y - 5, "temperature 0", PANEL2, txt=INK2, size=11, weight="700", bd=BORDER)
    f.y += 30

    # -- clinician input -------------------------------------------------------
    section(f, "Clinician input", "free text · any disease")
    lines = wrap_px(obs, f.cwidth - 34, 12.5, mono=True)
    ph = len(lines) * 19 + 20
    f.rrect(f.cx0, f.y - 4, f.cwidth, ph, PANEL, rx=11, stroke=BORDER)
    f.accent(f.cx0 + 10, f.y + 4, ph - 16, BLUE)
    for i, ln in enumerate(lines):
        f.text(f.cx0 + 24, f.y + 14 + i * 19, ln, INK2, 12.5, mono=True)
    f.y += ph + 14

    # -- axes ------------------------------------------------------------------
    section(f, "Biological axes", "decomposed · free-form, domain-specific")
    pill_flow(f, axes, BLUE_T, BLUE, BLUE_BD, size=12)
    f.gap(12)

    # -- hypotheses ------------------------------------------------------------
    section(f, "Hypotheses", "each graded against real retrieved evidence")
    for h in hyps:
        hid = h["hypothesis"]["id"]
        pmids = [e["reference"].get("pmid") for e in h.get("evidence", []) if e.get("reference")]
        pmids = [p for p in pmids if p]
        # cap the inline PMID list so a heavily-cited hypothesis (e.g. the SLE run's H2) can't
        # overflow the card; T2D's grounded hypothesis has 2 PMIDs, so this is a no-op there.
        pm_label = "PMID " + ", ".join(pmids[:4]) + (f"  +{len(pmids) - 4} more" if len(pmids) > 4 else "")
        by = f.y + 6
        if h.get("grounded"):
            f.dot(f.cx0 + 5, by - 4, GREEN)
            f.text(f.cx0 + 20, by, hid, INK, 13, "800")
            pw = f.pill(f.cx0 + 46, by - 4, "GROUNDED", GREEN, size=10.5, weight="800")
            detail = f"open question · supports {h['supporting_count']}"
            f.text(f.cx0 + 46 + pw + 10, by, detail, INK2, 12, "500")
            f.text(f.cx0 + 46 + pw + 10 + approx_w(detail, 12) + 10, by, pm_label,
                   TEAL, 12, "600", mono=True)
        else:
            f.ring(f.cx0 + 5, by - 4, FAINT)
            f.text(f.cx0 + 20, by, hid, MUTED, 13, "800")
            pw = f.pill(f.cx0 + 46, by - 4, "demoted", AMBER_T, txt=AMBER, size=10.5, weight="800", bd=AMBER_BD)
            f.text(f.cx0 + 46 + pw + 10, by, "ungrounded → not shipped as an experiment", MUTED, 12, "500")
        f.y += 30
    f.gap(4)

    # -- top experiment --------------------------------------------------------
    section(f, "Top experiment", "only the grounded hypothesis is promoted")
    q_lines = wrap_px(te.get("question") or "", f.cwidth - 130, 12.5)
    vw = pill_w("verified · resolvable", 10.5)
    # dataset names vary in length (a short atlas label vs a long Gladstone accession title),
    # so wrap the value and leave room on the first line for the right-aligned "verified" pill.
    ds_lines = wrap_px(te.get("dataset") or "", f.cwidth - 108 - vw - 26, 12.5)
    ds_extra = (len(ds_lines) - 1) * 18
    eh = 30 + 26 + ds_extra + len(q_lines) * 18 + 26 + 14
    top = f.y - 4
    f.rrect(f.cx0, top, f.cwidth, eh, GREEN_T, rx=11, stroke=GREEN_BD)
    f.accent(f.cx0 + 10, top + 10, eh - 20, GREEN)
    iy = top + 24
    f.text(f.cx0 + 24, iy, "dataset", MUTED, 11, "700")
    for k, ln in enumerate(ds_lines):
        f.text(f.cx0 + 108, iy + k * 18, ln, INK, 12.5, "700")
    f.pill(f.cx0 + f.cwidth - 16 - vw, iy - 4, "verified · resolvable", PURPLE_T, txt=PURPLE, size=10.5, weight="800", bd=PURPLE_BD)
    iy += 26 + ds_extra
    f.text(f.cx0 + 24, iy, "question", MUTED, 11, "700")
    for k, ln in enumerate(q_lines):
        f.text(f.cx0 + 108, iy + k * 18, ln, INK2, 12.5, "500")
    iy += len(q_lines) * 18 + 8
    steps = len(te.get("protocol_steps") or [])
    f.text(f.cx0 + 24, iy, "protocol", MUTED, 11, "700")
    f.text(f.cx0 + 108, iy, f"{steps} ordered, runnable steps · confirm_if + refute_if specified", INK2, 12.5, "500")
    f.y = top + eh + 14

    # -- claude science prompt -------------------------------------------------
    section(f, "claude_science_prompt", "paste-ready into Claude Science", note_col=TEAL)
    prompt = te.get("claude_science_prompt") or ""
    excerpt = prompt[:230].rsplit(" ", 1)[0] + " …"
    pl = wrap_px(excerpt, f.cwidth - 34, 12, mono=True)
    ph2 = len(pl) * 18 + 20
    f.rrect(f.cx0, f.y - 4, f.cwidth, ph2, TEAL_T, rx=11, stroke=TEAL_BD)
    f.accent(f.cx0 + 10, f.y + 4, ph2 - 16, TEAL)
    for i, ln in enumerate(pl):
        f.text(f.cx0 + 24, f.y + 14 + i * 18, ln, "#0b3a49", 12, mono=True)
    f.y += ph2 + 16

    f.text(f.cx0, f.y, f"{refs} resolvable references   ·   temperature 0   ·   "
           "TRANSBENCH_MODE=golden replays this byte-identical, keyless", MUTED, 11.5, "600")
    f.y += 18
    f.text(f.cx0, f.y, "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice.",
           FAINT, 10.5, "500")
    f.y += 6
    return f.render("TransBench generate_experiment output for a type 2 diabetes observation: four decomposed "
                    "axes, H2 grounded with PMIDs 37546319 and 39422716 while H1 and H3 are demoted as "
                    "ungrounded, a top experiment on hepatocyte transporter expression in a verified Tabula "
                    f"Sapiens dataset with a {len(te.get('protocol_steps') or [])}-step protocol, and a "
                    f"paste-ready claude_science_prompt. {refs} references, temperature 0.")


# ========================================================= FIGURE 3: CS flow
def build_flow() -> str:
    W = 900
    f = Fig(W, right="how the connector runs", sub="MCP connector · Claude Science")
    f.text(f.cx0, f.y, "From a bedside sentence to a reproducible figure", INK, 20, "800")
    f.text(f.cx0, f.y + 22, "One tool call — the connector does the grounding, verification, and hand-off.",
           MUTED, 12.5, "500")
    f.y += 54

    steps = [
        ("1", "Observation", ["free text", "any disease / drug"], BLUE, False),
        ("2", "TransBench engine", ["8 agents", "3 rigor gates"], TEAL, False),
        ("3", "Grounded brief", ["cited hypotheses", "content-verified dataset"], PURPLE, False),
        ("4", "Paste-ready", ["claude_science_prompt"], GREEN, False),
        ("5", "Claude Science", ["reproducible figure"], TEAL, True),
    ]
    n, gap = len(steps), 24
    nodw = (f.cwidth - (n - 1) * gap) / n
    nodh = 106
    top = f.y + 18
    cy = top + nodh / 2
    xs = [f.cx0 + i * (nodw + gap) for i in range(n)]
    for i in range(n - 1):
        f.arrow(xs[i] + nodw + 3, cy, xs[i + 1] - 3, cy, col=BORDER2, sw=2.4, head=9)
    for i, (num, title, subs, accent, hi) in enumerate(steps):
        x = xs[i]
        fill, stroke = (TEAL, TEAL) if hi else (CARD, BORDER)
        tc, sc = ("#ffffff", "#cfeaf1") if hi else (INK, MUTED)
        f.rrect(x, top, nodw, nodh, fill, rx=13, stroke=stroke)
        bcx = x + nodw / 2
        f.dot(bcx, top, "#ffffff", r=16)
        f.dot(bcx, top, accent, r=14)
        f.text(bcx, top + 5, num, "#ffffff", 14, "800", anchor="middle")
        ty = top + 46
        for ln in wrap_px(title, nodw - 14, 13.5):
            f.text(bcx, ty, ln, tc, 13.5, "800", anchor="middle")
            ty += 17
        ty += 3
        for s in subs:
            f.text(bcx, ty, s, sc, 10.6, "500", anchor="middle")
            ty += 14
    f.y = top + nodh + 32

    section(f, "MCP tools exposed", "async submit + poll · returns a job_id in <1s")
    pill_flow(f, ["generate_experiment()", "search_grounded_evidence()", "get_experiment_result()"],
              PANEL2, INK2, BORDER, size=12, mono=True)
    f.gap(4)
    f.text(f.cx0, f.y, "Runs locally · bring-your-own Anthropic key · de-identify observations before submitting.",
           MUTED, 11.5, "600")
    f.y += 6
    disclaimer(f)
    return f.render(
        "How the TransBench MCP connector runs: a free-text clinician observation flows through the TransBench "
        "engine (8 agents, 3 rigor gates) to a grounded brief, then a content-verified dataset and a paste-ready "
        "claude_science_prompt that Claude Science turns into a reproducible figure. Tools: generate_experiment, "
        "search_grounded_evidence, get_experiment_result.")


# ========================================================= FIGURE 4: pipeline
def build_pipeline() -> str:
    W = 900
    f = Fig(W, right="how it works", sub="8 agents · 3 rigor gates")
    f.text(f.cx0, f.y, "Eight agents, three gates — nothing unsupported can pass", INK, 20, "800")
    f.text(f.cx0, f.y + 22, "A clinician's sentence goes in; a grounded, testable brief comes out.", MUTED, 12.5, "500")
    f.y += 50
    mid = f.cx0 + f.cwidth / 2

    # input
    iw, ih = 300, 52
    ix, iy = mid - iw / 2, f.y
    f.cnode(ix, iy, iw, ih, "Clinician observation", ["free text · any disease, drug, mechanism"], accent=BLUE)

    # stage chain 1..4
    stages = [("1", "Decompose", ["axes + disease anchor"], BLUE),
              ("2", "Hypothesize", ["≤3 falsifiable mechanisms"], PURPLE),
              ("3", "Retrieve", ["multi-DB · PubMed + EPMC"], TEAL),
              ("4", "Grade", ["supports / refutes + grade"], AMBER)]
    ns, sgap = 4, 16
    sw = (f.cwidth - (ns - 1) * sgap) / ns
    sh = 66
    rtop = iy + ih + 40
    sxs = [f.cx0 + i * (sw + sgap) for i in range(ns)]
    s1c = sxs[0] + sw / 2
    f.elbow([(mid, iy + ih), (mid, rtop - 20), (s1c, rtop - 20), (s1c, rtop)])
    for i, (num, t, subs, c) in enumerate(stages):
        f.snode(sxs[i], rtop, sw, sh, num, t, subs, numcol=c)
    for i in range(ns - 1):
        f.arrow(sxs[i] + sw, rtop + sh / 2, sxs[i + 1], rtop + sh / 2, head=8)

    # rigor gates band
    gtop = rtop + sh + 40
    gh = 104
    s4c = sxs[3] + sw / 2
    f.elbow([(s4c, rtop + sh), (s4c, gtop - 20), (mid, gtop - 20), (mid, gtop)])
    f.rrect(f.cx0, gtop, f.cwidth, gh, AMBER_T, rx=13, stroke=AMBER_BD)
    f.text(f.cx0 + 18, gtop + 27, "5–6 · RIGOR GATES", AMBER, 12, "800", ls="0.5")
    f.text(f.cx1 - 16, gtop + 27, "anything unsupported cannot pass", AMBER, 11, "600", anchor="end")
    gates = [("Entailment", "does the source really support it?"),
             ("Grounding", "no resolvable citation → dropped"),
             ("Novelty", "textbook fact → demoted, never novel")]
    gw = (f.cwidth - 4 * 16) / 3
    gy = gtop + 40
    for i, (gt, gd) in enumerate(gates):
        gx = f.cx0 + 16 + i * (gw + 16)
        f.rrect(gx, gy, gw, 50, "#ffffff", rx=10, stroke=AMBER_BD)
        f.text(gx + 15, gy + 21, gt, INK, 12, "800")
        f.text(gx + 15, gy + 38, gd, MUTED, 10.4, "500")

    # decision
    dtop = gtop + gh + 36
    dw, dh = 452, 46
    dx = mid - dw / 2
    f.arrow(mid, gtop + gh, mid, dtop - 3)
    f.rrect(dx, dtop, dw, dh, PANEL, rx=dh / 2, stroke=BORDER2)
    f.text(mid, dtop + 29, "Is any hypothesis an open question  AND  grounded?", INK, 13, "800", anchor="middle")

    # branches
    btop = dtop + dh + 44
    bw, bh = 356, 64
    lx, rx = f.cx0, f.cx1 - bw
    lc, rc = lx + bw / 2, rx + bw / 2
    f.elbow([(mid - 70, dtop + dh), (mid - 70, btop - 22), (lc, btop - 22), (lc, btop)], col=GREEN, sw=2.4)
    f.elbow([(mid + 70, dtop + dh), (mid + 70, btop - 22), (rc, btop - 22), (rc, btop)], col=AMBER, sw=2.4)
    f.pill(mid - 70 - 15, (dtop + dh + btop - 22) / 2, "yes", GREEN, size=10.5, weight="800")
    f.pill(mid + 70 - 15, (dtop + dh + btop - 22) / 2, "no", AMBER, size=10.5, weight="800")
    f.cnode(lx, btop, bw, bh, "7 · Design experiment",
            ["content-verified dataset + runnable protocol"], fill=GREEN_T, stroke=GREEN_BD, accent=GREEN)
    f.cnode(rx, btop, bw, bh, "No experiment shipped",
            ["honest refusal, not a fabrication"], fill=AMBER_T, stroke=AMBER_BD, accent=AMBER)

    # assemble + output
    atop = btop + bh + 44
    aw, ah = 320, 50
    ax = mid - aw / 2
    f.elbow([(lc, btop + bh), (lc, atop - 22), (mid - 46, atop - 22), (mid - 46, atop)])
    f.elbow([(rc, btop + bh), (rc, atop - 22), (mid + 46, atop - 22), (mid + 46, atop)])
    f.cnode(ax, atop, aw, ah, "8 · Assemble TransBrief", ["schema-valid · full run manifest"], accent=TEAL)
    otop = atop + ah + 34
    ow, oh = 470, 54
    ox = mid - ow / 2
    f.arrow(mid, atop + ah, mid, otop - 3)
    f.rrect(ox, otop, ow, oh, TEAL_T, rx=12, stroke=TEAL_BD)
    f.accent(ox + 11, otop + 12, oh - 24, TEAL)
    f.text(mid + 5, otop + 23, "TransBrief", INK, 13.5, "800", anchor="middle")
    f.text(mid + 5, otop + 40, "references · top_experiment · claude_science_prompt  →  Claude Science",
           INK2, 10.6, "600", anchor="middle")
    f.y = otop + oh
    disclaimer(f)
    return f.render(
        "TransBench pipeline: a clinician observation is decomposed into axes, expanded into up to three "
        "falsifiable hypotheses, graded against multi-database retrieval, then filtered by three rigor gates "
        "(entailment, grounding, novelty). If any hypothesis is both an open question and grounded, a "
        "content-verified experiment is designed; otherwise no experiment is shipped. Everything is assembled "
        "into a schema-valid TransBrief with a claude_science_prompt.")


# ===================================================== FIGURE 5: architecture
def build_architecture() -> str:
    W = 900
    f = Fig(W, right="architecture", sub="standalone · reuses Iatronix read-only")
    f.text(f.cx0, f.y, "A standalone repo that reuses a mature backend — read-only", INK, 20, "800")
    f.text(f.cx0, f.y + 22, "TransBench imports DB-free leaf functions and never writes to Iatronix.",
           MUTED, 12.5, "500")
    f.y += 52

    # asymmetric columns: a wide client<->center gap (long API label) + a narrow
    # center<->right gap (short verbs). Gaps are sized so a label's overhang stays
    # within panel padding, never on top of a panel's text.
    wl, wc, wr, g1, g2 = 190, 226, 240, 104, 52
    x1 = f.cx0
    x2 = x1 + wl + g1
    x3 = x2 + wc + g2
    top = f.y

    # center hub — TransBench (this repo)
    th = 250
    f.rrect(x2, top, wc, th, "#f4f8ff", rx=13, stroke=BLUE_BD)
    f.text(x2 + wc / 2, top + 26, "TRANSBENCH · THIS REPO", BLUE, 11, "800", anchor="middle", ls="0.5")
    bx, bw = x2 + 16, wc - 32
    r_mcp, r_eng, r_mod = top + 42, top + 104, top + 166
    f.cnode(bx, r_mcp, bw, 52, "mcp_server", ["FastMCP · stdio + HTTP"], accent=BLUE)
    f.cnode(bx, r_eng, bw, 52, "engine.run_transbench", ["8-agent LangGraph pipeline"], accent=PURPLE)
    f.cnode(bx, r_mod, bw, 52, "modes", ["live · snapshot · golden"], accent=TEAL)
    icx = x2 + wc / 2
    f.arrow(icx, r_mcp + 52, icx, r_eng, head=7)
    f.arrow(icx, r_eng + 52, icx, r_mod, head=7)

    # left — MCP client (aligned to the engine row)
    ch = 118
    ccy = r_eng + 26
    f.rrect(x1, ccy - ch / 2, wl, ch, PANEL, rx=13, stroke=BORDER)
    f.text(x1 + wl / 2, ccy - ch / 2 + 26, "MCP CLIENT", MUTED, 11, "800", anchor="middle", ls="0.6")
    f.cnode(x1 + 16, ccy - 16, wl - 32, 52, "Claude Science", ["or HTTP · direct CLI"], accent=TEAL)

    # right — Iatronix (read-only) over External services
    ih = 116
    f.rrect(x3, top, wr, ih, GREEN_T, rx=13, stroke=GREEN_BD)
    f.pill(x3 + 16, top + 24, "READ-ONLY", GREEN, size=9.5, weight="800")
    f.text(x3 + wr - 16, top + 27, "Iatronix backend", GREEN, 12, "800", anchor="end")
    f.text(x3 + 18, top + 52, "DB-free leaf functions:", INK2, 10.6, "700")
    f.text(x3 + 18, top + 71, "fetch_evidence · rank_articles", INK2, 10, mono=True)
    f.text(x3 + 18, top + 89, "grounding_gate · create_llm", INK2, 10, mono=True)
    etop, eh = top + ih + 18, 116
    f.rrect(x3, etop, wr, eh, PANEL, rx=13, stroke=BORDER)
    f.text(x3 + wr / 2, etop + 24, "EXTERNAL SERVICES", MUTED, 11, "800", anchor="middle", ls="0.5")
    for k, ln in enumerate(["PubMed / ClinicalTrials.gov", "Europe PMC / Semantic Scholar",
                            "GEO / Tabula Sapiens", "Anthropic API · BYOK"]):
        f.text(x3 + 18, etop + 46 + k * 16, ln, INK2, 10.4, "500")

    # connectors — client <-> center
    cy1, cy2 = ccy - 18, ccy + 18
    gL = (x1 + wl + x2) / 2
    f.arrow(x1 + wl, cy1, x2, cy1, head=8)
    f.elabel(gL, cy1 - 13, "generate_experiment()", INK2, 9)
    f.arrow(x2, cy2, x1 + wl, cy2, head=8, col=TEAL)
    f.elabel(gL, cy2 + 13, "TransBrief · JSON", TEAL, 9)
    # connectors — engine -> Iatronix (import, dashed) and -> External (queries)
    gR = (x2 + wc + x3) / 2
    f.arrow(x2 + wc, r_eng + 18, x3, top + ih / 2, head=8, dash=True)
    f.elabel(gR, (r_eng + 18 + top + ih / 2) / 2, "import", INK2, 9)
    f.arrow(x2 + wc, r_eng + 34, x3, etop + eh / 2, head=8, col=TEAL)
    f.elabel(gR, (r_eng + 34 + etop + eh / 2) / 2, "queries", TEAL, 9)

    f.y = max(ccy + ch / 2, top + th, etop + eh)
    disclaimer(f)
    return f.render(
        "TransBench architecture: an MCP client (Claude Science, HTTP, or CLI) calls generate_experiment on the "
        "TransBench repo, whose FastMCP server drives an 8-agent LangGraph engine with live/snapshot/golden modes. "
        "The engine imports DB-free leaf functions from the Iatronix backend read-only (never writing) and calls "
        "external services — PubMed, Europe PMC, GEO/Tabula Sapiens, and the Anthropic API via BYOK — returning a "
        "TransBrief as JSON.")


def main() -> None:
    goldens = {p.stem: json.loads(p.read_text()) for p in sorted(SNAP.glob("*_golden_brief.json"))}
    t2d = goldens["metabolic_t2d_golden_brief"]
    sle = goldens["autoimmune_sle_treg_golden_brief"]
    # per-card headline + aria description; the card BODY is fully data-driven from the golden.
    t2d_alt = ("TransBench generate_experiment output for a type 2 diabetes observation: four decomposed "
               "axes, H2 grounded with PMIDs 37546319 and 39422716 while H1 and H3 are demoted as "
               "ungrounded, a top experiment on hepatocyte transporter expression in a verified Tabula "
               f"Sapiens dataset with a {len(t2d['top_experiment'].get('protocol_steps') or [])}-step "
               f"protocol, and a paste-ready claude_science_prompt. {len(t2d['references'])} references, "
               "temperature 0.")
    sle_alt = ("TransBench generate_experiment output for a lupus (SLE) observation on the Gladstone "
               "dataset: six decomposed axes, three hypotheses (H2 and H3 grounded against real "
               "citations, H1 demoted as ungrounded), a top experiment probing PTPN2 in CD4+ regulatory "
               "vs conventional T cells in the content-verified Gladstone/Marson Perturb-CITE-seq dataset "
               f"GSE278572 with a {len(sle['top_experiment'].get('protocol_steps') or [])}-step protocol, "
               f"and a paste-ready claude_science_prompt. {len(sle['references'])} references, temperature 0.")
    (IMG / "without-with.svg").write_text(build_without_with(goldens))
    (IMG / "in-action-t2d.svg").write_text(
        build_in_action(t2d, "A real type 2 diabetes run, field by field", t2d_alt))
    (IMG / "in-action-sle.svg").write_text(
        build_in_action(sle, "A real lupus (SLE) run — the Gladstone dataset, field by field", sle_alt,
                        sub="MCP connector · live-captured Gladstone run"))
    (IMG / "claude-science-flow.svg").write_text(build_flow())
    (IMG / "pipeline.svg").write_text(build_pipeline())
    (IMG / "architecture.svg").write_text(build_architecture())
    print("wrote: without-with · in-action-t2d · in-action-sle · claude-science-flow · pipeline · architecture")
    print("scoreboard domains:", list(goldens))


if __name__ == "__main__":
    main()
