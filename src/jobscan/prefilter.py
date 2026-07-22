"""Zero-cost keyword prefilter (RU/EN). Only survivors reach the LLM."""
import re

# Words that signal "this message is a job posting"
JOB_SIGNALS = [
    # EN
    "hiring", "we are looking", "we're looking", "looking for", "job opening",
    "position", "vacancy", "salary", "full-time", "part-time", "remote",
    "requirements", "responsibilities", "apply", "recruiter", "стек",
    # RU
    "вакансия", "вакансии", "ищем", "ищет", "требуется", "требуются",
    "зарплата", "зп", "вилка", "оклад", "удалёнка", "удаленка", "удалённо",
    "удаленно", "офис", "опыт от", "опыт работы", "обязанности", "требования",
    "условия", "занятость", "оформление", "собеседование", "резюме",
    "junior", "middle", "senior", "джун", "мидл", "сеньор", "синьор", "лид",
]


def is_job_post(text, min_length=80, my_keywords=None):
    """Return (passed, reason)."""
    if not text or len(text) < min_length:
        return False, "too_short"
    low = text.lower()
    if not any(s in low for s in JOB_SIGNALS):
        return False, "no_job_signals"
    if my_keywords:
        if not any(k.lower() in low for k in my_keywords):
            return False, "no_my_keywords"
    return True, "ok"


def compact(text, max_chars=3000):
    """Trim a JD for the LLM prompt: collapse whitespace, cap length."""
    t = re.sub(r"\n{3,}", "\n\n", text).strip()
    return t[:max_chars] + ("…" if len(t) > max_chars else "")
