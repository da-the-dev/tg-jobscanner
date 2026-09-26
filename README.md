# telegram-jobscan

Scans your subscribed Telegram job channels, filters and scores postings against
your resume with an LLM, and renders an HTML dashboard with rankings, pros/cons,
your strengths/gaps, and direct links to each message.

Pipeline: `fetch new msgs → dedupe → prefilter (free) → follow apply links for
thin postings → LLM score → auto-flag dealbreakers → SQLite → dashboard`

The prefilter is free and does three things: drops messages that don't read as a
job posting, drops candidates advertising *themselves* (`#резюме` / "open to
work" / "ищу работу" posts — these channels are two-sided), and drops postings
that don't mention any of your keywords.

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
2. **Fill `config.yaml`** — channel list (public @usernames or t.me links) and
   your prefilter keywords. Any `${ENV_VAR}` in the file is expanded from the
   environment at load time (and the run aborts if it's unset), so secrets can
   stay out of the file.
3. **Resume:** paste your actual resume into `resume.md`. The run refuses to
   start while it's still the shipped placeholder — the LLM won't score against
   a template.
4. **LLM:** the default is OpenRouter — set `OPENROUTER_API_KEY` in your
   environment and you're done. See "Choosing your LLM" for the alternatives.
5. *(mtproto mode only)* set api_id/api_hash, then `uv run jobscan login`
   once; `uv run jobscan channels` lists your subscriptions.

The project uses a `src/` layout and exposes a console script, so always
invoke it as `uv run jobscan …` (or just `jobscan …` inside an activated
venv) — not by running a file directly.

## Choosing your LLM

Set `llm.backend` in `config.yaml` to `openai` or `claude-code`. Switching is
just that one line — the cache, prefilter, and dashboard are identical either
way.

### 1. Any OpenAI-compatible endpoint (`backend: openai`) — recommended

This one backend talks to anything that speaks the OpenAI `/responses` API:
a hosted pay-per-token service, or a model you run yourself.

**OpenRouter** is the go-to and the default in `config.example.yaml` — pay-per-
token access to every hosted model worth using (DeepSeek, Llama, Qwen, GPT,
Gemini, Claude, …), nothing to install, no subscription, and a cheap model
scores a full backlog for cents.

Set `OPENROUTER_API_KEY` in the environment and pick a model:
```yaml
llm:
  backend: "openai"
  openai:
    host: "https://openrouter.ai/api/v1"   # the default, can be omitted
    model: "deepseek/deepseek-chat"        # anything from openrouter.ai/models
```

**Self-hosted** (Ollama, vLLM, LM Studio, …) works the same way — just point
`host` at its OpenAI-compatible endpoint. Most don't check the API key at all,
so `api_key_env` can be left unset:
```yaml
llm:
  backend: "openai"
  openai:
    host: "http://localhost:11434/v1"   # Ollama's OpenAI-compatible endpoint
    model: "qwen2.5:14b"                # must be pulled first: `ollama pull qwen2.5:14b`
```
Zero cost, fully offline, nothing leaves your machine — the tradeoff is lower
scoring quality and a slower run than a hosted model.

### 2. Claude via your subscription (`backend: claude-code`)
Uses the `claude` CLI, no per-token cost — worth it if you already pay for a
Claude plan. One gotcha: if you have an `ANTHROPIC_API_KEY` in your shell, the
CLI tries to use it and returns **401 Invalid authentication credentials** on a
subscription plan. The tool strips that variable automatically
(`claude_code.use_subscription: true`), so scoring works even with a stray key
in your env.

Sanity-check the CLI itself is logged in:
```bash
printf 'Reply with just: ok' | claude -p --output-format json
```
Want `"is_error": false`. If it says *Not logged in*, run `claude` then
`/login`. Tune cost with `claude_code.model: "haiku"`.

## Following apply links

A lot of postings don't carry the actual JD inline — they're a title, a few
tags, and "Узнать подробнее и откликнуться: тут", with the real requirements
living on the other end of that link. The same message is usually also
padded with a footer of "more vacancies in other channels" links, which are
noise.

`links.py` extracts every link in a message (from Telethon entities in
mtproto mode, from raw `<a>` tags in web-preview mode) and classifies each
one: a bare `t.me/<channel>` link with a generic anchor is a crosspost and is
ignored; an external domain, a specific `t.me/<channel>/<id>` post, or a
`t.me/<channel>` link whose anchor reads like "apply here" / "message the
recruiter" is worth following. For postings that are still short after the
inline text (`links.skip_fetch_min_chars`), the apply link's page text is
fetched and folded into what the LLM sees — cached by URL so a link shared
across cross-posted duplicates, or seen again on a rerun, is only ever
fetched once.

A message whose apply link is a personal Telegram contact rather than a
form/ATS gets a `📞 direct contact` badge and a small score bump
(`links.recruiter_bonus`) — those consistently convert better.

## The rubric

The LLM scores 0-100 using explicit bands (a wrong-domain or wrong-seniority
posting should score in the 0-20s, not hover around 50 — most rubric-less
prompts compress scores into a comfortable middle range, which is the bug
this fixes) and extracts a couple of facts about the posting alongside its
judgement call: the seniority level it's hiring for, and whether it explicitly
requires being based in Russia.

Those two facts are then applied **deterministically in code**, not left to
the model's own judgement: a posting matching `prefilter.reject_seniority` or
`prefilter.reject_russia_only` gets a visible ⚠ flag and is dimmed on the
dashboard. It's never hidden — a misfiring rule should stay easy to spot and
override — but you no longer have to eyeball every "Middle" or
Russia-residency-required posting yourself.

## Usage

```bash
uv run jobscan run             # fetch + score + render the dashboard
uv run jobscan serve --open    # live dashboard; status changes save to the DB
uv run jobscan render --open   # re-render the static file and open it
uv run jobscan stats           # message counts by status
```

`run` flags (combine freely):

| flag | effect |
|---|---|
| `--retry-errors` | re-queue batches that previously failed scoring |
| `--refilter` | re-run the prefilter over the whole cache first — drops CVs / non-jobs that an older filter let through (and their cached verdicts), re-queues anything that now passes |
| `--reset-scores` | discard every cached verdict and re-score from scratch |
| `--prune-days N` | delete cached postings older than N days before scoring (postings you're tracking are kept) |
| `--no-render` | skip writing the dashboard file |

The dashboard: sort by any column, filter by score / channel / text, click a
row for the why-apply / why-skip / strengths / gaps analysis, click **Open in
TG** to jump to the original message.

## Tracking what you applied to

Every posting carries a status — `new → maybe → applied → interviewing →
offer`, plus `rejected` and `skipped` — with a free-text note and a
timestamped history of every change. `maybe` is for the ones you're not sure
about yet; it stays in the "open" view instead of getting buried with the
rejects. All of it lives in SQLite (`applications` table), so it survives
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
If you instead open the dashboard file directly, changes queue in the browser
and a banner offers **Sync now** — start `jobscan serve` and they flush into the
DB (the page also retries automatically on load). Statuses set with the old
localStorage-only dashboard are imported into that queue the first time you open
the new one.

The server is FastAPI + uvicorn: `POST /api/status` (one change or a batch),
`GET /api/applications[?status=…]`, `GET /api/health`, and interactive docs at
`/api/docs`. An unknown status is rejected per item rather than failing the
whole batch, so a bad entry can't wedge the browser's retry queue.

## Layout

```
src/jobscan/
  config.py              load + validate config.yaml, ${ENV} expansion,
                         resume-template guard
  models.py              SQLAlchemy models: channels, messages, verdicts,
                         applications, application_events, link_fetches
  db.py                  DB facade — one short-lived session per call, so the
                         same object is safe for the CLI and the API server;
                         brings the schema up to date via Alembic on open
  migrations/            Alembic environment + revisions (see below)
  fetcher.py             mtproto fetch (telethon)
  fetcher_web.py         t.me/s/<channel> fetch, no credentials
  links.py               extracts/classifies links in a posting, fetches +
                         caches the apply link's page text
  prefilter.py           free filter: job-signal + CV/job-wanted + keyword
  scorer.py              batched, parallel LLM scoring; verdict cache;
                         deterministic rubric flags; retry-errors / refilter
  llm.py                 openai (OpenRouter or any compatible endpoint) /
                         claude-code backends
  progress.py            terminal progress bar
  server.py              FastAPI app + `jobscan serve`
  dashboard.py           fills the template from the DB
  templates/dashboard.html   the page itself (edit this for UI changes)
```

Schema changes go through Alembic (`src/jobscan/migrations/`) rather than
hand-written `ALTER TABLE`s. `DB.__init__` runs `alembic upgrade head`
automatically — nothing to run by hand for normal use. If you're changing
`models.py` yourself: point `alembic.ini`'s `sqlalchemy.url` at a scratch
database (never your real one) and run `alembic revision --autogenerate -m "…"`
from the project root, then review the generated file in
`src/jobscan/migrations/versions/` before committing it.

## Running it regularly

However you schedule it, the shape is the same: a cron entry (or any
scheduler) running `uv run jobscan run -c /path/to/config.yaml` on a machine
that stays awake, pointed at a persistent `db_path`/`resume.md`/session file.
A more portable, batteries-included deployment setup is in the works.

## Cost control

- Prefilter (`prefilter.my_keywords`) drops non-matching posts for free.
  Keywords are matched on **word boundaries** (so `ai` won't fire on "email"
  and `ml` won't fire on "html") — list your real stack / role terms, RU+EN;
  empty list = only generic job-post + CV detection.
- `links.max_fetches_per_run` caps how many apply-link pages get fetched per
  run; `links.skip_fetch_min_chars` skips the fetch entirely when the inline
  message is already a full JD.
- `llm.batch_size: 5` postings per LLM call; raise to save calls.
- `llm.concurrency: 4` batches are scored in parallel with a live progress
  bar. Raise it (4–6, more on a hosted endpoint) to finish a big backlog
  faster; drop to `1` for a single local GPU. Cost is unchanged — same number
  of calls, just overlapped.
- Verdict cache means a rerun after failure only pays for what's new;
  `--prune-days` keeps the cache (and each run's fetch) from growing forever.
- Pick a cheap OpenRouter model (or `llm.claude_code.model: "haiku"`) for the
  lowest per-run cost.

## Security notes

- `jobscan.session` grants full access to your Telegram account — treat it
  like a password, don't commit or copy it anywhere. `config.yaml`, `*.db`,
  `*.session` and `resume.md` are gitignored.
- The tool is read-only against Telegram: it never posts, joins, or messages
  anyone. It does make outbound HTTP GET requests to the apply links found in
  thin postings (see "Following apply links") — plain fetches, nothing sent
  beyond the request itself; set `links.enabled: false` to turn that off.
- Polling your own subscribed channels once a day is normal client behavior;
  avoid pointing it at hundreds of channels or minute-level schedules.
- `jobscan serve` binds loopback only and has no auth — don't expose it.
