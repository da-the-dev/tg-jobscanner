"""Credential-free fetch for PUBLIC channels via t.me/s/<channel> web preview.

No Telegram account, api_id or login needed. Limitations: public channels
only; long posts may be truncated in the preview (rare for job posts)."""
import datetime
import hashlib
import html as htmllib
import re
import time
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

POST_RE = re.compile(r'data-post="[^"/]+/(\d+)"')
TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
TIME_RE = re.compile(r'<time datetime="([^"]+)"')


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _strip_html(s):
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return htmllib.unescape(s).strip()


def channel_id(username):
    """Stable synthetic integer id for web-mode channels."""
    return int(hashlib.sha1(username.lower().encode()).hexdigest()[:8], 16)


def parse_page(page_html):
    """Return list of (msg_id, iso_date, text), oldest→newest as served."""
    out = []
    # split page into per-message chunks anchored at data-post
    parts = re.split(r'(?=data-post=")', page_html)
    for part in parts:
        m = POST_RE.search(part[:200])
        if not m:
            continue
        msg_id = int(m.group(1))
        tm = TIME_RE.search(part)
        tx = TEXT_RE.search(part)
        if not tx:
            continue  # media-only post
        out.append((msg_id, tm.group(1) if tm else "", _strip_html(tx.group(1))))
    # a message can appear twice (data-post on wrapper + footer link); dedupe
    seen, uniq = set(), []
    for item in out:
        if item[0] not in seen:
            seen.add(item[0])
            uniq.append(item)
    return uniq


def fetch_channel(username, last_id, cutoff, max_msgs, log=print, delay=1.0):
    """Collect messages with id > last_id, respecting cutoff/max on first run."""
    collected = {}
    before = None
    for _ in range(40):  # hard page cap
        url = f"https://t.me/s/{username}" + (f"?before={before}" if before else "")
        page = _get(url)
        if "tgme_widget_message" not in page:
            if before is None:
                log(f"  !! '{username}' is not a public channel (no web preview)")
                return []
            break  # ran past the oldest page; keep what we have
        batch = parse_page(page)
        if not batch:
            break
        hit_boundary = False
        for msg_id, date, text in batch:
            if msg_id <= last_id:
                hit_boundary = True
                continue
            if last_id == 0 and date:
                try:
                    d = datetime.datetime.fromisoformat(date)
                    if d < cutoff:
                        hit_boundary = True
                        continue
                except ValueError:
                    pass
            collected[msg_id] = (date, text)
        oldest = min(m for m, _, _ in batch)
        if hit_boundary or len(collected) >= max_msgs or before == oldest:
            break
        before = oldest
        time.sleep(delay)
    # newest max_msgs only
    ids = sorted(collected)[-max_msgs:] if last_id == 0 else sorted(collected)
    return [(i, *collected[i]) for i in ids]


def fetch_new(cfg, db, log=print):
    days = int(cfg["backfill"]["days"])
    max_msgs = int(cfg["backfill"]["max_messages"])
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    added = 0
    for ref in cfg["channels"]:
        username = str(ref).replace("https://t.me/s/", "").replace(
            "https://t.me/", "").strip("/@ ")
        cid = channel_id(username)
        db.upsert_channel(cid, username, username)
        last = db.last_msg_id(cid)
        msgs = fetch_channel(username, last, cutoff, max_msgs, log=log)
        n = 0
        for msg_id, date, text in msgs:
            if text.strip():
                inserted, _ = db.add_message(
                    cid, msg_id, date, text, f"https://t.me/{username}/{msg_id}")
                n += inserted
            db.set_last_msg_id(cid, msg_id)
        added += n
        log(f"  {username}: +{n} new")
    return added
