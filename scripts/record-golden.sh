#!/usr/bin/env bash
# record-golden.sh — put TransBench into GOLDEN mode for a clean, instant,
# deterministic on-camera demo, verify it end-to-end, then print a FRESH
# single-use Claude Science login link to record against. Fully reversible.
#
# WHY: on camera you never want the real ~60-120s live pipeline. Golden mode
# serves the pre-captured lupus brief verbatim (exact-observation match ONLY —
# it cannot answer any other question), so `generate_experiment` returns
# instantly inside Claude Science. This flips the single control point: the
# systemd service that backs your private HTTPS-tunnel connector.
#
# USAGE
#   bash scripts/record-golden.sh            # ENGAGE golden, verify, print a fresh CS link
#   bash scripts/record-golden.sh --revert   # restore LIVE (run this AFTER you finish filming)
#   bash scripts/record-golden.sh --status   # show the current mode, change nothing
#
# It is idempotent (safe to run repeatedly) and reversible (a systemd drop-in,
# never an edit to the packaged unit). Runs as root; falls back to sudo.
set -euo pipefail

# ── knobs (override via env if your box differs) ────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="${TRANSBENCH_SERVICE:-transbench-mcp}"
MCP_PORT="${MCP_HTTP_PORT:-8500}"
APP="${CLAUDE_SCIENCE_BIN:-/root/Downloads/claude-science-linux-x64}"
SNAPSHOT="${GOLDEN_SNAPSHOT:-snapshots/autoimmune_sle_treg_golden_brief.json}"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN="${DROPIN_DIR}/golden.conf"
TUNNEL_PORT="${CLAUDE_SCIENCE_PORT:-8000}"   # the CS daemon's real port (for the SSH -L reminder)
cd "$REPO_ROOT"

# ── tiny helpers ────────────────────────────────────────────────────────────
c_ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
c_info() { printf '  \033[36m•\033[0m %s\n' "$*"; }
c_warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
c_err()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }
hr()     { printf '%s\n' "────────────────────────────────────────────────────────────"; }
die()    { c_err "$*"; exit 1; }

