from jobscan.links import analyze, classify, crossposts_in

# --- TaxDome-style posting: one real apply link, rest are channel crossposts ---
TAXDOME_LINKS = [
    ("Откликнуться тут", "https://example-ats.com/jobs/senior-ai-engineer"),
    ("Продакты и менеджеры", "https://t.me/pm_channel"),
    ("Дизайнеры", "https://t.me/design_channel"),
    ("Разработчики", "https://t.me/dev_channel"),
]

# --- Velvetech-style posting: the apply link is itself a personal TG contact ---
VELVETECH_LINKS = [
    ("тут", "https://t.me/some_recruiter"),
    ("React Developer", "https://t.me/velvetech_jobs/501"),
    ("Senior Data Engineer", "https://t.me/velvetech_jobs/502"),
]


def test_external_apply_link_is_classified_apply():
    assert classify("Откликнуться тут", "https://example-ats.com/jobs/1") == "apply"


def test_bare_channel_link_with_generic_anchor_is_crosspost():
    assert classify("Продакты и менеджеры", "https://t.me/pm_channel") == "crosspost"


def test_bare_channel_link_with_apply_anchor_is_recruiter_contact():
    assert classify("тут", "https://t.me/some_recruiter") == "recruiter_contact"


def test_bot_deep_link_is_bot():
    assert classify("Откликнуться", "https://t.me/hr_ai_bot?start=vacancy1") == "bot"


def test_mailto_is_contact():
    assert classify("написать", "mailto:hr@company.com") == "contact"


def test_specific_post_link_is_apply():
    assert classify("React Developer", "https://t.me/velvetech_jobs/501") == "apply"


def test_analyze_picks_external_apply_over_crossposts():
    url, kind, has_contact = analyze(TAXDOME_LINKS)
    assert url == "https://example-ats.com/jobs/senior-ai-engineer"
    assert kind == "apply"
    assert has_contact is False


def test_analyze_flags_recruiter_contact_even_when_an_apply_link_also_exists():
    url, kind, has_contact = analyze(VELVETECH_LINKS)
    # the numbered posts outrank the bare recruiter link as the fetch target,
    # but the recruiter contact is still surfaced via has_recruiter_contact
    assert kind == "apply"
    assert has_contact is True


def test_analyze_with_no_links_returns_nothing():
    assert analyze([]) == (None, None, False)


def test_crossposts_in_finds_only_the_crosspost_links():
    out = dict(crossposts_in(TAXDOME_LINKS))
    assert out == {"pm_channel": "Продакты и менеджеры",
                   "design_channel": "Дизайнеры", "dev_channel": "Разработчики"}


def test_crossposts_in_excludes_apply_and_recruiter_contact_links():
    # velvetech: a recruiter-contact root link + two numbered apply posts —
    # none of those are crossposts, so nothing should come back
    assert crossposts_in(VELVETECH_LINKS) == []


def test_crossposts_in_dedupes_a_repeated_footer_link():
    repeated = [("Разработчики", "https://t.me/dev_channel"),
               ("Разработчики →", "https://t.me/dev_channel")]
    out = crossposts_in(repeated)
    assert len(out) == 1
    assert out[0][0] == "dev_channel"
