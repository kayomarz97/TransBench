#!/usr/bin/env bash
#
# scan-secrets.sh — portable, zero-dependency secret & sensitive-file scanner.
#
# Single source of truth for two callers:
#   • .githooks/pre-push       (automated backstop — blocks a push on findings)
#   • the `secret-scanner` agent (interactive review before a push)
#
# Engines:
#   • a high-precision regex baseline (always runs; no dependencies), plus
#   • gitleaks as a second opinion IF it happens to be installed (auto-detected).
#
# Design notes:
#   • Only  file:line  rule-name  is printed — never the secret value — so logs stay clean.
#   • Patterns require a REALISTIC value, so docs that show placeholders (e.g. "sk-ant-…")
#     do not self-match. Guard/doc files are also on a self-exclude list as a second defense.
#
# Usage:  scan-secrets.sh [--staged | --all | --pre-push]   (default: --staged)
#
set -euo pipefail

MODE="${1:---staged}"
case "$MODE" in
  -h|--help) sed -n '2,20p' "$0"; exit 0;;
  --staged|--all|--pre-push) ;;
  *) echo "usage: $0 [--staged|--all|--pre-push]" >&2; exit 2;;
esac

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

# Files that legitimately contain secret PATTERNS as documentation — never content-scan these.
SELF_EXCLUDE='^(scripts/scan-secrets\.sh|\.githooks/pre-push|\.claude/agents/secret-scanner\.md|\.claude/AGENTS_PLAYBOOK\.md|CLAUDE\.md)$'

# Sensitive files that must NEVER be tracked/committed (structural check, content-independent).
SENSITIVE_FILE='(^|/)\.env($|\.[^e])|\.local\.md$|^demo/|\.pem$|\.p12$|(^|/)id_rsa$'

# Secret content patterns:  name<TAB>ERE.  Each demands a real-looking value.
PATTERNS=$(cat <<'EOF'
anthropic-api-key	sk-ant-[A-Za-z0-9_-]{24,}
aws-access-key-id	AKIA[0-9A-Z]{16}
google-api-key	AIza[0-9A-Za-z_-]{35}
github-token	gh[pousr]_[A-Za-z0-9]{36,}
slack-token	xox[baprs]-[A-Za-z0-9-]{10,}
private-key-block	-----BEGIN [A-Z ]*PRIVATE KEY-----
generic-secret-assignment	(api[_-]?key|secret|passwd|password|token)['"]?[[:space:]]*[:=][[:space:]]*['"][A-Za-z0-9/+=_-]{16,}['"]
EOF
)

findings=0
report() { printf '  %-5s %s\n' "$1" "$2"; }

# Resolve the set of files in scope for this mode.
case "$MODE" in
  --staged)          scope="$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)";;
  --all|--pre-push)  scope="$( { git ls-files; git ls-files --others --exclude-standard; } 2>/dev/null | sort -u || true)";;
esac

# Read staged blob (index) in --staged mode; the on-disk file otherwise.
get_content() {
  if [ "$MODE" = "--staged" ]; then git show ":$1" 2>/dev/null || true
  else cat "$1" 2>/dev/null || true; fi
}

echo "🔎 scan-secrets ($MODE) — $(printf '%s\n' "$scope" | grep -c . || true) file(s) in scope"

# 1) Structural: sensitive files that should never be here.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if printf '%s' "$f" | grep -qE "$SENSITIVE_FILE"; then
    report "HIGH" "$f  → sensitive file must not be committed (gitignore it / remove it)"
    findings=$((findings + 1))
  fi
done <<< "$scope"

# 2) Content: high-precision secret patterns (skip self/doc files and binaries).
mapfile -t FILES < <(printf '%s\n' "$scope" | grep -Ev "$SELF_EXCLUDE" || true)
for f in "${FILES[@]}"; do
  [ -n "$f" ] || continue
  content="$(get_content "$f")"
  [ -n "$content" ] || continue
  while IFS=$'\t' read -r name regex; do
    [ -n "$name" ] || continue
    while IFS= read -r ln; do
      [ -n "$ln" ] || continue
      report "HIGH" "$f:$ln  → $name"
      findings=$((findings + 1))
    done < <(printf '%s\n' "$content" | grep -nEI "$regex" 2>/dev/null | cut -d: -f1 || true)
  done <<< "$PATTERNS"
done

# 3) Second opinion: gitleaks, only if present. Never required.
if command -v gitleaks >/dev/null 2>&1; then
  echo "· gitleaks found — running as a second engine"
  if [ "$MODE" = "--staged" ]; then
    gitleaks protect --staged --no-banner --redact >/dev/null 2>&1 || { report "HIGH" "gitleaks flagged staged content"; findings=$((findings + 1)); }
  else
    gitleaks detect --no-banner --redact >/dev/null 2>&1 || { report "HIGH" "gitleaks flagged repo content"; findings=$((findings + 1)); }
  fi
fi

echo
if [ "$findings" -gt 0 ]; then
  echo "❌ $findings potential secret/sensitive finding(s) above."
  echo "   Fix: remove the value, gitignore the file, and (if ever committed) scrub it from history."
  echo "   Deliberate override (use with care): SKIP_SECRET_SCAN=1 git push"
  exit 1
fi
echo "✅ clean — no secrets or sensitive files detected ($MODE)."
exit 0
