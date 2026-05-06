#!/usr/bin/env bash
# generate-digest.sh — Generate daily digest using GWS CLI + Claude Code
#
# Architecture: GWS CLI pre-fetches Gmail + Calendar data, passes it as
# context to Claude. Claude summarizes and writes the digest file.
# No cloud MCP tokens required — only local GWS CLI auth.
#
# Usage:
#   ./generate-digest.sh          # Generate digest for today
#   ./generate-digest.sh --cron   # Same, quieter output for cron/systemd

set -euo pipefail

DIGEST_DIR="$HOME/vault/claude/digests"
TODAY=$(date +%Y-%m-%d)
DIGEST_FILE="$DIGEST_DIR/$TODAY.md"
LOG_DIR="$DIGEST_DIR/logs"
LOG="$LOG_DIR/generate.log"
QUIET="${1:-}"
BOXING_SCHEDULE="$DIGEST_DIR/boxing-schedule.md"
TECH_CONFIG="$DIGEST_DIR/digest-tech-config.md"
GWS="${HOME}/.local/bin/gws"
TECH_IDS_FILE="$LOG_DIR/.tech-ids-$TODAY.tmp"
AKITA_IDS_FILE="$LOG_DIR/.akita-ids-$TODAY.tmp"

mkdir -p "$DIGEST_DIR" "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "Starting digest generation for $TODAY"

# Skip if today's digest already exists and is non-empty
if [[ -f "$DIGEST_FILE" ]] && [[ -s "$DIGEST_FILE" ]]; then
    log "Digest for $TODAY already exists, skipping"
    exit 0
fi

# ---------------------------------------------------------------------------
# Fetch inbox/career/news email data via GWS CLI
# ---------------------------------------------------------------------------

log "Fetching Gmail inbox data..."

EMAIL_DATA=$(python3 << 'PYEOF'
import subprocess, json, sys, os

gws = os.path.expanduser("~/.local/bin/gws")

def gws_call(*args):
    result = subprocess.run([gws] + list(args), capture_output=True, text=True)
    raw = result.stdout.strip()
    lines = raw.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('{') or line.startswith('['):
            raw = '\n'.join(lines[i:])
            break
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}

# Unread + inbox messages for career/news/general sections
unread = gws_call("gmail", "users", "messages", "list",
                  "--params", '{"userId":"me","q":"is:unread -label:INBOX/Tech","maxResults":60}')
inbox = gws_call("gmail", "users", "messages", "list",
                 "--params", '{"userId":"me","q":"in:inbox -is:unread -label:INBOX/Tech","maxResults":40}')

all_ids = []
seen = set()
for lst in [unread.get("messages", []), inbox.get("messages", [])]:
    for m in lst:
        mid = m.get("id", "")
        if mid and mid not in seen:
            all_ids.append(mid)
            seen.add(mid)

IMPORTANT_PATTERNS = [
    "scott.stevens", "scottstevens", "kevin.walshaw", "kevinwalshaw",
    "turbalance", "verkada", "alert@", "fraud", "security-noreply",
    "noreply@chase", "noreply@bankofamerica", "noreply@wellsfargo",
    "allexia", "allexia.arcuri",
]

SKIP_PATTERNS = [
    "wyze", "amazon.com", "paypal", "no-reply@amazon",
    "shipment-tracking", "auto-confirm", "noreply@ebay",
    "em.pennymac.com", "tommys-express.com", "omegamino.net",
    "loopnet.com", "morenzio.com", "marketdisruptors.io",
    "porsche.us", "podcompany.com", "dimo.zone", "email.bose.com",
]

messages = []
important_count = 0

