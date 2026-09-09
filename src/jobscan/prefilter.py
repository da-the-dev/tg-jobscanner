"""Zero-cost keyword prefilter (RU/EN). Only survivors reach the LLM."""
import functools
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

# Markers that a message is a candidate advertising THEMSELVES (a CV / "job
# wanted" post) rather than a vacancy. These job channels are two-sided: people
# post "#резюме … ищу работу, CV прикладываю" alongside real openings. A vacancy
# almost never carries first-person job-seeking language, so one hit is decisive.
#
# Kept deliberately tight — recruiters write "I'm looking for a PM", vacancies
# say "прикрепляйте резюме", and "#cv" usually means Computer Vision — so those
# are NOT markers. Every pattern below is either an unambiguous self-tag or
# something only the job seeker themselves would write.
CV_MARKERS = re.compile(
    r"""
      # --- unambiguou self-tags ---
      \#(?:резюме|opentowork|open_to_work|ищуработу|ищу_работу|ищу_работа
         |ищувакансию|ищу_вакансию|lookingforjob|looking_for_job|jobseeker
         |job_seeker|hireme|hire_me|freelancer|ищу_проект|ищупроект)\b
      # --- EN first-person job seeking ---
    | \bopen\s+to\s+(?:work\b|new\s+(?:roles?|opportunities|challenges?)\b)
    | \b(?:looking|searching)\s+for\s+(?:a\s+)?(?:new\s+)?(?:role|position|opportunit)\b
    | \blooking\s+for\s+my\s+next\b
    | \bseeking\s+(?:a\s+)?(?:new\s+)?(?:role|position|opportunit|challenge)\b
    | \bseeking\s+(?:new\s+)?(?:opportunities|employment|work\b)
    | \bon\s+the\s+(?:job\s+)?market\s+for\b
    | \b(?:my|attached\s+is\s+my|here'?s\s+my|please\s+find\s+my)\s+(?:cv|resume)\b
    | \b(?:cv|resume)\s+(?:is\s+)?(?:attached|enclosed|below)\b
    | \bavailable\s+for\s+hire\b
      # --- RU first-person job seeking ---
    | ищу\s+(?:новую\s+|удал[её]нную\s+|части?чную\s+|постоянную\s+|интересную\s+)*работу
    | (?:в\s+(?:активном\s+)?поиске|нахожусь\s+в\s+поиске)
        \s+(?:новой\s+|новых\s+|новое\s+)?(?:работы|места|проекта|проектов|ролей?|позици)
    | рассматриваю\s+(?:новые\s+|интересные\s+|только\s+)?(?:предложени|ваканси|оффер|позици|варианты)
    | открыт[аы]?\s+к\s+(?:новым\s+)?(?:предложени|вакансиям|офферам?|возможностям)
    | (?:резюме|cv|сv)\s+(?:прилагаю|прикладываю|приложил[аи]?|приложу|прикреплю|отправлю
        |ниже|во\s+вложении|прикреплен|скину)
    | (?:прилагаю|прикладываю|приложил[аи]?|прикрепляю|скидываю)\s+(?:сво[её]\s+)?(?:резюме|cv|сv)
    | (?:мо[её]|мои)\s+(?:резюме|портфолио)\b
    | если\s+у\s+(?:кого-то|вас|кого|кого-нибудь)\s+есть\s+(?:подходящие\s+)?ваканси
    | буду\s+(?:рад|рада)\s+(?:рекомендаци|совет|наводк|знакомств|нетворк)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def looks_like_cv(text):
    """True if the message reads as someone's own CV / job-wanted post."""
    return bool(text and CV_MARKERS.search(text))


@functools.lru_cache(maxsize=16)
def _keyword_re(keywords):
    """One word-boundary regex for all my_keywords (tuple => hashable/cached).

    Matched on boundaries, not raw substrings, so "ai" no longer fires on
    "email"/"retail" nor "ml" on "html"/"aml". Internal spaces tolerate any
    whitespace run ("machine  learning", line breaks).
    """
    parts = [re.escape(k.lower().strip()).replace(r"\ ", r"\s+").replace(" ", r"\s+")
             for k in keywords if k and k.strip()]
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")


def is_job_post(text, min_length=80, my_keywords=None):
    """Return (passed, reason)."""
    if not text or len(text) < min_length:
        return False, "too_short"
    if looks_like_cv(text):
        return False, "candidate_cv"
    low = text.lower()
    if not any(s in low for s in JOB_SIGNALS):
        return False, "no_job_signals"
    if my_keywords:
        rx = _keyword_re(tuple(my_keywords))
        if rx and not rx.search(low):
            return False, "no_my_keywords"
    return True, "ok"


def compact(text, max_chars=3000):
    """Trim a JD for the LLM prompt: collapse whitespace, cap length."""
    t = re.sub(r"\n{3,}", "\n\n", text).strip()
    return t[:max_chars] + ("…" if len(t) > max_chars else "")
