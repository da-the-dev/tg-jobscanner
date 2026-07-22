"""CLI: python -m jobscan <command>"""
import argparse
import webbrowser

from . import config as config_mod
from . import dashboard, scorer
from .db import DB
from .llm import get_backend


def main():
    p = argparse.ArgumentParser(prog="jobscan",
                                description="Scan Telegram job channels against your resume.")
    p.add_argument("-c", "--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="one-time Telegram login (creates session file)")
    sub.add_parser("channels", help="list channels you're subscribed to")
    runp = sub.add_parser("run", help="fetch new messages, score, render dashboard")
    runp.add_argument("--no-render", action="store_true")
    runp.add_argument("--retry-errors", action="store_true",
                      help="also retry messages that previously failed scoring")
    rend = sub.add_parser("render", help="re-render dashboard from cache only")
    rend.add_argument("--open", action="store_true", help="open in browser")
    sub.add_parser("stats", help="show cache statistics")
    args = p.parse_args()

    cfg = config_mod.load(args.config)
    if cfg["fetch_mode"] == "mtproto":
        from . import fetcher  # requires telethon + api credentials
    else:
        from . import fetcher_web as fetcher  # public channels, no credentials

    if args.cmd in ("login", "channels"):
        if cfg["fetch_mode"] != "mtproto":
            raise SystemExit(f"'{args.cmd}' only applies to fetch_mode: mtproto "
                             "(web mode needs no login)")
        if args.cmd == "login":
            with fetcher.make_client(cfg) as client:
                me = client.get_me()
                print(f"Logged in as {me.first_name} (@{me.username}). Session saved.")
        else:
            fetcher.list_dialogs(cfg)
        return

    db = DB(cfg["db_path"])

    if args.cmd == "stats":
        print(db.stats())
        return

    if args.cmd == "render":
        out = dashboard.render(cfg, db)
        if args.open:
            webbrowser.open("file://" + __import__("os").path.abspath(out))
        return

    # run
    resume = config_mod.load_resume(cfg)
    if args.retry_errors:
        scorer.retry_errors(db)
    print("Fetching new messages…")
    added = fetcher.fetch_new(cfg, db)
    print(f"Fetched {added} new messages. Scoring…")
    scored = scorer.score_pending(cfg, db, get_backend(cfg), resume)
    print(f"Scored {scored} postings.")
    if not args.no_render:
        dashboard.render(cfg, db)
    print("Stats:", db.stats())


if __name__ == "__main__":
    main()