for mid in all_ids[:80]:
    meta = gws_call("gmail", "users", "messages", "get",
                    "--params", json.dumps({
                        "userId": "me", "id": mid,
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject", "Date", "List-Unsubscribe"]
                    }))
    if not meta:
        continue

    headers = {h["name"].lower(): h["value"]
               for h in meta.get("payload", {}).get("headers", [])}
    from_addr = headers.get("from", "").lower()
    subject = headers.get("subject", "(no subject)")
    date_h = headers.get("date", "")
    snippet = meta.get("snippet", "")
    is_newsletter = "list-unsubscribe" in headers

    if any(p in from_addr for p in SKIP_PATTERNS):
        continue

    is_important = any(p in from_addr for p in IMPORTANT_PATTERNS)

    msg_data = {
        "id": mid,
        "from": headers.get("from", ""),
        "subject": subject,
        "date": date_h,
        "snippet": snippet,
        "is_newsletter": is_newsletter,
        "full_body": None,
    }

    if is_important and important_count < 10:
        full = gws_call("gmail", "users", "messages", "get",
                        "--params", json.dumps({
                            "userId": "me", "id": mid, "format": "full"
                        }))
        def extract_body(payload):
            if payload.get("mimeType") == "text/plain":
                import base64
                data = payload.get("body", {}).get("data", "")
                try:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")[:2000]
                except Exception:
                    return ""
            for part in payload.get("parts", []):
                result = extract_body(part)
                if result:
                    return result
            return ""
        msg_data["full_body"] = extract_body(full.get("payload", {}))
        important_count += 1

    messages.append(msg_data)

output = []
output.append(f"=== INBOX EMAIL DATA ({len(messages)} messages) ===\n")

for m in messages:
    line = f"FROM: {m['from']}\nSUBJECT: {m['subject']}\nDATE: {m['date']}\n"
    if m["full_body"]:
        line += f"BODY:\n{m['full_body'][:1500]}\n"
    else:
        line += f"SNIPPET: {m['snippet']}\n"
    if m["is_newsletter"]:
        line += "[NEWSLETTER]\n"
    output.append(line)
    output.append("---")

print("\n".join(output))
PYEOF
)

if [[ -z "$EMAIL_DATA" ]]; then
    log "WARNING: GWS inbox fetch returned empty"
    EMAIL_DATA="(Gmail fetch failed — GWS CLI may need re-auth: run 'gws auth login')"
fi

# ---------------------------------------------------------------------------
# Fetch tech newsletter data grouped by label
# ---------------------------------------------------------------------------

log "Fetching Tech label data..."

export TECH_IDS_FILE

