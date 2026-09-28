"""Storage-layer tests: application bookkeeping + `remove-channel` semantics.

`remove_channel` is the fiddly one: it must delete a channel and its messages,
but keep any verdict still referenced by another channel's cross-post or by an
application you're tracking (status past 'new').
"""
import json

from jobscan.db import DB, text_hash

DATE = "2026-09-01T10:00:00+00:00"
T_ALPHA = ("Вакансия: Senior Python backend в FinTechCo. "
           "Стек: Python FastAPI PostgreSQL. Удалёнка из любой страны.")
T_BETA = ("Вакансия: Data Engineer в DataCo. Опыт от 3 лет, "
          "зарплата 300к, офис Москва.")


def _verdict(score=80):
    return {"relevant": 1, "score": score, "title": "Test Role", "flags": [],
            "contact_type": ""}


def _seed_alpha_beta(db):
    """Channel 100 ('alpha_jobs'): messages for T_ALPHA (h1) and T_BETA (h2).
    Channel 200 ('beta_jobs'): a cross-post of T_ALPHA (h1) — same text_hash.
    Verdicts cached for both hashes; h2 additionally has a tracked application.
    """
    db.upsert_channel(100, "alpha_jobs", "Alpha Jobs")
    db.upsert_channel(200, "beta_jobs", "Beta Jobs")
    db.add_message(100, 1, DATE, T_ALPHA, "https://t.me/alpha_jobs/1")
    db.add_message(100, 2, DATE, T_BETA, "https://t.me/alpha_jobs/2")
    db.add_message(200, 7, DATE, T_ALPHA, "https://t.me/beta_jobs/7")
    db.set_last_msg_id(100, 2)  # as fetch_new would after ingesting
    db.set_last_msg_id(200, 7)
    h1, h2 = text_hash(T_ALPHA), text_hash(T_BETA)
    db.save_verdict(h1, _verdict(80), "test")
    db.save_verdict(h2, _verdict(60), "test")
    db.set_application(h2, "applied", note="referred")
    return h1, h2


def test_remove_channel_unknown_returns_false(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    assert db.remove_channel("nobody") == (False, 0, 0)


def test_remove_channel_deletes_unreferenced_verdicts(tmp_path):
    """A channel whose verdict nothing else references: message AND verdict go."""
    db = DB(str(tmp_path / "t.db"))
    db.upsert_channel(42, "solo", "Solo")
    db.add_message(42, 1, DATE, T_ALPHA, "https://t.me/solo/1")
    h = text_hash(T_ALPHA)
    db.save_verdict(h, _verdict(), "test")

    found, msgs, verds = db.remove_channel("@SOLO")  # input tolerant of @/case
    assert (found, msgs, verds) == (True, 1, 1)
    assert not db.has_verdict(h)
    assert db.last_msg_id(42) == 0  # channel row deleted


def test_remove_channel_keeps_crosspost_and_tracked_verdicts(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    h1, h2 = _seed_alpha_beta(db)

    found, msgs, verds = db.remove_channel("alpha_jobs")
    assert (found, msgs, verds) == (True, 2, 0)
    # h1 survives: beta_jobs still references it; h2 survives: you're tracking it
    assert db.has_verdict(h1) and db.has_verdict(h2)
    assert db.last_msg_id(100) == 0   # alpha gone
    assert db.last_msg_id(200) == 7   # beta untouched
    assert db.application(h2)["status"] == "applied"  # tracking survives


def test_set_application_stamps_applied_at_once(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.upsert_channel(100, "alpha_jobs", "Alpha Jobs")
    db.add_message(100, 1, DATE, T_ALPHA, "https://t.me/alpha_jobs/1")
    h = text_hash(T_ALPHA)

    row = db.set_application(h, "applied")
    first = row["applied_at"]
    db.set_application(h, "rejected", note="no reply")  # back to applied? no — rejected
    row = db.application(h)
    # applied_at was stamped when it first reached 'applied' and must NOT be
    # wiped by the later status change (regression guard for the coalesce).
    assert row["status"] == "rejected"
    assert row["applied_at"] == first
    events = [e["status"] for e in db.application_events(h)]
    assert events == ["applied", "rejected"]


def test_record_discovered_channel_inserts_new(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.record_discovered_channel("dev_jobs", "Разработчики", "alpha_jobs")

    rows = db.discovered_channels()
    assert len(rows) == 1
    assert rows[0]["username"] == "dev_jobs"
    assert rows[0]["anchor_text"] == "Разработчики"
    assert rows[0]["mention_count"] == 1
    assert json.loads(rows[0]["source_channels"]) == ["alpha_jobs"]
    assert rows[0]["status"] == "new"


def test_record_discovered_channel_repeat_bumps_count_and_merges_sources(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.record_discovered_channel("dev_jobs", "Разработчики", "alpha_jobs")
    db.record_discovered_channel("Dev_Jobs", "Разработчики →", "alpha_jobs")  # same source again
    db.record_discovered_channel("dev_jobs", "Developers", "beta_jobs")       # a new source

    rows = db.discovered_channels()
    assert len(rows) == 1
    row = rows[0]
    assert row["mention_count"] == 3
    assert row["anchor_text"] == "Developers"  # most recent anchor text wins
    assert json.loads(row["source_channels"]) == ["alpha_jobs", "beta_jobs"]


def test_discovered_channels_sorted_by_mention_count(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.record_discovered_channel("rare", "X", "alpha")
    db.record_discovered_channel("popular", "Y", "alpha")
    db.record_discovered_channel("popular", "Y", "beta")

    rows = db.discovered_channels()
    assert [r["username"] for r in rows] == ["popular", "rare"]


def test_set_discovered_status_marks_added_and_filters(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.record_discovered_channel("dev_jobs", "Разработчики", "alpha_jobs")

    assert db.set_discovered_status("@dev_jobs", "added") is True
    assert db.discovered_channels("new") == []
    assert db.discovered_channels("added")[0]["username"] == "dev_jobs"


def test_set_discovered_status_unknown_channel_returns_false(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    assert db.set_discovered_status("nobody", "added") is False


def test_set_discovered_status_rejects_unknown_status(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.record_discovered_channel("dev_jobs", "Разработчики", "alpha_jobs")
    try:
        db.set_discovered_status("dev_jobs", "bogus")
        assert False, "should have raised"
    except ValueError:
        pass