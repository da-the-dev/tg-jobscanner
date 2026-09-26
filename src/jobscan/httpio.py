"""One shared HTTP GET for the two places that fetch pages over plain HTTPS:
the public-channel web-preview fetcher (fetcher_web) and apply-link
enrichment (links). Just urllib with a browser User-Agent — no dependencies.

Errors are the caller's business: fetcher_web lets them abort a channel,
links.fetch_text swallows them (best-effort enrichment).
"""
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def get(url, timeout=30):
    """GET `url` and return the body decoded as UTF-8 (lossy on mojibake).

    Raises on HTTP/network failure — callers decide how fatal that is.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")