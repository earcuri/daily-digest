#!/usr/bin/env bash
# run-akita-actions.sh — Execute Akita action items from today's digest
#
# Reads the "## Akita Actions" section from today's digest, runs Claude to
# execute the research/fetch/summarize tasks, writes results to vault, and
# appends a link back to the digest.
#
# Usage:
#   ./run-akita-actions.sh          # Run for today
#   ./run-akita-actions.sh --cron   # Quieter output for systemd

set -euo pipefail

DIGEST_DIR="$HOME/vault/claude/digests"
VAULT_AKITA="$HOME/vault/akita"
TODAY=$(date +%Y-%m-%d)
DIGEST_FILE="$DIGEST_DIR/$TODAY.md"
AKITA_FILE="$VAULT_AKITA/$TODAY.md"
LOG="$HOME/.logs/akita/akita.log"
QUIET="${1:-}"

mkdir -p "$VAULT_AKITA" "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "Starting Akita actions for $TODAY"

# Skip if no digest exists yet
if [[ ! -f "$DIGEST_FILE" ]] || [[ ! -s "$DIGEST_FILE" ]]; then
    log "No digest found at $DIGEST_FILE — skipping"
    exit 0
fi

# Extract Akita Actions section
AKITA_SECTION=$(python3 << PYEOF
import re, sys

with open("${DIGEST_FILE}") as f:
    content = f.read()

# Find ## Akita Actions section
match = re.search(r'##\s+Akita Actions\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
if match:
    section = match.group(1).strip()
    if section and "No Akita actions today" not in section:
        print(section)
    else:
        print("")
else:
    print("")
PYEOF
)

if [[ -z "$AKITA_SECTION" ]]; then
    log "No Akita actions found in today's digest — skipping"
    exit 0
fi

log "Found Akita actions to process"

# Skip if akita file already exists and is non-empty
if [[ -f "$AKITA_FILE" ]] && [[ -s "$AKITA_FILE" ]]; then
    log "Akita file for $TODAY already exists, skipping"
    exit 0
fi

PROMPT="You are Akita, Edward's AI assistant. Today is ${TODAY}. You are running as part of an automated pipeline.

Edward has left the following action items for you from his daily digest. Each item may ask you to:
- Research a topic and summarize key findings
- Fetch and read a URL or article
- Look up information about a person, company, or concept
- Draft content or notes
- Any other research or synthesis task

=== AKITA ACTION ITEMS FROM TODAY'S DIGEST ===

${AKITA_SECTION}

=== YOUR TASK ===

For each action item:
1. Execute the action using your tools (WebFetch for URLs/articles, Read for local files)
2. Write a clear, useful summary of what you found
3. Keep it actionable — Edward will read this tomorrow morning

Write your results to: ${AKITA_FILE}

Start the file with:
# Akita Research — ${TODAY}
_Processed at $(date '+%I:%M %p') MT_

For each action item, use:
## [Action description]

[Your findings, 1-3 paragraphs or bullets]

=== RULES ===
- Be concise and specific — this is a morning briefing, not an essay
- Always write to the file, never output to stdout
- If a URL is unavailable, note it and summarize what you know from context
- If an action is ambiguous, use best judgment and note your interpretation"

ALLOWED_TOOLS="Read,Write,WebFetch"

log "Running Claude CLI for Akita actions..."

CLAUDE_FAILED=0
if [[ "$QUIET" == "--cron" ]]; then
    claude -p "$PROMPT" --allowedTools "$ALLOWED_TOOLS" >> "$LOG" 2>&1 || CLAUDE_FAILED=1
else
    claude -p "$PROMPT" --allowedTools "$ALLOWED_TOOLS" || CLAUDE_FAILED=1
fi

if [[ $CLAUDE_FAILED -eq 1 ]]; then
    log "WARNING: Claude CLI returned error for Akita actions"
fi

# If vault file was written, append link to digest
if [[ -f "$AKITA_FILE" ]] && [[ -s "$AKITA_FILE" ]]; then
    log "Akita vault file written: $AKITA_FILE"

    # Append link to today's digest under Akita Actions section
    python3 << PYEOF
import re

digest_path = "${DIGEST_FILE}"
akita_path = "${AKITA_FILE}"

with open(digest_path) as f:
    content = f.read()

link_line = f"\n\n> Akita research complete: [{akita_path}]({akita_path})"

if link_line.strip() not in content:
    # Append after Akita Actions section
    content = re.sub(
        r'(##\s+Akita Actions.*?)(\n##|\Z)',
        lambda m: m.group(1) + link_line + m.group(2),
        content,
        flags=re.DOTALL
    )
    with open(digest_path, 'w') as f:
        f.write(content)
    print("Digest updated with Akita vault link")
PYEOF
else
    log "WARNING: Akita vault file not produced"
fi

log "Done"
