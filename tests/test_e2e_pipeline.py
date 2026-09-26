"""End-to-end pipeline test: seed messages → prefilter → batch-score with a
fake LLM → dashboard render.

This replaces the old `tests/dry_run.py` script, which pytest never collected
(bad filename). It is the only test that exercises the whole pipeline — the
scorer's queue-building, dedupe-by-hash, prefilter, rule overlay, caching,
idempotency, and the HTML render — against a throwaway SQLite DB.
"""
import json

from jobscan import dashboard, scorer
from jobscan.config import DEFAULTS, _merge
from jobscan.db import DB
from jobscan.llm import extract_json_array

# same corpus as the old dry run: 2 relevant jobs, 1 exact duplicate, and
# one each of: not a job / wrong stack / too short
FAKE_MSGS = [
    (1, "Вакансия: Senior Python Backend разработчик. Компания FinTechCo. "
        "Стек: Python, FastAPI, PostgreSQL, Kafka. Опыт от 5 лет. Вилка 350-450к руб. "
        "Удалёнка из любой страны. Обязанности: проектирование сервисов, код-ревью. "
        "Резюме в личку."),
    (2, "We are hiring a Backend Engineer (Python/Go). Remote, EU timezones. "
        "Salary 70-90k EUR. Requirements: 4+ years backend, Kubernetes, AWS. "
        "Apply via link. Full-time position at CloudCorp."),
    (3, "Вакансия:  Senior Python Backend разработчик. Компания FinTechCo. "
        "Стек: Python, FastAPI, PostgreSQL, Kafka. Опыт от 5 лет. Вилка 350-450к руб. "
        "Удалёнка из любой страны. Обязанности: проектирование сервисов, код-ревью. "
        "Резюме в личку."),  # cross-posted duplicate of #1 (different whitespace)
    (4, "Подборка бесплатных курсов по программированию на этой неделе! Забирайте."),
    (5, "Вакансия: 1C-разработчик, офис Москва, зарплата 200к, опыт от 3 лет, "
        "обязанности: доработка конфигураций, требования: знание 1С 8.3."),
    (6, "ищем джуна"),
]

RESUME = ("Senior Python backend developer, 6 years. FastAPI, PostgreSQL, AWS. "
          "Remote only." * 3)


class FakeBackend:
    name = "fake:test"

    def complete(self, prompt):
        n = prompt.count("--- posting id=")
        out = []
        for i in range(1, n + 1):
            out.append({
                "id": i, "relevant": True, "score": 60 + i * 10,
                "title": f"Fake Job {i}", "company": "TestCo",
                "salary": "100k", "location": "remote",
                "reasons_apply": ["stack match", "remote"],
                "reasons_skip": ["salary unclear"],
                "strengths": ["python"], "weaknesses": ["no kafka"],
            })
        return "Here you go:\n```json\n" + json.dumps(out) + "\n```"


def _seed(tmp_path):
    cfg = _merge(DEFAULTS, {
        "prefilter": {"my_keywords": ["python", "backend"]},
        "db_path": str(tmp_path / "pipeline.db"),
        "dashboard_path": str(tmp_path / "dashboard.html"),
    })
    db = DB(cfg["db_path"])
    db.upsert_channel(100, "test_jobs", "Test Jobs Channel")
    for mid, text in FAKE_MSGS:
        db.add_message(100, mid, f"2026-07-0{mid}T10:00:00+00:00", text,
                       f"https://t.me/test_jobs/{mid}")
        db.set_last_msg_id(100, mid)
    return cfg, db


def test_pipeline_counts_dedupe_and_filter(tmp_path):
    cfg, db = _seed(tmp_path)
    scored = scorer.score_pending(cfg, db, FakeBackend(), RESUME)
    stats = db.stats()

    assert scored == 2
    assert stats.get("scored") == 2, stats
    assert stats.get("duplicate") == 1, stats   # message 3, same hash as 1
    assert stats.get("filtered_out") == 3, stats  # courses, 1C, too-short
    assert db.last_msg_id(100) == 6  # incremental-fetch bookkeeping intact


def test_pipeline_is_idempotent(tmp_path):
    cfg, db = _seed(tmp_path)
    scorer.score_pending(cfg, db, FakeBackend(), RESUME)
    # a second run must score nothing: verdicts are cached, statuses advanced
    assert scorer.score_pending(cfg, db, FakeBackend(), RESUME) == 0


def test_dashboard_renders_scored_postings(tmp_path):
    cfg, db = _seed(tmp_path)
    scorer.score_pending(cfg, db, FakeBackend(), RESUME)
    out = dashboard.render(cfg, db)
    html = open(out, encoding="utf-8").read()
    assert "Fake Job 1" in html
    assert "https://t.me/test_jobs/" in html


def test_extract_json_array_tolerates_prose_and_fences():
    assert extract_json_array('bla [ {"a": "x]y"} ] bla') == [{"a": "x]y"}]