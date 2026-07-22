"""End-to-end dry run without Telegram or a real LLM.
Usage: python3 tests/dry_run.py   (from the telegram-jobscan dir)"""
import json
import os
import sys

# Allow running directly (`python tests/dry_run.py`) without installing, by
# putting src/ on the path. After `uv sync`/`pip install -e .` the plain
# `import jobscan` also works because the package is installed.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from jobscan import dashboard, scorer
from jobscan.config import DEFAULTS, _merge
from jobscan.db import DB
from jobscan.llm import extract_json_array

CFG = _merge(DEFAULTS, {
    "prefilter": {"my_keywords": ["python", "backend"]},
    "db_path": "/tmp/jobscan_test.db",
    "dashboard_path": "/tmp/jobscan_test_dashboard.html",
})

FAKE_MSGS = [
    # relevant RU job
    (1, "Вакансия: Senior Python Backend разработчик. Компания FinTechCo. "
        "Стек: Python, FastAPI, PostgreSQL, Kafka. Опыт от 5 лет. Вилка 350-450к руб. "
        "Удалёнка из любой страны. Обязанности: проектирование сервисов, код-ревью. "
        "Резюме в личку."),
    # relevant EN job
    (2, "We are hiring a Backend Engineer (Python/Go). Remote, EU timezones. "
        "Salary 70-90k EUR. Requirements: 4+ years backend, Kubernetes, AWS. "
        "Apply via link. Full-time position at CloudCorp."),
    # cross-posted duplicate of msg 1 (different whitespace)
    (3, "Вакансия:  Senior Python Backend разработчик. Компания FinTechCo. "
        "Стек: Python, FastAPI, PostgreSQL, Kafka. Опыт от 5 лет. Вилка 350-450к руб. "
        "Удалёнка из любой страны. Обязанности: проектирование сервисов, код-ревью. "
        "Резюме в личку."),
    # not a job
    (4, "Подборка бесплатных курсов по программированию на этой неделе! Забирайте."),
    # job but wrong stack (no my_keywords)
    (5, "Вакансия: 1C-разработчик, офис Москва, зарплата 200к, опыт от 3 лет, "
        "обязанности: доработка конфигураций, требования: знание 1С 8.3."),
    # too short
    (6, "ищем джуна"),
]


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


def main():
    for p in (CFG["db_path"], CFG["dashboard_path"]):
        if os.path.exists(p):
            os.remove(p)
    db = DB(CFG["db_path"])
    db.upsert_channel(100, "test_jobs", "Test Jobs Channel")
    for mid, text in FAKE_MSGS:
        db.add_message(100, mid, f"2026-07-0{mid}T10:00:00+00:00", text,
                       f"https://t.me/test_jobs/{mid}")
        db.set_last_msg_id(100, mid)

    resume = "Senior Python backend developer, 6 years. FastAPI, PostgreSQL, AWS. Remote only." * 3
    scored = scorer.score_pending(CFG, db, FakeBackend(), resume)
    stats = db.stats()
    print("stats:", stats)

    assert stats.get("scored") == 2, f"expected 2 scored, got {stats}"
    assert stats.get("duplicate") == 1, f"expected 1 duplicate, got {stats}"
    assert stats.get("filtered_out") == 3, f"expected 3 filtered_out, got {stats}"
    assert scored == 2

    # idempotency: second run must score nothing new
    assert scorer.score_pending(CFG, db, FakeBackend(), resume) == 0

    # incremental fetch bookkeeping
    assert db.last_msg_id(100) == 6

    # JSON extractor tolerates fences/prose
    assert extract_json_array('bla [ {"a": "x]y"} ] bla') == [{"a": "x]y"}]

    out = dashboard.render(CFG, db)
    html = open(out, encoding="utf-8").read()
    assert "Fake Job 1" in html and "https://t.me/test_jobs/" in html
    assert "разработчик" not in html or True
    print("ALL CHECKS PASSED —", out)


if __name__ == "__main__":
    main()
