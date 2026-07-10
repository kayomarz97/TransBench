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
#     • runs in TRANSBENCH_MODE=golden (snapshot replay, no network),
#     • deselects the tests that BY DESIGN exercise the live-fallback path.
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

# Tests that make a REAL API call BY DESIGN — they assert the live-FALLBACK path taken when a
# snapshot deliberately doesn't match. Unlike every other live test, they lack the
# `skipif(not ANTHROPIC_API_KEY)` guard their siblings have, so they cannot pass offline.
# LONG-TERM FIX: add that guard to these two upstream; until then they're out of OFFLINE scope
# (run them in the live suite, with a key). See the mistakes ledger in .claude/AGENTS_PLAYBOOK.md.
DESELECT=(
  "tests/test_experiment_phase5.py::test_run_retrieve_falls_through_to_live_when_snapshot_has_no_matching_entry"
  "tests/test_snapshot_toggle.py::test_statement_mismatch_falls_back_to_live_retrieval"
)
desel_args=()
for n in "${DESELECT[@]}"; do desel_args+=(--deselect "$n"); done

echo "🧪 offline bench — no key, golden replay, ${#DESELECT[@]} live-fallback test(s) deselected"
env -u ANTHROPIC_API_KEY -u PUBMED_API_KEY TRANSBENCH_MODE=golden \
  "$ROOT/.venv/bin/python" -m pytest -q -p no:cacheprovider "${desel_args[@]}" "$@"
rc=$?

echo
if [ "$rc" -eq 0 ]; then
  echo "✅ offline bench GREEN — deterministic, no key, no network."
else
  echo "❌ offline bench FAILED (pytest exit $rc). Report it honestly — do not edit tests to hide it."
fi
exit "$rc"
