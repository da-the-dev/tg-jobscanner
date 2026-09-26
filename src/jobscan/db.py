"""Storage layer: message cache, verdict cache, application tracking.

Thin facade over SQLAlchemy so callers never touch sessions. Every method runs
in its own short-lived session, which also makes the object safe to share
between the CLI and the (threaded) API server.
"""
import datetime
import hashlib
import json
import re
from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, func, inspect, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import sessionmaker

from .models import (STATUSES, Application, ApplicationEvent, Base, Channel,
                     LinkFetch, Message, Verdict)

__all__ = ["DB", "STATUSES", "text_hash"]

NOW = func.datetime("now")


def text_hash(text):
    norm = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


class DB:
    def __init__(self, path):
        self.path = path
        self.engine = create_engine(f"sqlite+pysqlite:///{path}",
                                    connect_args={"check_same_thread": False})
        self._ensure_schema()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def _ensure_schema(self):
        """Bring the DB up to the latest schema via Alembic (see migrations/).

        A brand new file has no tables yet — `upgrade("head")` runs every
        revision from scratch. A pre-Alembic DB (this project's own history:
        schema managed by create_all() + an ad-hoc ALTER before Alembic was
        adopted) has the tables but no `alembic_version` row — replaying
        revision 0001's CREATE TABLEs against it would collide, so it's
        stamped at whichever revision its actual columns already match
        first, then only the remaining revisions run.
        """
        cfg = Config()
        cfg.set_main_option("script_location", str(files("jobscan").joinpath("migrations")))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{self.path}")

        insp = inspect(self.engine)
        if insp.has_table("channels") and not insp.has_table("alembic_version"):
            has_links_col = "links" in {c["name"] for c in insp.get_columns("messages")}
            command.stamp(cfg, "0002" if has_links_col else "0001")
        command.upgrade(cfg, "head")

    # --- channels ---
    def upsert_channel(self, cid, username, title):
        stmt = insert(Channel).values(id=cid, username=username, title=title)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Channel.id],
            set_={"username": stmt.excluded.username, "title": stmt.excluded.title})
        with self.Session.begin() as s:
            s.execute(stmt)

    def last_msg_id(self, cid):
        with self.Session() as s:
            return s.scalar(select(Channel.last_msg_id).where(Channel.id == cid)) or 0

    def set_last_msg_id(self, cid, msg_id):
        with self.Session.begin() as s:
            s.execute(update(Channel)
                      .where(Channel.id == cid, Channel.last_msg_id < msg_id)
                      .values(last_msg_id=msg_id))

    # --- messages ---
    def add_message(self, cid, msg_id, date, text, link, links=None):
        h = text_hash(text)
        with self.Session.begin() as s:
            dup = s.scalar(select(Message.msg_id).where(Message.text_hash == h).limit(1))
            status = "duplicate" if dup is not None else "new"
            res = s.execute(insert(Message).on_conflict_do_nothing().values(
                channel_id=cid, msg_id=msg_id, date=date, text=text,
                text_hash=h, link=link, links=json.dumps(links or []), status=status))
            return res.rowcount > 0, status

    def set_status(self, cid, msg_id, status):
        with self.Session.begin() as s:
            s.execute(update(Message)
                      .where(Message.channel_id == cid, Message.msg_id == msg_id)
                      .values(status=status))

    def set_status_by_hash(self, h, status):
        """Set status on every copy of a posting (cross-posts share a hash)."""
        with self.Session.begin() as s:
            return s.execute(update(Message).where(Message.text_hash == h)
                             .values(status=status)).rowcount

    def all_messages(self):
        with self.Session() as s:
            return list(s.scalars(select(Message).order_by(Message.date)))

    def pending_messages(self):
        """Messages that passed nothing yet: status='new'."""
        with self.Session() as s:
            return list(s.scalars(
                select(Message).where(Message.status == "new").order_by(Message.date)))

    def reset_errors(self):
        """Put failed messages back in the queue so the next run retries them."""
        with self.Session.begin() as s:
            return s.execute(update(Message).where(Message.status == "error")
                             .values(status="new")).rowcount

    def reset_scores(self):
        """Drop every cached verdict and requeue scored/errored messages.

        The next run re-scores everything from scratch. Applications are keyed by
        text_hash independently, so your tracking survives.
        """
        with self.Session.begin() as s:
            n = s.execute(delete(Verdict)).rowcount
            s.execute(update(Message)
                      .where(Message.status.in_(("scored", "error")))
                      .values(status="new"))
        return n

    def prune_older_than(self, days):
        """Delete cached postings older than `days`, plus any verdict no message
        still references. Keeps anything you're tracking (application past 'new').

        Returns (messages_deleted, verdicts_deleted).
        """
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=days)).isoformat()
        tracked = select(Application.text_hash).where(Application.status != "new")
        with self.Session.begin() as s:
            msgs = s.execute(
                delete(Message).where(Message.date < cutoff,
                                      Message.text_hash.not_in(tracked))).rowcount
            verds = s.execute(
                delete(Verdict).where(
                    Verdict.text_hash.not_in(select(Message.text_hash)),
                    Verdict.text_hash.not_in(tracked))).rowcount
        return msgs, verds

    def remove_channel(self, username):
        """Delete a channel and all its cached messages, for when you drop it
        from config.yaml and want it gone from the board entirely. Verdicts
        still referenced by another channel's cross-post, or by a tracked
        application (status past 'new'), are kept.

        Returns (found, messages_deleted, verdicts_deleted).
        """
        username = username.strip().lstrip("@")
        tracked = select(Application.text_hash).where(Application.status != "new")
        with self.Session.begin() as s:
            cid = s.scalar(select(Channel.id)
                           .where(func.lower(Channel.username) == username.lower()))
            if cid is None:
                return False, 0, 0
            msgs = s.execute(delete(Message).where(Message.channel_id == cid)).rowcount
            verds = s.execute(delete(Verdict).where(
                Verdict.text_hash.not_in(select(Message.text_hash)),
                Verdict.text_hash.not_in(tracked))).rowcount
            s.execute(delete(Channel).where(Channel.id == cid))
        return True, msgs, verds

    def stats(self):
        with self.Session() as s:
            return dict(s.execute(select(Message.status, func.count())
                                  .group_by(Message.status)).all())

    # --- verdicts ---
    def has_verdict(self, h):
        with self.Session() as s:
            return s.scalar(select(Verdict.text_hash).where(Verdict.text_hash == h)) is not None

    def verdict(self, h):
        with self.Session() as s:
            return s.get(Verdict, h)

    def save_verdict(self, h, v, model):
        def arr(key):
            return json.dumps(v.get(key) or [], ensure_ascii=False)

        values = dict(
            text_hash=h, relevant=int(bool(v.get("relevant"))), score=int(v.get("score") or 0),
            title=v.get("title") or "", company=v.get("company") or "",
            salary=v.get("salary") or "", location=v.get("location") or "",
            reasons_apply=arr("reasons_apply"), reasons_skip=arr("reasons_skip"),
            strengths=arr("strengths"), weaknesses=arr("weaknesses"),
            flags=arr("flags"), contact_type=v.get("contact_type") or "", model=model)
        stmt = insert(Verdict).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Verdict.text_hash],
            set_={k: v for k, v in values.items() if k != "text_hash"})
        with self.Session.begin() as s:
            s.execute(stmt)

    def delete_verdict(self, h):
        """Drop a cached verdict unless the posting is being tracked. Returns
        the number of rows removed (0 or 1)."""
        tracked = select(Application.text_hash).where(Application.status != "new")
        with self.Session.begin() as s:
            return s.execute(delete(Verdict).where(
                Verdict.text_hash == h,
                Verdict.text_hash.not_in(tracked))).rowcount

    # --- link fetch cache (see links.py) ---
    def get_link_fetch(self, url):
        with self.Session() as s:
            return s.get(LinkFetch, url)

    def save_link_fetch(self, url, status, text):
        stmt = insert(LinkFetch).values(url=url, status=status, text=text, fetched_at=NOW)
        stmt = stmt.on_conflict_do_update(
            index_elements=[LinkFetch.url],
            set_={"status": stmt.excluded.status, "text": stmt.excluded.text,
                  "fetched_at": NOW})
        with self.Session.begin() as s:
            s.execute(stmt)

    def resolve_hash(self, prefix):
        """Match a full hash or unique prefix against scored postings."""
        with self.Session() as s:
            rows = s.scalars(select(Verdict.text_hash)
                             .where(Verdict.text_hash.startswith(prefix)).limit(2)).all()
        if not rows:
            raise KeyError(f"no posting matches hash {prefix!r}")
        if len(rows) > 1:
            raise KeyError(f"hash {prefix!r} is ambiguous — use more characters")
        return rows[0]

    # --- dashboard ---
    def dashboard_rows(self):
        """One row per unique relevant verdict, joined to a message and its status."""
        stmt = (select(Verdict,
                       Message.link, Message.date, Message.channel_id,
                       Channel.title.label("channel_title"), Channel.username,
                       func.coalesce(Application.status, "new").label("app_status"),
                       func.coalesce(Application.note, "").label("app_note"),
                       Application.applied_at,
                       Application.updated_at.label("status_updated"))
                .join(Message, Message.text_hash == Verdict.text_hash)
                .join(Channel, Channel.id == Message.channel_id)
                .outerjoin(Application, Application.text_hash == Verdict.text_hash)
                .where(Verdict.relevant == 1)
                .group_by(Verdict.text_hash)
                .order_by(Verdict.score.desc()))
        with self.Session() as s:
            return [_flatten(r) for r in s.execute(stmt)]

    # --- applications ---
    def set_application(self, h, status=None, note=None):
        """Upsert status/note for a posting and append a history event."""
        if status is not None and status not in STATUSES:
            raise ValueError(f"unknown status {status!r} (expected one of {', '.join(STATUSES)})")
        with self.Session.begin() as s:
            cur = s.get(Application, h)
            status = status or (cur.status if cur else "new")
            note = cur.note if note is None and cur else (note or "")
            # applied_at is stamped the first time a posting reaches 'applied', and kept
            # afterwards — a later rejection must not erase when you applied.
            stmt = insert(Application).values(
                text_hash=h, status=status, note=note, updated_at=NOW,
                applied_at=NOW if status == "applied" else None)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Application.text_hash],
                set_={"status": stmt.excluded.status, "note": stmt.excluded.note,
                      "updated_at": NOW,
                      "applied_at": func.coalesce(Application.applied_at,
                                                  stmt.excluded.applied_at)})
            s.execute(stmt)
            s.add(ApplicationEvent(text_hash=h, status=status, note=note))
        return self.application(h)

    def application(self, h):
        with self.Session() as s:
            row = s.get(Application, h)
            return None if row is None else _asdict(row)

    def application_events(self, h=None):
        stmt = select(ApplicationEvent).order_by(ApplicationEvent.at, ApplicationEvent.id)
        if h is not None:
            stmt = stmt.where(ApplicationEvent.text_hash == h)
        with self.Session() as s:
            return [_asdict(e) for e in s.scalars(stmt)]

    def application_counts(self):
        with self.Session() as s:
            return dict(s.execute(select(Application.status, func.count())
                                  .group_by(Application.status)).all())

    def applications(self, status=None):
        """Tracked postings (status != 'new'), most recently changed first."""
        stmt = (select(Application,
                       Verdict.score, Verdict.title, Verdict.company,
                       Verdict.salary, Verdict.location,
                       Message.link, Message.date,
                       Channel.title.label("channel_title"), Channel.username)
                .outerjoin(Verdict, Verdict.text_hash == Application.text_hash)
                .outerjoin(Message, Message.text_hash == Application.text_hash)
                .outerjoin(Channel, Channel.id == Message.channel_id)
                .where(Application.status != "new")
                .group_by(Application.text_hash)
                .order_by(Application.updated_at.desc()))
        if status:
            stmt = stmt.where(Application.status == status)
        with self.Session() as s:
            return [_flatten(r) for r in s.execute(stmt)]


def _asdict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _flatten(row):
    """Row of (entity, extra columns…) → one plain dict, JSON-serialisable."""
    out = {}
    for key, value in row._mapping.items():
        if isinstance(value, Base):
            out.update(_asdict(value))
        else:
            out[key] = value
    return out
