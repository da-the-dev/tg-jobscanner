"""Render the HTML dashboard from the database.

The page itself lives in templates/dashboard.html; this module only fills in its
__PLACEHOLDERS__. Application status comes from the DB, not from the browser, so
it survives re-renders and machine changes. The page edits it two ways:
  * served by `jobscan serve` → every change POSTs straight to the DB;
  * opened as a plain file  → changes queue in localStorage and are flushed to
    a running `jobscan serve` (Sync button / automatic retry on load).
"""
import datetime
import json
from functools import cache
from importlib.resources import files

from .models import STATUSES

TEMPLATE_NAME = "templates/dashboard.html"


@cache
def template():
    return files(__package__).joinpath(TEMPLATE_NAME).read_text(encoding="utf-8")


def build(cfg, db, live=False, api_base=""):
    """Return the dashboard HTML for the current DB contents."""
    events = {}
    for e in db.application_events():
        events.setdefault(e["text_hash"], []).append(
            {"status": e["status"], "note": e["note"] or "", "at": e["at"]})
    rows = []
    for r in db.dashboard_rows():
        rows.append({
            "hash": r["text_hash"], "score": r["score"], "title": r["title"],
            "company": r["company"], "salary": r["salary"], "location": r["location"],
            "channel": r["channel_title"] or (r["username"] or "?"),
            "date": r["date"] or "", "link": r["link"],
            "status": r["app_status"], "note": r["app_note"],
            "applied_at": r["applied_at"] or "", "status_updated": r["status_updated"] or "",
            "history": events.get(r["text_hash"], []),
            "reasons_apply": json.loads(r["reasons_apply"] or "[]"),
            "reasons_skip": json.loads(r["reasons_skip"] or "[]"),
            "strengths": json.loads(r["strengths"] or "[]"),
            "weaknesses": json.loads(r["weaknesses"] or "[]"),
        })
    html = (template()
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__STATUSES__", json.dumps(list(STATUSES)))
            .replace("__LIVE__", "true" if live else "false")
            .replace("__API__", json.dumps(api_base))
            .replace("__GENERATED__", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__COUNT__", str(len(rows)))
            .replace("__MIN_SCORE__", str(cfg["scoring"]["min_score"])))
    return html, len(rows)


def serve_url(cfg):
    """Where a statically-opened dashboard should try to flush its changes."""
    sc = cfg.get("serve") or {}
    host = sc.get("host", "127.0.0.1")
    return f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{sc.get('port', 8765)}"


def render(cfg, db, log=print):
    html, n = build(cfg, db, live=False, api_base=serve_url(cfg))
    out = cfg["dashboard_path"]
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"  dashboard: {out} ({n} postings)")
    return out
