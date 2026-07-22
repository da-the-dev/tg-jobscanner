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
uv run jobscan render --open   # re-render from cache and open in browser
uv run jobscan stats           # message counts by status
uv run jobscan run --retry-errors  # also retry previously failed batches
```

Open `dashboard.html` in a browser: sort by any column, filter by score /
channel / text, click a row for the why-apply / why-skip / strengths / gaps
analysis, click **Open in TG** to jump to the original message. The
new/applied/skipped status you set per job survives re-renders (stored in
browser localStorage).

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
- Verdict cache means a rerun after failure only pays for what's new.
- `llm.claude_code.model: "haiku"` for the cheapest scoring.

## Security notes

- `jobscan.session` grants full access to your Telegram account — treat it
  like a password, don't commit or copy it anywhere.
- The tool is read-only: it never posts, joins, or messages anyone.
- Polling your own subscribed channels once a day is normal client behavior;
  avoid pointing it at hundreds of channels or minute-level schedules.
