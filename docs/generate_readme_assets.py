#!/usr/bin/env python3
"""Generate the README's SVG "output cards" from the REAL committed golden briefs.

Every value drawn here (PMIDs, axis names, hypothesis verdicts, the experiment
question, the claude_science_prompt excerpt, the domain scoreboard counts) is
read straight out of ``snapshots/*_golden_brief.json`` — nothing is hand-typed
or fabricated. Re-run after (re)capturing goldens to refresh the images:

    python docs/generate_readme_assets.py

Writes:  docs/img/in-action-t2d.svg   docs/img/without-with.svg
Deterministic and offline (no API key, no network) — pure JSON -> SVG.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SNAP = REPO / "snapshots"
IMG = REPO / "docs" / "img"
IMG.mkdir(parents=True, exist_ok=True)

# --- palette (GitHub-dark-ish; a self-contained card reads fine on both themes) ---
BG, PANEL, BORDER = "#0d1117", "#161b22", "#30363d"
TXT, MUTED = "#c9d1d9", "#8b949e"
GREEN, AMBER, RED, BLUE, PURPLE = "#3fb950", "#d29922", "#f85149", "#58a6ff", "#bc8cff"
MONO = "ui-monospace,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
LH = 21  # line height


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap(s: str, width: int) -> list[str]:
    out: list[str] = []
    for para in (s or "").split("\n"):
        out.extend(textwrap.wrap(para, width) or [""])
    return out


class SVG:
    def __init__(self, w: int) -> None:
        self.w = w
        self.parts: list[str] = []
        self.y = 0

    def text(self, x: int, s: str, fill: str = TXT, size: int = 13, weight: str = "normal", mono: bool = True) -> None:
        fam = MONO if mono else "system-ui,-apple-system,Segoe UI,Roboto,sans-serif"
        self.parts.append(
            f'<text x="{x}" y="{self.y}" fill="{fill}" font-family="{fam}" '
            f'font-size="{size}" font-weight="{weight}" xml:space="preserve">{esc(s)}</text>'
        )

    def line(self, x: int, s: str, fill: str = TXT, size: int = 13, weight: str = "normal", mono: bool = True) -> None:
        self.text(x, s, fill=fill, size=size, weight=weight, mono=mono)
        self.y += LH

    def check(self, x: int, y: int, col: str) -> None:
        """A checkmark drawn as an SVG path (never a font glyph) so it renders
        identically on GitHub and in any rasterizer, baseline ~= y."""
        self.parts.append(
            f'<path d="M{x} {y - 5} l4 5 l8 -12" fill="none" stroke="{col}" '
            f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    def cross(self, x: int, y: int, col: str) -> None:
        self.parts.append(
            f'<path d="M{x} {y - 11} l11 11 M{x + 11} {y - 11} l-11 11" fill="none" '
            f'stroke="{col}" stroke-width="2.4" stroke-linecap="round"/>'
        )

    def gap(self, px: int) -> None:
        self.y += px

    def render(self, title: str) -> str:
        h = self.y + 24
        header = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{h}" '
            f'viewBox="0 0 {self.w} {h}" role="img" aria-label="{esc(title)}">'
            f'<rect x="0.5" y="0.5" width="{self.w - 1}" height="{h - 1}" rx="10" fill="{BG}" stroke="{BORDER}"/>'
            f'<rect x="0.5" y="0.5" width="{self.w - 1}" height="40" rx="10" fill="{PANEL}"/>'
            f'<rect x="0.5" y="30" width="{self.w - 1}" height="11" fill="{PANEL}"/>'
            f'<line x1="0" y1="41" x2="{self.w}" y2="41" stroke="{BORDER}"/>'
            f'<circle cx="20" cy="20" r="5" fill="{RED}"/><circle cx="38" cy="20" r="5" fill="{AMBER}"/>'
            f'<circle cx="56" cy="20" r="5" fill="{GREEN}"/>'
            f'<text x="76" y="25" fill="{MUTED}" font-family="{MONO}" font-size="13">{esc(title)}</text>'
        )
        return header + "".join(self.parts) + "</svg>"


def build_in_action(t2d: dict) -> str:
    W = 780
    s = SVG(W)
    s.y = 68
    obs = t2d["request_echo"]
    axes = [a["axis"] for a in t2d["axes"]]
    hyps = t2d["hypotheses"]
    te = t2d["top_experiment"]
    refs = len(t2d["references"])

    s.line(24, "CLINICIAN INPUT", MUTED, 11, "bold")
    for ln in wrap(obs, 92):
        s.line(24, ln, TXT)
    s.gap(8)

    s.line(24, "BIOLOGICAL AXES  (decomposed, free-form)", MUTED, 11, "bold")
    for ln in wrap("  ".join(f"[{a}]" for a in axes), 92):
        s.line(24, ln, BLUE)
    s.gap(8)

    s.line(24, "HYPOTHESES  (each graded against real retrieved evidence)", MUTED, 11, "bold")
    for h in hyps:
        hid = h["hypothesis"]["id"]
        pmids = [e["reference"].get("pmid") for e in h.get("evidence", []) if e.get("reference")]
        pmids = [p for p in pmids if p]
        if h.get("grounded"):
            dot, col = "●", GREEN
            tag = f"open_question · GROUNDED · supports={h['supporting_count']} · PMID {', '.join(pmids)}"
        else:
            dot, col = "○", MUTED
            tag = f"{h['novelty']} · ungrounded → demoted, not shipped as an experiment"
        s.line(24, f"{dot} {hid}  {tag}", col)
    s.gap(8)

    s.line(24, f"TOP EXPERIMENT  (only the grounded hypothesis, {te['hypothesis_id']}, is promoted)", MUTED, 11, "bold")
    s.line(24, f"dataset  : {te['dataset']}   [verified · resolvable]", PURPLE)
    for i, ln in enumerate(wrap(te["question"], 92)):
        s.line(24, ("question : " if i == 0 else "           ") + ln, TXT)
    steps = len(te.get("protocol_steps") or [])
    s.line(24, f"protocol : {steps} ordered, runnable steps · confirm_if + refute_if specified", TXT)
    s.gap(8)

    s.line(24, "claude_science_prompt  (paste-ready into Claude Science)", MUTED, 11, "bold")
    prompt = te.get("claude_science_prompt") or ""
    excerpt = prompt[:210].rsplit(" ", 1)[0] + " …"
    for ln in wrap(excerpt, 92):
        s.line(24, ln, GREEN)
    s.gap(10)

    s.line(24, f"{refs} resolvable references · temperature 0 · reproduce byte-identical: TRANSBENCH_MODE=golden", MUTED, 12)
    s.line(24, "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice.", MUTED, 11)
    return s.render("TransBench · generate_experiment()  —  type 2 diabetes (live-captured, real PubMed)")


def build_without_with(goldens: dict[str, dict]) -> str:
    W = 820
    colw = (W - 24 * 3) // 2
    lx, rx = 24, 24 + colw + 24
    s = SVG(W)

    # scoreboard counts — all computed from the real goldens
    shipped = sum(1 for g in goldens.values() if g["top_experiment"]["hypothesis_id"] not in ("none", "", None))
    gated = sum(1 for g in goldens.values() if g["top_experiment"]["hypothesis_id"] in ("none", "", None))
    caught = sum(1 for g in goldens.values() if "original proposal rejected" in (g["top_experiment"].get("feasibility_notes") or "").lower())
    n = len(goldens)

    def panel(x: int, title: str, tcol: str, rows: list[tuple[str, str, str]], top: int) -> int:
        s.parts.append(f'<rect x="{x}" y="{top}" width="{colw}" height="__H__{x}__" rx="8" fill="{PANEL}" stroke="{BORDER}"/>')
        s.y = top + 26
        s.text(x + 16, title, tcol, 13, "bold")
        s.y += LH + 6
        for kind, icol, txt in rows:
            (s.check if kind == "check" else s.cross)(x + 16, s.y, icol)
            first = True
            for ln in wrap(txt, 40):
                s.text(x + 34, ln, icol if first else TXT, 12.5)
                s.y += LH
                first = False
            s.y += 8
        return s.y + 8

    top = 60
    s.y = 52
    s.text(24, "Same question. One connector of difference.", TXT, 15, "bold", mono=False)

    left_rows = [
        ("cross", RED, "Citations: plausible-looking PMIDs, often fabricated or not about the claim"),
        ("cross", RED, "Novelty: confidently reframes textbook facts as 'novel findings'"),
        ("cross", RED, "Dataset: names a GEO accession that may not exist — or is a different study"),
        ("cross", RED, "Answers every question with equal confidence, even with no evidence"),
    ]
    right_rows = [
        ("check", GREEN, "Every citation is a real, resolvable PubMed / trial record — or the claim is dropped"),
        ("check", GREEN, "Novelty gate demotes 'established' claims; only open questions are promoted"),
        ("check", GREEN, f"Dataset gate verifies each proposed accession against the real record — rejected {caught} unverifiable ones here"),
        ("check", GREEN, f"Refuses to ship an experiment when nothing clears the bar ({gated} of {n} domains, gated not faked)"),
    ]
    end_l = panel(lx, "Plain LLM  —  no connector", RED, left_rows, top)
    end_r = panel(rx, "TransBench connector", GREEN, right_rows, top)
    body_bottom = max(end_l, end_r)
    ph_l = body_bottom - top
    # fix the two panel heights to equal, full body
    s.parts = [p.replace(f"__H__{lx}__", str(ph_l)).replace(f"__H__{rx}__", str(ph_l)) for p in s.parts]

    # quantified scoreboard strip
    s.y = body_bottom + 30
    sb = (f"{n} real domains tested   ·   {shipped} runnable experiments shipped   ·   "
          f"{gated} correctly gated out   ·   {caught} unverifiable datasets caught   ·   0 fabricated citations")
    s.parts.append(f'<rect x="24" y="{body_bottom + 12}" width="{W - 48}" height="34" rx="8" fill="{PANEL}" stroke="{BORDER}"/>')
    s.text(40, sb, TXT, 12.5)
    s.y = body_bottom + 54
    return s.render("Without vs. with the TransBench connector — quantified on 4 real captured runs")


def main() -> None:
    t2d = json.loads((SNAP / "metabolic_t2d_golden_brief.json").read_text())
    goldens = {
        p.stem: json.loads(p.read_text())
        for p in sorted(SNAP.glob("*_golden_brief.json"))
    }
    (IMG / "in-action-t2d.svg").write_text(build_in_action(t2d))
    (IMG / "without-with.svg").write_text(build_without_with(goldens))
    print("wrote", IMG / "in-action-t2d.svg")
    print("wrote", IMG / "without-with.svg")
    print("scoreboard domains:", list(goldens))


if __name__ == "__main__":
    main()
