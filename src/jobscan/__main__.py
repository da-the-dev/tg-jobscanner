"""CLI: python -m jobscan <command>"""
import argparse
import webbrowser

from . import config as config_mod
from . import dashboard, scorer
from .db import DB, STATUSES
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
    runp.add_argument("--reset-scores", action="store_true",
                      help="discard every cached score and re-score from scratch")
    runp.add_argument("--prune-days", type=int, metavar="N",
                      help="delete cached postings older than N days "
                           "(keeps ones you're tracking) before scoring")
    runp.add_argument("--refilter", action="store_true",
                      help="re-run the prefilter over the whole cache first "
                           "(drops CVs/non-jobs that slipped through earlier)")
    rend = sub.add_parser("render", help="re-render dashboard from cache only")
    rend.add_argument("--open", action="store_true", help="open in browser")
    sub.add_parser("stats", help="show cache statistics")

    srv = sub.add_parser("serve", help="serve the dashboard so status changes save to the DB")
    srv.add_argument("--host")
    srv.add_argument("--port", type=int)
    srv.add_argument("--open", action="store_true", help="open in browser")

    mark = sub.add_parser("mark", help="set the application status of a posting")
    mark.add_argument("hash", help="posting hash or unique prefix (shown by `applications`)")
    mark.add_argument("status", choices=STATUSES)
    mark.add_argument("--note", help="free-text note (recruiter, rejection reason…)")

    apps = sub.add_parser("applications", help="list postings you're tracking")
    apps.add_argument("--status", choices=STATUSES, help="only this status")
    apps.add_argument("--history", action="store_true", help="show the status timeline")

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

    if args.cmd == "serve":
        from . import server
        server.serve(cfg, db, host=args.host, port=args.port, open_browser=args.open)
        return

    if args.cmd == "mark":
        try:
            h = db.resolve_hash(args.hash)
        except KeyError as e:
            raise SystemExit(e.args[0]) from None
        row = db.set_application(h, args.status, args.note)
        v = db.verdict(h)
        print(f"{h}  {row['status']:<12} {v.title or '(untitled)'}"
              f"{' @ ' + v.company if v.company else ''}")
        if row["note"]:
            print(f"  note: {row['note']}")
        return

    if args.cmd == "applications":
        rows = db.applications(args.status)
        if not rows:
            print("Nothing tracked yet — mark postings in the dashboard or with `jobscan mark`.")
            return
        for r in rows:
            print(f"{r['text_hash']}  {r['status']:<12} {(r['score'] or 0):>3}  "
                  f"{(r['title'] or '(untitled)')[:48]:<48}  {(r['company'] or '')[:22]:<22}  "
                  f"{(r['updated_at'] or '')[:10]}")
            if r["note"]:
                print(f"    note: {r['note']}")
            if r["link"]:
                print(f"    {r['link']}")
            if args.history:
                for e in db.application_events(r["text_hash"]):
                    print(f"    {e['at'][:16]}  {e['status']}"
                          f"{' — ' + e['note'] if e['note'] else ''}")
        print(f"\n{len(rows)} shown · all tracked — " +
              " · ".join(f"{k}: {n}" for k, n in sorted(db.application_counts().items())
                         if k != "new"))
        return

    # run
    if args.refilter:
        scorer.refilter(cfg, db)
    if args.prune_days is not None:
        m, v = db.prune_older_than(args.prune_days)
        print(f"Pruned {m} posting(s) and {v} verdict(s) older than "
              f"{args.prune_days} days.")
    if args.reset_scores:
        n = db.reset_scores()
        print(f"Cleared {n} cached score(s) — re-scoring below.")
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