run_root() {                      # run a privileged command as root, or via sudo
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

service_pid() { systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0; }

current_mode() {                  # read TRANSBENCH_MODE from the RUNNING service; default 'live'
  local pid; pid="$(service_pid)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 0 ]] && [[ -r "/proc/$pid/environ" ]]; then
    local m; m="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^TRANSBENCH_MODE=//p' | head -1)"
    printf '%s' "${m:-live}"
  else
    printf 'unknown'
  fi
}

wait_for_port() {                 # block until the MCP server is listening again (max ~15s)
  local i
  for i in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${MCP_PORT}") 2>/dev/null; then exec 3>&- 2>/dev/null; return 0; fi
    sleep 0.5
  done
  return 1
}

# ── actions ─────────────────────────────────────────────────────────────────
do_status() {
  hr; printf '  TransBench mode: \033[1m%s\033[0m   (service: %s, pid %s)\n' "$(current_mode)" "$SERVICE" "$(service_pid)"; hr
}

write_dropin_golden() {           # idempotent: only write+reload if content differs
  local want; want=$'[Service]\nEnvironment=TRANSBENCH_MODE=golden'
  if [[ -f "$DROPIN" ]] && [[ "$(cat "$DROPIN")" == "$want" ]]; then
    c_ok "golden drop-in already in place ($DROPIN)"
  else
    run_root mkdir -p "$DROPIN_DIR"
    printf '%s\n' '[Service]' 'Environment=TRANSBENCH_MODE=golden' | run_root tee "$DROPIN" >/dev/null
    run_root systemctl daemon-reload
    c_ok "wrote golden drop-in + reloaded systemd"
  fi
}

verify_golden() {                 # the "verify-then-link" pre-flight
  local ok=1
  # (a) the running service must actually be in golden
  if [[ "$(current_mode)" == "golden" ]]; then c_ok "service env is TRANSBENCH_MODE=golden"
  else c_err "service env is NOT golden (got '$(current_mode)')"; ok=0; fi
  # (b) the golden brief must match its own observation offline (proves snapshot + exact-match guard)
  if env -u ANTHROPIC_API_KEY TRANSBENCH_MODE=golden PYTHONPATH="$REPO_ROOT/src" \
       "$REPO_ROOT/.venv/bin/python" - "$SNAPSHOT" >/dev/null 2>&1 <<'PY'
import sys, json
from transbench.engine import _try_load_golden_brief
obs = json.load(open(sys.argv[1]))["request_echo"]
sys.exit(0 if _try_load_golden_brief(obs) is not None else 1)
PY
  then c_ok "golden brief loads + matches the lupus observation (offline, no key)"
  else c_err "golden brief did NOT match — the paste would fall back to a live run"; ok=0; fi
  # (c) Claude Science daemon must be up
  if "$APP" status 2>/dev/null | grep -q '"running": *true'; then c_ok "Claude Science daemon is running"
  else c_err "Claude Science daemon is not running (start it, then re-run)"; ok=0; fi
  [[ "$ok" -eq 1 ]]
}

print_link_and_reminders() {
  local obs; obs="$(env -u ANTHROPIC_API_KEY PYTHONPATH="$REPO_ROOT/src" "$REPO_ROOT/.venv/bin/python" \
                    -c "import json,sys;print(json.load(open(sys.argv[1]))['request_echo'])" "$SNAPSHOT")"
  echo; hr
  printf '  \033[1mFRESH CLAUDE SCIENCE LINK\033[0m  (single-use, expires ~3 min — open it now)\n'; hr
  "$APP" url || die "could not mint a Claude Science link ('$APP url' failed)"
  echo; hr
  printf '  \033[1mBEFORE YOU HIT RECORD\033[0m\n'; hr
  c_info "1. Tunnel must forward the REAL port:  ssh -N -L ${TUNNEL_PORT}:localhost:${TUNNEL_PORT} <you@server>"
  c_info "   then open the link above in your Windows browser."
  c_info "2. In Claude Science, paste this observation VERBATIM (golden matches only an exact copy):"
  printf '\n     \033[36m%s\033[0m\n\n' "$obs"
  c_info "3. Call generate_experiment → brief appears INSTANTLY → paste PROMPT 2 (printed below) into a"
  c_info "   CS chat → let the figure render → hold it ~2s → stop OBS. (Both prompts are ready-to-copy below.)"
  c_warn "When you are done filming, restore live:  bash scripts/record-golden.sh --revert"
  hr
}

emit_prompts() {                  # print BOTH ready-to-paste Claude Science prompts (clean, no ANSI)
  "$REPO_ROOT/.venv/bin/python" - "$SNAPSHOT" "$REPO_ROOT/demo/recording_prompts.txt" <<'PY'
import sys, json, pathlib
snap, outfile = sys.argv[1], sys.argv[2]
b = json.load(open(snap))
obs = b["request_echo"]
csp = b["top_experiment"]["claude_science_prompt"]
prompt1 = ("Use the TransBench connector's generate_experiment tool on this de-identified clinical "
           "observation, then show me the returned brief — the biological axes, the falsifiable "
           "hypotheses with their real PMIDs, and the experiment's claude_science_prompt:\n\n" + obs)
bar = "┄" * 74
def block(title, body):
    return f"▶ {title}\n{bar}\n{body}\n{bar}"
pathlib.Path(outfile).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(outfile).write_text(
    "TransBench demo — copy-paste prompts for Claude Science (golden mode)\n"
    "Regenerated from the golden brief on each engage; safe to copy, no secrets.\n\n"
    "=== PROMPT 1 — paste FIRST (makes the grounded brief appear; instant in golden) ===\n\n"
    + prompt1 + "\n\n"
    "=== PROMPT 2 — paste NEXT (makes the figure render; identical to the prompt the brief hands you) ===\n\n"
    + csp + "\n", encoding="utf-8")
print("\n" + "═"*20 + " COPY-PASTE PROMPTS (paste into Claude Science) " + "═"*20 + "\n")
print(block("PROMPT 1 — paste FIRST: makes the grounded brief appear (instant in golden)", prompt1) + "\n")
print(block("PROMPT 2 — paste NEXT: draws the figure (same text the brief hands you)", csp) + "\n")
print(f"(also saved for easy copying: {outfile})")
PY
}

engage() {
  hr; printf '  \033[1mENGAGING GOLDEN MODE\033[0m for a clean on-camera take\n'; hr
  [[ -x "$REPO_ROOT/.venv/bin/python" ]] || die "venv python missing — run 'uv sync' first"
  write_dropin_golden
  if [[ "$(current_mode)" != "golden" ]]; then
    run_root systemctl restart "$SERVICE"
    wait_for_port || die "service did not come back on :${MCP_PORT} after restart"
    c_ok "restarted $SERVICE (now on :${MCP_PORT})"
  else
    c_ok "$SERVICE already running in golden — no restart needed"
  fi
  echo; c_info "pre-flight (verify-then-link):"
  verify_golden || die "pre-flight FAILED — not printing a link into a broken take. Fix the ✗ above and retry."
  print_link_and_reminders
  emit_prompts
}

revert() {
  hr; printf '  \033[1mRESTORING LIVE MODE\033[0m (the everyday default)\n'; hr
  if [[ -f "$DROPIN" ]]; then
    run_root rm -f "$DROPIN"
    run_root systemctl daemon-reload
    run_root systemctl restart "$SERVICE"
    wait_for_port || die "service did not come back on :${MCP_PORT} after restart"
    c_ok "removed golden drop-in + restarted $SERVICE"
  else
    c_ok "no golden drop-in present — already live"
  fi
  do_status
}

# ── dispatch ────────────────────────────────────────────────────────────────
case "${1:-}" in
  ""|--engage|--golden) engage ;;
  --revert|--live)      revert ;;
  --status)             do_status ;;
  --prompts)            emit_prompts ;;
  -h|--help)            sed -n '2,20p' "$0" ;;
  *) die "unknown option: $1  (use: --engage | --revert | --status)" ;;
esac
