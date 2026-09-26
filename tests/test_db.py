"""Storage-layer tests: application bookkeeping + `remove-channel` semantics.

`remove_channel` is the fiddly one: it must delete a channel and its messages,
but keep any verdict still referenced by another channel's cross-post or by an
application you're tracking (status past 'new').
"""
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