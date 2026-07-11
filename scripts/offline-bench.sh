#!/usr/bin/env bash
#
# offline-bench.sh — the deterministic, FREE, offline bench for TransBench.
#
# WHY THIS EXISTS (the gotcha it solves):
#   config.py loads .env with override=True, and the live tests gate on
#   `skipif(not ANTHROPIC_API_KEY)`. So on a machine whose .env holds a real key, a plain
#   `pytest` SILENTLY runs the live pipeline — real Anthropic + PubMed calls, minutes of
#   wall-clock, real money. This wrapper forces the true offline condition (the same one CI
#   runs under) no matter what's on disk:
#     • temporarily hides .env behind a restore-trap (never deleted, always put back),
#     • strips ANTHROPIC_API_KEY / PUBMED_API_KEY from the environment,
#     • runs in TRANSBENCH_MODE=golden (snapshot replay, no network).
#   Result: ~2s, no key, no network, deterministic, green.
#
# Usage:  bash scripts/offline-bench.sh [extra pytest args...]
#
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

BAK="$ROOT/.env.offline-bench.bak"
if [ -e "$BAK" ]; then
  echo "❌ $BAK already exists — a previous bench may have crashed mid-run."
  echo "   Inspect it, then restore your key file with:  mv '$BAK' '$ROOT/.env'"
  exit 3
fi

# Always put .env back — on success, failure, or Ctrl-C. The key file is only ever moved,
# never modified or deleted, and never printed.
restore() { [ -e "$BAK" ] && mv -f "$BAK" "$ROOT/.env"; }
trap restore EXIT INT TERM
[ -f "$ROOT/.env" ] && mv "$ROOT/.env" "$BAK"

# NOTE (CODE_REVIEW Finding 6): the two live-fallback tests (run_retrieve's snapshot-miss and
# statement-mismatch cases) were previously --deselect'd here — one of them left
# `gather_extra_sources` un-mocked, so it hit live Europe PMC and was non-deterministic offline.
# Both now mock that backend like their siblings, so they are genuinely offline + deterministic
# and run in this bench with NO manual exclusions. Keep it that way: never re-add a --deselect to
# hide a failure — fix the test's un-mocked seam instead.

echo "🧪 offline bench — no key, golden replay, no manual exclusions"
env -u ANTHROPIC_API_KEY -u PUBMED_API_KEY TRANSBENCH_MODE=golden \
  "$ROOT/.venv/bin/python" -m pytest -q -p no:cacheprovider "$@"
rc=$?

echo
if [ "$rc" -eq 0 ]; then
  echo "✅ offline bench GREEN — deterministic, no key, no network."
else
  echo "❌ offline bench FAILED (pytest exit $rc). Report it honestly — do not edit tests to hide it."
fi
exit "$rc"
