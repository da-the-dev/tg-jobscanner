# telegram-jobscan

Scans your subscribed Telegram job channels with your own account, filters and
scores postings against your resume with an LLM, and renders a static HTML
dashboard with rankings, pros/cons, your strengths/gaps, and direct links to
each message.

Pipeline: `fetch new msgs → dedupe → keyword prefilter (free) → LLM score → SQLite → dashboard.html`

Nothing is re-fetched or re-scored twice: each channel remembers its last seen
message ID, and verdicts are cached by message text hash (cross-posted
duplicates are scored once).

## Two fetch modes

- **`fetch_mode: web`** (default) — scrapes the public `t.me/s/<channel>` web
  preview. **No Telegram account, credentials, or login needed.** Works for
  public channels only (the vast majority of job boards are public). Telethon
  isn't even required in this mode.
- **`fetch_mode: mtproto`** — full client login as your account; needed only
  for private channels. Requires api_id/api_hash from https://my.telegram.org.

### If my.telegram.org shows ERROR when creating an app

Known long-standing issue. In rough order of success rate: disable VPN/proxy
(datacenter IPs are blocked), try from your phone on mobile data, use a clean
incognito window, fill the form minimally (short ASCII title, lowercase
alphanumeric short name, URL empty, platform Desktop), retry in a few days.
VoIP-registered and very new accounts are often barred entirely. Until it
works, just use `fetch_mode: web`.

## Setup (one-time, ~5 min in web mode)

1. **Install (uv):**
   ```bash
   cd telegram-jobscan
   uv sync                       # installs deps + the `jobscan` command
   cp config.example.yaml config.yaml
   ```
   (Plain pip works too: `pip install -e .`)
2. **Fill `config.yaml`** — channel list (public @usernames or t.me links).
3. **Resume:** paste your resume into `resume.md`.
4. **LLM:** pick one of the two cases below.
5. *(mtproto mode only)* set api_id/api_hash, then `uv run jobscan login`
   once; `uv run jobscan channels` lists your subscriptions.

The project uses a `src/` layout and exposes a console script, so always
invoke it as `uv run jobscan …` (or just `jobscan …` inside an activated
venv) — not by running a file directly.

## Choosing your LLM

Set `llm.backend` in `config.yaml` to one of:

### Case A — Claude via your subscription (`backend: claude-code`)
Uses the `claude` CLI, no per-token cost. One gotcha: if you have an
`ANTHROPIC_API_KEY` in your shell, the CLI tries to use it and returns
**401 Invalid authentication credentials** on a subscription plan. The tool
now strips that variable automatically (`claude_code.use_subscription: true`),
so scoring works even with a stray key in your env.

Sanity-check the CLI itself is logged in:
```bash
printf 'Reply with just: ok' | claude -p --output-format json
```
Want `"is_error": false`. If it says *Not logged in*, run `claude` then
`/login`. Tune cost with `claude_code.model: "haiku"`.

### Case B — Local model via Ollama (`backend: ollama`)
Zero cost, fully offline, nothing leaves your Mac.
```bash
brew install ollama          # or from ollama.com
ollama serve                 # runs the local server
ollama pull qwen2.5:14b      # match llm.ollama.model in config
```
Then set `llm.backend: ollama`. Quality is lower than Claude and each run is
slower, but there's no auth and no metering. Smaller models (e.g.
`llama3.1:8b`) are faster; larger ones score better.

Switching between the two is just the one `llm.backend` line — the cache,
prefilter, and dashboard are identical either way.

## Usage

```bash
uv run jobscan run             # fetch + score + render dashboard.html
uv run jobscan serve --open    # live dashboard; status changes save to the DB
uv run jobscan render --open   # re-render the static file and open it
uv run jobscan stats           # message counts by status
uv run jobscan run --retry-errors  # also retry previously failed batches
```

The dashboard: sort by any column, filter by score / channel / text, click a
row for the why-apply / why-skip / strengths / gaps analysis, click **Open in
TG** to jump to the original message.

## Tracking what you applied to

Every posting carries a status — `new → applied → interviewing → offer`, plus
`rejected` and `skipped` — with a free-text note and a timestamped history of
every change. All of it lives in SQLite (`applications` table), so it survives
re-renders, re-runs, and moving the DB between machines.

```bash
uv run jobscan serve --open                 # edit statuses/notes in the browser
uv run jobscan mark 95702c applied --note "referred by Ann"
uv run jobscan mark 95702c rejected --note "wanted more MLOps"
uv run jobscan applications                 # everything you're tracking
uv run jobscan applications --status applied --history
```

`mark` takes any unique prefix of the posting hash shown by `applications` (and
by the dashboard's per-row detail). Status filter pills at the top of the page
show counts; **Open** hides rejected/skipped, and anything you're tracking stays
visible even if it falls below the min-score filter.

**Two ways to edit, one source of truth.** Under `jobscan serve` (default
`127.0.0.1:8765`, loopback-only, no auth) each change POSTs straight to the DB.
If you instead open `dashboard.html` as a plain file, changes queue in the
browser and a banner offers **Sync now** — start `jobscan serve` and they flush
into the DB (the page also retries automatically on load). Statuses set with the
old localStorage-only dashboard are imported into that queue the first time you
open the new one.

The server is FastAPI + uvicorn: `POST /api/status` (one change or a batch),
`GET /api/applications[?status=…]`, `GET /api/health`, and interactive docs at
`/api/docs`. An unknown status is rejected per item rather than failing the
whole batch, so a bad entry can't wedge the browser's retry queue.

## Layout

```
src/jobscan/
  models.py              SQLAlchemy models: channels, messages, verdicts,
                         applications, application_events
  db.py                  DB facade — one short-lived session per call, so the
                         same object is safe for the CLI and the API server
  fetcher.py             mtproto fetch (telethon)
  fetcher_web.py         t.me/s/<channel> fetch, no credentials
  prefilter.py           free keyword/job-signal filter
  scorer.py              batched, parallel LLM scoring with a verdict cache
  llm.py                 claude-code / ollama backends
  server.py              FastAPI app + `jobscan serve`
  dashboard.py           fills the template from the DB
  templates/dashboard.html   the page itself (edit this for UI changes)
```

## Daily schedule (optional)

```bash
# edit WorkingDirectory in launchd/com.jobscan.daily.plist first
cp launchd/com.jobscan.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jobscan.daily.plist
```

Runs every day at 09:00 (Mac must be awake). Logs: `/tmp/jobscan.log`.
Unload with `launchctl unload ~/Library/LaunchAgents/com.jobscan.daily.plist`.

## Cost control

- Prefilter (`prefilter.my_keywords`) drops non-matching posts for free —
  tune it to your stack; empty list = only generic job-post detection.
- `llm.batch_size: 5` postings per LLM call; raise to save calls.
- `llm.concurrency: 4` batches are scored in parallel with a live progress
  bar. Raise it (4–6) to finish a big backlog faster on `claude-code`; set it
  to `1` for Ollama on a single local GPU. Cost is unchanged — same number of
  calls, just overlapped.
- Verdict cache means a rerun after failure only pays for what's new.
- `llm.claude_code.model: "haiku"` for the cheapest scoring.

## Security notes

- `jobscan.session` grants full access to your Telegram account — treat it
  like a password, don't commit or copy it anywhere.
- The tool is read-only: it never posts, joins, or messages anyone.
- Polling your own subscribed channels once a day is normal client behavior;
  avoid pointing it at hundreds of channels or minute-level schedules.
