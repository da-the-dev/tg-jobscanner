"""Incremental Telegram fetch via Telethon (user account, MTProto)."""
import datetime

from telethon.sync import TelegramClient


def make_client(cfg):
    tg = cfg["telegram"]
    return TelegramClient(tg.get("session", "jobscan"), int(tg["api_id"]), tg["api_hash"])


def msg_link(entity, msg_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    return f"https://t.me/c/{entity.id}/{msg_id}"


def fetch_new(cfg, db, log=print):
    """Fetch only messages newer than last_msg_id per channel. First run is
    capped by backfill.days AND backfill.max_messages."""
    days = int(cfg["backfill"]["days"])
    max_msgs = int(cfg["backfill"]["max_messages"])
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    added = 0

    with make_client(cfg) as client:
        for ref in cfg["channels"]:
            ref = str(ref).replace("https://t.me/", "").strip("/@ ")
            try:
                entity = client.get_entity(ref)
            except Exception as e:
                log(f"  !! cannot resolve '{ref}': {e}")
                continue
            db.upsert_channel(entity.id, getattr(entity, "username", None),
                              getattr(entity, "title", ref))
            last = db.last_msg_id(entity.id)
            kwargs = {"min_id": last} if last else {"limit": max_msgs}
            n = 0
            for msg in client.iter_messages(entity, **kwargs):
                if not last and msg.date < cutoff:
                    break  # first run: stop at time horizon
                text = msg.message or ""
                if text.strip():
                    inserted, _ = db.add_message(
                        entity.id, msg.id, msg.date.isoformat(), text,
                        msg_link(entity, msg.id))
                    n += inserted
                db.set_last_msg_id(entity.id, msg.id)
            added += n
            log(f"  {getattr(entity, 'title', ref)}: +{n} new")
    return added


def list_dialogs(cfg):
    """Helper: print channels the account is subscribed to (for config.yaml)."""
    with make_client(cfg) as client:
        for d in client.iter_dialogs():
            if d.is_channel:
                uname = getattr(d.entity, "username", None)
                print(f"{d.name:50.50}  {('@' + uname) if uname else '(private, id=' + str(d.entity.id) + ')'}")
