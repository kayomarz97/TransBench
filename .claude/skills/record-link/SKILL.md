---
name: record-link
description: Put TransBench into golden mode and hand the user a fresh single-use Claude Science link to record the demo against. Invoke when the user says "link to record skill", "record link", "golden link", "give me the recording link", or "/record-link". Golden mode makes generate_experiment return the pre-captured lupus brief INSTANTLY on camera (no ~60-120s live wait). The user records with OBS themselves — this never starts a recording.
trigger: /record-link
---

# /record-link — flip to golden, verify, hand over a recording link

The user is about to record the Claude Science segment and wants a clean, instant take.
Do exactly this, in order — do not improvise, do not start any recording.

## Steps

1. **Run the engine script** from the repo root:
   ```bash
   bash scripts/record-golden.sh
   ```
   It engages golden mode (a reversible systemd drop-in on `transbench-mcp.service`), runs the
   verify-then-link pre-flight, and prints a **fresh single-use (~3 min) Claude Science link** plus the
   on-camera reminders.

2. **If the pre-flight fails** (the script exits non-zero / shows any `✗`): STOP. Report the exact failing
   check to the user in plain language and do **not** present a link — a link into a broken run is the one
   failure the prep doc exists to prevent. Common fixes: Claude Science daemon down → they restart it; tunnel
   unhealthy → check the service. Then offer to re-run.

3. **On success**, relay to the user, in two registers (plain first, then the technical line):
   - the **fresh link** verbatim (it is the user's own single-use login link — safe to send to them; never
     commit it, never send it anywhere but to them),
   - the **SSH tunnel reminder**: `ssh -N -L 8000:localhost:8000 <you@server>` must be up, then open the link
     in their Windows browser,
   - **both copy-paste prompts, each in its own fenced code block** so the chat's copy button grabs them
     cleanly (the script prints them and also writes `demo/recording_prompts.txt`):
       - **PROMPT 1** — paste FIRST into Claude Science; it makes the connector run `generate_experiment` and
         the grounded brief appears instantly (golden). It already embeds the verbatim lupus observation.
       - **PROMPT 2** — paste NEXT into a new CS chat; it is the brief's own `claude_science_prompt` and draws
         the figure (the real live run). Same text the brief hands them — provided ahead so nothing is copied
         off-screen mid-take.
   - remind them to **hold the figure ~2s** before stopping OBS.

4. **Remind about revert.** Tell them that when filming is done, restore live with:
   ```bash
   bash scripts/record-golden.sh --revert
   ```
   (or they can just say "revert to live" and you'll run it).

## Guardrails
- **You never press record** — the user runs OBS manually.
- The link is single-use and expires in ~3 minutes; if it lapses, just re-run the skill for a fresh one.
- Full context: `demo/RECORDING_RUNBOOK.md` (the human runbook) and `demo/CLAUDE_SCIENCE_RECORDING.md`.
