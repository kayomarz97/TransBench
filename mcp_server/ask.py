"""ask.py — fully-local TransBench CLI (no MCP connector, no server, no sandbox).

Runs the engine directly in this repo's venv on your `.env` key and prints:
  - a short grounded summary (hypotheses + citation count), and
  - the `claude_science_prompt` to paste into a Claude Science chat to produce the figure.

This is the private/local workflow: nothing is exposed on a network, no third party hosts it.
(The reasoning still calls Anthropic's cloud API and PubMed — de-identify the observation:
age band + presentation only, no names/MRNs/dates.)

Usage:
    bash mcp_server/ask.sh "33F, resistant hypertension on telmisartan + thiazide + CCB; raised CRP"
"""
from __future__ import annotations

import asyncio
import sys
import textwrap


def main() -> int:
    obs = " ".join(sys.argv[1:]).strip()
    if len(obs) < 3:
        print('usage: bash mcp_server/ask.sh "<de-identified clinical observation>"', file=sys.stderr)
        return 2

    # Imported here so a bad/missing arg errors instantly without loading the engine.
    from transbench.engine import run_transbench

    print(f"\nObservation: {obs}\n(running the grounded pipeline — ~1–3 min, real PubMed + LLM)…\n", file=sys.stderr)
    brief = asyncio.run(run_transbench(obs, None, user_key=None))  # user_key=None -> .env ANTHROPIC_API_KEY

    hyps = brief.hypotheses or []
    refs = brief.references or []
    top = getattr(brief, "top_experiment", None)

    print("=" * 78)
    print(f"GROUNDED BRIEF — {len(hyps)} hypotheses, {len(refs)} real citations")
    print("=" * 78)
    for h in hyps:
        stmt = getattr(getattr(h, "hypothesis", None), "statement", "") or ""
        print(f"\n• [{h.novelty}] (support {h.supporting_count} / contra {h.contradicting_count})")
        print(textwrap.fill(stmt, width=78, initial_indent="  ", subsequent_indent="  "))

    prompt = getattr(top, "claude_science_prompt", None) if top else None
    print("\n" + "=" * 78)
    print("PASTE THIS INTO CLAUDE SCIENCE  (it will load the dataset and produce the figure)")
    print("=" * 78 + "\n")
    print(prompt or "(no experiment prompt was produced)")
    print("\n" + "-" * 78)
    print(getattr(brief, "disclaimer", "") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