TECH_DATA=$(python3 << PYEOF
import subprocess, json, os, re, base64

gws = os.path.expanduser("~/.local/bin/gws")
ids_file = "${TECH_IDS_FILE}"

URL_SKIP = ["unsubscribe", "pixel", "open.php", "mailchimp", "sendgrid",
            "mandrillapp", "list-manage", "track.", "beacon", ".gif?",
            "email-tracking", "click.convertkit", "click.mlsend",
            "account", "login", "signup", "subscribe", "manage", "preferences"]

def gws_call(*args):
    result = subprocess.run([gws] + list(args), capture_output=True, text=True)
    raw = result.stdout.strip()
    lines = raw.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('{') or line.startswith('['):
            raw = '\n'.join(lines[i:])
            break
    try:
        return json.loads(raw)
    except:
        return {}

def extract_body_text(payload):
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""
    for part in payload.get("parts", []):
        result = extract_body_text(part)
        if result:
            return result
    return ""

def extract_article_urls(text):
    found = re.findall(r'https?://[^\s<>"{}|\\\[\]]{15,}', text)
    clean = []
    seen = set()
    for u in found:
        u = u.rstrip('.,;)')
        if u in seen:
            continue
        if any(s in u.lower() for s in URL_SKIP):
            continue
        if len(u) > 300:
            continue
        clean.append(u)
        seen.add(u)
        if len(clean) >= 3:
            break
    return clean

# Label groups: (display_name, priority, [label_paths], max_per_path)
# AI and System Design sub-labels queried individually to ensure full coverage.
# labelIds array param breaks GWS CLI (passes as string); use q search instead.
TECH_LABEL_GROUPS = [
    ("AI", "HIGH", [
        "INBOX/Tech/AI/Ken Huang - Agentic AI",
        "INBOX/Tech/AI/Architects Notebook",
        "INBOX/Tech/AI/Chase AI",
        "INBOX/Tech/AI/AI Architect",
        "INBOX/Tech/AI/Deeplearning.AI",
        "INBOX/Tech/AI/Daily Dose of Data Science",
        "INBOX/Tech/AI/ElevenLabs",
        "INBOX/Tech/AI/Making AI Easy",
        "INBOX/Tech/AI/Maven",
        "INBOX/Tech/AI/Nate's Substack",
        "INBOX/Tech/AI/OpenAI",
        "INBOX/Tech/AI/PyImageSearch",
        "INBOX/Tech/AI",
    ], 2),
    ("The New Stack",          "HIGH",   ["INBOX/Tech/The New Stack"],          7),
    ("Pragmatic Engineer",     "HIGH",   ["INBOX/Tech/Pragmatic Engineer"],     7),
    ("ByteByteGo",             "HIGH",   ["INBOX/Tech/ByteByteGo"],             7),
    ("Engineering Leadership", "HIGH",   ["INBOX/Tech/Engineering Leadership"], 5),
    ("RedPanda",               "HIGH",   ["INBOX/Tech/RedPanda"],               5),
    ("Nvidia",                 "HIGH",   ["INBOX/Tech/Nvidia"],                 5),
    ("System Design",          "MEDIUM", [
        "INBOX/Tech/System Design",
        "INBOX/Tech/System Design/Course",
        "INBOX/Tech/System Design/Neo Kim",
        "INBOX/Tech/System Design/Roadmap",
    ], 3),
    ("Python",                 "MEDIUM", ["INBOX/Tech/Python"],                 5),
]

output = ["=== TECH NEWSLETTER DATA (by label) ===\n"]
all_tech_ids = []

for group_name, priority, label_paths, max_per_path in TECH_LABEL_GROUPS:
    seen = set()
    group_ids = []
    for label_path in label_paths:
        lst = gws_call("gmail", "users", "messages", "list",
                       "--params", json.dumps({
                           "userId": "me",
                           "q": f'label:"{label_path}"',
                           "maxResults": max_per_path
                       }))
        for m in lst.get("messages", []):
            mid = m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                group_ids.append(mid)

    if not group_ids:
        continue

    output.append(f"--- GROUP: {group_name} | PRIORITY: {priority} ---")
    for mid in group_ids:
        full = gws_call("gmail", "users", "messages", "get",
                        "--params", json.dumps({
                            "userId": "me", "id": mid, "format": "full"
                        }))
        headers = {h["name"].lower(): h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        from_addr = headers.get("from", "")
        date_h = headers.get("date", "")
        snippet = full.get("snippet", "")
        body_text = extract_body_text(full.get("payload", {}))
        article_urls = extract_article_urls(body_text)
        gmail_link = f"https://mail.google.com/mail/u/0/#all/{mid}"
        output.append(f"FROM: {from_addr}")
        output.append(f"SUBJECT: {subject}")
        output.append(f"DATE: {date_h}")
        output.append(f"SNIPPET: {snippet[:300]}")
        output.append(f"GMAIL_LINK: {gmail_link}")
        if article_urls:
            output.append(f"ARTICLE_URLS: {' | '.join(article_urls)}")
        output.append("")
        all_tech_ids.append(mid)

    output.append("")

try:
    with open(ids_file, 'w') as f:
        json.dump(all_tech_ids, f)
except Exception:
    pass

print("\n".join(output))
PYEOF
)

if [[ -z "$TECH_DATA" ]]; then
    log "WARNING: Tech label fetch returned empty"
    TECH_DATA="(Tech label fetch failed)"
fi

# ---------------------------------------------------------------------------
# Fetch __READ label for Akita notes/actions
# ---------------------------------------------------------------------------

log "Fetching __READ label for Akita notes..."

export AKITA_IDS_FILE

AKITA_NOTES=$(python3 << PYEOF
import subprocess, json, os, re, base64

gws = os.path.expanduser("~/.local/bin/gws")
READ_LABEL = "Label_3348452723284105606"
akita_ids_file = "${AKITA_IDS_FILE}"

URL_SKIP = ["unsubscribe", "pixel", "open.php", "click.", "mailchimp",
            "sendgrid", "mandrillapp", "list-manage", "track.", "beacon",
            ".gif?", "email-tracking"]

def gws_call(*args):
    result = subprocess.run([gws] + list(args), capture_output=True, text=True)
    raw = result.stdout.strip()
    lines = raw.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('{') or line.startswith('['):
            raw = '\n'.join(lines[i:])
            break
    try:
        return json.loads(raw)
    except:
        return {}

def extract_body_text(payload):
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""
    for part in payload.get("parts", []):
        result = extract_body_text(part)
        if result:
            return result
    return ""

def extract_urls(text):
    found = re.findall(r'https?://[^\s<>"{}|\\\[\]]{10,}', text)
    clean = []
    seen = set()
    for u in found:
        u = u.rstrip('.,;)')
        if u in seen:
            continue
        if any(s in u.lower() for s in URL_SKIP):
            continue
        clean.append(u)
        seen.add(u)
        if len(clean) >= 5:
            break
    return clean

lst = gws_call("gmail", "users", "messages", "list",
               "--params", json.dumps({
                   "userId": "me",
                   "q": 'label:"__READ"',
                   "maxResults": 10
               }))
msgs = lst.get("messages", [])
all_akita_ids = [m.get("id","") for m in msgs if m.get("id")]

try:
    with open(akita_ids_file, 'w') as f:
        json.dump(all_akita_ids, f)
except Exception:
    pass

if not msgs:
    print("=== AKITA NOTES (_read label) ===\nNo messages in _read label today.\n")
else:
    output = [f"=== AKITA NOTES (_read label — {len(msgs)} items) ===\n"]
    for m in msgs:
        mid = m.get("id", "")
        full = gws_call("gmail", "users", "messages", "get",
                        "--params", json.dumps({"userId": "me", "id": mid, "format": "full"}))
        headers = {h["name"].lower(): h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        from_addr = headers.get("from", "")
        snippet = full.get("snippet", "")
        body_text = extract_body_text(full.get("payload", {}))
        urls = extract_urls(body_text)
        output.append(f"FROM: {from_addr}")
        output.append(f"SUBJECT: {subject}")
        output.append(f"SNIPPET: {snippet[:500]}")
        output.append(f"GMAIL_LINK: https://mail.google.com/mail/u/0/#all/{mid}")
        if urls:
            output.append("URLS_IN_EMAIL:")
            for u in urls:
                output.append(f"  - {u}")
        output.append("---")
    print("\n".join(output))
PYEOF
)

# ---------------------------------------------------------------------------
# Fetch Calendar data
# ---------------------------------------------------------------------------

log "Fetching Calendar data..."

CALENDAR_DATA=$(python3 << PYEOF
import subprocess, json, os, sys
from datetime import datetime, timedelta, timezone

gws = os.path.expanduser("~/.local/bin/gws")
today = datetime.now().astimezone()
time_min = today.replace(hour=0, minute=0, second=0).isoformat()
time_max = (today + timedelta(days=7)).replace(hour=23, minute=59, second=59).isoformat()

def gws_call(*args):
    result = subprocess.run([gws] + list(args), capture_output=True, text=True)
    raw = result.stdout.strip()
    lines = raw.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('{') or line.startswith('['):
            raw = '\n'.join(lines[i:])
            break
    try:
        return json.loads(raw)
    except:
        return {}

calendars_to_check = [
    ("primary", "Primary"),
    ("family18375723734580498230@group.calendar.google.com", "Family"),
]

output = ["=== CALENDAR DATA (next 7 days) ===\n"]
for cal_id, cal_name in calendars_to_check:
    events = gws_call("calendar", "events", "list",
                      "--params", json.dumps({
                          "calendarId": cal_id,
                          "timeMin": time_min,
                          "timeMax": time_max,
                          "singleEvents": True,
                          "orderBy": "startTime",
                          "maxResults": 30
                      }))
    items = events.get("items", [])
    if items:
        output.append(f"Calendar: {cal_name}")
        for ev in items:
            start = ev.get("start", {})
            start_str = start.get("dateTime", start.get("date", ""))
            output.append(f"  - {start_str}: {ev.get('summary','(no title)')}")
    else:
        output.append(f"Calendar: {cal_name} — no events")

print("\n".join(output))
PYEOF
)

if [[ -z "$CALENDAR_DATA" ]]; then
    log "WARNING: GWS Calendar fetch returned empty"
    CALENDAR_DATA="(Calendar fetch failed)"
fi

# Read boxing schedule if it exists
BOXING_CONTENT=""
if [[ -f "$BOXING_SCHEDULE" ]]; then
    BOXING_CONTENT=$(cat "$BOXING_SCHEDULE")
fi

# Read tech priority config if it exists
TECH_CONFIG_CONTENT=""
if [[ -f "$TECH_CONFIG" ]]; then
    TECH_CONFIG_CONTENT=$(cat "$TECH_CONFIG")
fi

# ---------------------------------------------------------------------------
# Build prompt with pre-fetched data
# ---------------------------------------------------------------------------

PROMPT="You are writing Edward's daily digest for ${TODAY}. Edward is in Colorado Springs, CO (Mountain Time).

You have been given pre-fetched Gmail, Calendar, and Tech newsletter data below. Your only tools are Read (if needed) and Write (to write the digest file).

=== PRE-FETCHED DATA ===

${EMAIL_DATA}

${TECH_DATA}

${AKITA_NOTES}

${CALENDAR_DATA}

=== BOXING SCHEDULE ===
${BOXING_CONTENT:-No boxing-schedule.md configured yet.}

=== TECH NEWSLETTER PRIORITY CONFIG ===
${TECH_CONFIG_CONTENT:-Use your best judgment on tech content relevance.}

=== YOUR TASK ===

Write a complete digest to: ${DIGEST_FILE}

Start with:
# Edward's Daily Digest — ${TODAY}
_Generated at $(date '+%I:%M %p') MT_

SECTION ORDER: Weather, [Financial Alerts if any], Career, Boxing, Technology, News, Calendar, Newsletters, Akita Actions, Notes

Read the sender classification config at ${DIGEST_DIR}/digest-config.md for general priorities.

=== SECTION INSTRUCTIONS ===

## Weather
Fetch current conditions from Open-Meteo (lat=38.8339, lon=-104.8214) using WebFetch.
If unavailable write: 'Weather fetch unavailable — check weather.gov'
Format: bold summary line + code block (time icon temp wind rows).

## Financial Alerts (only if present)
Surface fraud alerts, unusual activity, large transactions. Always first.

## Career
TOP PRIORITY. From INBOX EMAIL DATA:
- Scott Stevens and Kevin Walshaw emails: surface in full with action items
- Turbalance, Verkada, Cursor, job offer, interview, CTO, Field CTO
- Required actions with [ACTION] prefix

## Boxing
Use the boxing schedule above. Show today's session or 'Rest day'.
Note calendar conflicts.

## Technology
Use TECH NEWSLETTER DATA for this section. Group by label name.

### [Group Name]

- **[Subject line](GMAIL_LINK value)** — summary

FORMATTING (critical):
- Copy the exact GMAIL_LINK value as the markdown link URL — do NOT invent links
- Format every bullet as: **[subject](GMAIL_LINK)** — summary

CONTENT RULES:
- HIGH priority groups: include ALL emails, 1-2 sentence summaries
- MEDIUM priority groups: include only emails on Interest Topics (see config); 1-line summaries
- Omit groups with no messages entirely

ARTICLE FETCHING:
- For HIGH priority emails that have ARTICLE_URLS in the data, use WebFetch on the first URL
- Write your summary based on the actual article content, not just the snippet
- Limit total WebFetch calls to 10 across the entire Technology section
- If WebFetch fails or returns a paywall, fall back to the SNIPPET

INTEREST FILTER (from digest-tech-config.md):
- SURFACE prominently: agentic AI, multi-agent systems, AI infra (CPU/memory/inference hardware), system design for high-throughput data, Kafka, RedPanda, streaming pipelines, engineering leadership, context optimization for agents, Field CTO/technical strategy
- ONE-LINER only (even in HIGH groups): AWS/GCP introductory marketing, Elastic/ELK, LLM training math, basic data science, SaaS product announcements
- Do NOT skip emails from HIGH labels — always show them, but with appropriate depth

## News
4-6 headlines from news sources in INBOX EMAIL DATA. One sentence each.

## Calendar
All events from calendar data grouped by day. Flag [ACTION] for anything needing prep.

## Newsletters
One-line summary for relevant newsletters from INBOX EMAIL DATA.
Count junk: 'X emails appear to be junk.'
SKIP: PayPal, Amazon receipts, Wyze, routine transactions.

## Akita Actions
From AKITA NOTES section above, extract action items Edward has noted.
Each item as a bullet:
- **[Action type]:** [description] — **[email subject](GMAIL_LINK value)**
- If the email has URLS_IN_EMAIL, list them as sub-bullets under the action item
- Use the exact GMAIL_LINK value from the data as the email link target
If no items in _read label, write: 'No Akita actions today.'

## Notes
Leave blank. Edward adds notes via the dashboard.

=== RULES ===
- Do NOT mark emails as read, move, label, or delete anything
- Bullets: 1-2 sentences max
- Write to file directly, do not output to stdout
- Use markdown links [text](url) for all email references
- Section headers use ## (h2), label groups within Technology use ### (h3)
- If a section has no relevant data, write a brief 'Nothing notable today.' line"

ALLOWED_TOOLS="Read,Write,WebFetch"

log "Running Claude CLI..."

CLAUDE_FAILED=0
if [[ "$QUIET" == "--cron" ]]; then
    claude -p "$PROMPT" --allowedTools "$ALLOWED_TOOLS" >> "$LOG" 2>&1 || CLAUDE_FAILED=1
else
    claude -p "$PROMPT" --allowedTools "$ALLOWED_TOOLS" || CLAUDE_FAILED=1
fi

if [[ $CLAUDE_FAILED -eq 1 ]]; then
    log "WARNING: Claude CLI returned error"
fi

# Fallback if digest was not written
if [[ ! -f "$DIGEST_FILE" ]] || [[ ! -s "$DIGEST_FILE" ]]; then
    log "Digest not produced — writing fallback"
    cat > "$DIGEST_FILE" << EOF
# Edward's Daily Digest — ${TODAY}

_Generated at $(date '+%I:%M %p') MT — Claude CLI failed to write digest_

## Career

Nothing fetched — check logs at: ${LOG}

## Notes

EOF
    log "Fallback digest written"
else
    log "Digest generation succeeded: $DIGEST_FILE"

    # Mark tech newsletter emails as read (only after successful digest write)
    if [[ -f "$TECH_IDS_FILE" ]]; then
        log "Marking tech newsletter emails as read..."
        python3 << PYEOF2
import subprocess, json, os

gws = os.path.expanduser("~/.local/bin/gws")

def gws_call(*args):
    result = subprocess.run([gws] + list(args), capture_output=True, text=True)
    return result

try:
    with open("${TECH_IDS_FILE}") as f:
        ids = json.load(f)
    if ids:
        gws_call("gmail", "users", "messages", "batchModify",
                 "--params", '{"userId":"me"}',
                 "--json", json.dumps({
                     "ids": ids,
                     "removeLabelIds": ["UNREAD"]
                 }))
        print(f"Marked {len(ids)} tech emails as read")
except Exception as e:
    print(f"Mark-as-read error (non-fatal): {e}")
PYEOF2
        rm -f "$TECH_IDS_FILE"
    fi

    # Mark __READ label emails as read after extraction
    if [[ -f "$AKITA_IDS_FILE" ]]; then
        log "Marking __READ emails as read..."
        python3 << PYEOF3
import subprocess, json, os

gws = os.path.expanduser("~/.local/bin/gws")

def gws_call(*args):
    result = subprocess.run([gws] + list(args), capture_output=True, text=True)
    return result

try:
    with open("${AKITA_IDS_FILE}") as f:
        ids = json.load(f)
    if ids:
        gws_call("gmail", "users", "messages", "batchModify",
                 "--params", '{"userId":"me"}',
                 "--json", json.dumps({
                     "ids": ids,
                     "removeLabelIds": ["UNREAD"]
                 }))
        print(f"Marked {len(ids)} __READ emails as read")
except Exception as e:
    print(f"__READ mark-as-read error (non-fatal): {e}")
PYEOF3
        rm -f "$AKITA_IDS_FILE"
    fi
fi

# Cleanup digests older than 30 days
CLEANED=0
while IFS= read -r -d '' old_file; do
    rm "$old_file"
    CLEANED=$((CLEANED + 1))
done < <(find "$DIGEST_DIR" -maxdepth 1 -name "????-??-??.md" -mtime +30 -print0 2>/dev/null)
[[ $CLEANED -gt 0 ]] && log "Cleaned $CLEANED digest(s) older than 30 days"

log "Done"
