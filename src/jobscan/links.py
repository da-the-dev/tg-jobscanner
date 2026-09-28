"""Classify and follow links found inside job postings.

Job channels are two things at once: the posting itself, and a footer of
"more vacancies in other channels" links. We only want to read the former.

A link to a bare `t.me/<username>` (channel root, no message id) is almost
always a crosspost/footer link — UNLESS its anchor text ("тут",
"откликнуться", "написать рекрутеру"…) signals it's actually how you apply,
in which case the channel root IS the apply mechanism (a recruiter's own
Telegram profile). Everything else (an external domain, or `t.me/<user>/<id>`
pointing at one specific post) is worth fetching for extra JD context.
"""
import html as htmllib
import re
import urllib.parse

from . import httpio

TG_ROOT_RE = re.compile(r"^https?://t\.me/(?!s/|c/|iv\b)([A-Za-z0-9_]+)/?(?:\?.*)?$")
TG_POST_RE = re.compile(r"^https?://t\.me/(?!s/|c/)([A-Za-z0-9_]+)/(\d+)/?$")
TG_BOT_RE = re.compile(r"_bot\b", re.IGNORECASE)

APPLY_ANCHOR_RE = re.compile(
    r"отклик|подробнее|узнать|apply|read\s+more|learn\s+more|see\s+more"
    r"|\bтут\b|\bздесь\b|\bhere\b|вакансия|position|details|job\b",
    re.IGNORECASE)
RECRUITER_ANCHOR_RE = re.compile(
    r"рекрутер|личку|личные\s+сообщения|написать|dm\s+me|reach\s+out"
    r"|contact\s+me|\bhr\b|@\w+",
    re.IGNORECASE)

# raw <a href="...">inner html</a> inside an un-stripped message div (web preview mode)
_A_TAG_RE = re.compile(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', re.S | re.IGNORECASE)
_INNER_TAG_RE = re.compile(r"<[^>]+>")


def _clean_anchor(inner_html):
    return htmllib.unescape(_INNER_TAG_RE.sub("", inner_html)).strip()


def _unwrap_iv(url):
    """Telegram's Instant View wraps external links as t.me/iv?url=<encoded>."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc == "t.me" and parsed.path in ("/iv", "/iv/"):
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("url"):
            return urllib.parse.unquote(qs["url"][0])
    return url


def extract_from_html(div_html):
    """(anchor, url) pairs from a raw (un-stripped) message div — web preview mode."""
    out = []
    for m in _A_TAG_RE.finditer(div_html):
        url = _unwrap_iv(htmllib.unescape(m.group(1)))
        out.append((_clean_anchor(m.group(2)), url))
    return out


def extract_from_entities(text, entities):
    """(anchor, url) pairs from Telethon message entities — mtproto mode.

    Offsets are UTF-16 code units (the Telegram wire format), so we slice via
    a UTF-16 round-trip rather than plain Python string indexing — otherwise
    anything past an emoji/astral character in the text would misalign.
    """
    if not entities:
        return []
    u16 = text.encode("utf-16-le")

    def slice_(offset, length):
        return u16[offset * 2:(offset + length) * 2].decode("utf-16-le", "ignore")

    out = []
    for e in entities:
        cls = type(e).__name__
        if cls == "MessageEntityTextUrl" and getattr(e, "url", None):
            out.append((slice_(e.offset, e.length), e.url))
        elif cls == "MessageEntityUrl":
            bare = slice_(e.offset, e.length)
            out.append((bare, bare))
    return out


def classify(anchor, url):
    """Return one of: apply, recruiter_contact, crosspost, bot, contact, other."""
    if url.startswith("mailto:"):
        return "contact"
    m_root = TG_ROOT_RE.match(url)
    if m_root:
        username = m_root.group(1)
        if TG_BOT_RE.search(username) or "?start=" in url:
            return "bot"
        if APPLY_ANCHOR_RE.search(anchor) or RECRUITER_ANCHOR_RE.search(anchor):
            return "recruiter_contact"
        return "crosspost"
    if TG_POST_RE.match(url):
        return "apply"
    if url.startswith("http://") or url.startswith("https://"):
        return "apply"
    return "other"


def analyze(links):
    """Classify every (anchor, url) pair from a message.

    Returns (primary_url, primary_kind, has_recruiter_contact):
      primary_url/kind — the best candidate worth fetching for JD text
                         ("apply" links win over a lone "recruiter_contact").
      has_recruiter_contact — True if ANY link in the message is a direct
                         personal contact (used for the score bonus/badge
                         even when a separate apply link is also fetched).
    """
    apply_link = None
    contact_link = None
    has_contact = False
    for anchor, url in links:
        kind = classify(anchor, url)
        if kind == "apply" and apply_link is None:
            apply_link = url
        elif kind == "recruiter_contact":
            has_contact = True
            if contact_link is None:
                contact_link = url
    if apply_link:
        return apply_link, "apply", has_contact
    if contact_link:
        return contact_link, "recruiter_contact", has_contact
    return None, None, has_contact


def crossposts_in(links):
    """(username, anchor_text) pairs for every "crosspost" link in a message —
    the candidates for channel discovery (see db.record_discovered_channel).
    Deduped within the message: a footer link repeated twice counts once."""
    out = {}
    for anchor, url in links:
        if classify(anchor, url) != "crosspost":
            continue
        m = TG_ROOT_RE.match(url)
        if m:
            out[m.group(1).lower()] = anchor
    return list(out.items())


def _fetch_url(url):
    """https://t.me/<user>/<id> has no inline text over plain HTTP — the /s/
    web-preview path does. Rewrite before fetching; every other URL as-is."""
    m = TG_POST_RE.match(url)
    if m:
        return f"https://t.me/s/{m.group(1)}/{m.group(2)}"
    return url


def fetch_text(url, timeout=10, max_chars=4000):
    """Best-effort page text for JD enrichment. None on any failure — a
    blocked/JS-only/paywalled page just means we score off the TG text alone."""
    try:
        raw = httpio.get(_fetch_url(url), timeout=timeout)
    except Exception:
        return None
    body = re.sub(r"(?is)<(script|style|nav|footer|header)\b.*?</\1>", " ", raw)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) < 50:
        return None
    return text[:max_chars]
