# Daily Digest

A config-driven web dashboard for viewing daily markdown digests. Point it at a directory of `YYYY-MM-DD.md` files and get a clean, dark-themed dashboard with weather, section cards, archive calendar, and keyboard navigation.

## Setup

Requires Python 3.10+ and [uv](https://github.com/astral-sh/uv).

```bash
cp .env.example .env
# Edit .env — at minimum, set DIGEST_DIR
```

### Run

```bash
# Load env and start server
set -a && source .env && set +a
uv run server.py
```

Or run directly with env vars:

```bash
DIGEST_DIR=/path/to/digests uv run server.py
```

## Configuration

All configuration is via environment variables. See `.env.example` for the full list.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DIGEST_DIR` | Yes | — | Path to directory containing digest markdown files |
| `DIGEST_PORT` | No | `10001` | Server port |
| `DIGEST_HOST` | No | `127.0.0.1` | Server bind address |
| `DIGEST_USER_NAME` | No | *(empty)* | Name shown in the hero greeting |
| `DIGEST_APP_NAME` | No | `Daily Digest` | Brand name in nav bar and titles |
| `DIGEST_WEATHER_LAT` | No | *(disabled)* | Latitude for weather forecast |
| `DIGEST_WEATHER_LON` | No | *(disabled)* | Longitude for weather forecast |
| `DIGEST_NAV_LINKS` | No | `[]` | JSON array of extra nav links: `[{"label":"X","url":"http://..."}]` |

## Digest File Format

Each digest is a markdown file named `YYYY-MM-DD.md` (e.g., `2026-03-13.md`).

The server parses H2 headings (`## Section Name`) as section boundaries. Recognized section types get icons and accent colors:

- **Weather** — hourly forecast display
- **Career** — blue accent
- **Boxing** — red accent
- **Technology** — green accent
- **News** — amber accent
- **Miscellaneous** — purple accent
- **Calendar** — orange accent

Unrecognized sections render with a generic style. You can use any H2 headings you like.

### Example digest structure

```markdown
## Weather
☀️ Clear, High 72°F / Low 45°F

## Technology
- **ByteByteGo**: New article on distributed consensus...
- **Pragmatic Engineer**: Engineering leadership trends...

## News
- AI regulation bill advances in Senate
- Tech earnings exceed expectations

## Calendar
- 2026-03-14 - 10:00 AM: Team standup
- 2026-03-15 - 2:00 PM: Design review
```

## Generating Digests

Digests are just markdown files — create them however you like. A template generator script is provided in `generate-digest.sh.example` that uses Claude CLI with Google Workspace CLI tools.

## License

MIT
