"""Score prefiltered messages against the resume, in batches, with caching."""
import datetime
import json
import queue
import threading

from . import links as links_mod
from . import prefilter
from .llm import extract_json_array
from .progress import Progress

ERROR_LOG = "jobscan_errors.log"

PROMPT = """You are screening Telegram job postings (Russian or English) against a candidate's resume.

CANDIDATE RESUME:
<resume>
{resume}
</resume>

Below are {n} postings. For EACH one return a JSON object with exactly these keys:
  "id": the posting id (integer, as given),
  "relevant": true only if it is a real job posting that plausibly fits this candidate's profile and seniority; false for ads, courses, digests, off-profile roles,
  "score": 0-100 overall fit — use the FULL range, most postings are NOT a great fit:
     0-20  wrong domain or wrong seniority entirely (e.g. junior role for a senior candidate),
     21-40 right domain but a real dealbreaker (seniority/location/stack mismatch),
     41-60 plausible but with meaningful gaps,
     61-80 strong fit with minor gaps,
     81-100 excellent fit.
  "title": job title, "company": company or "" if unknown,
  "salary": salary/fork as stated or "", "location": e.g. "remote", "hybrid Moscow", "onsite Berlin" or "",
  "seniority": one of "junior", "middle", "senior", "lead", "unclear" — the LEVEL THE POSTING IS HIRING FOR (not the candidate's level),
  "requires_russia": true only if the posting explicitly requires being physically based in Russia / Russian tax residency / a Russian legal entity (ИП) — false if remote-anywhere or silent on it,
  "reasons_apply": 1-4 short bullets why the candidate should apply,
  "reasons_skip": 1-4 short bullets why they might skip it,
  "strengths": 1-4 candidate strengths for THIS job, "weaknesses": 1-4 gaps for THIS job.
If relevant is false: still set id/relevant/score(low)/title/seniority/requires_russia, leave other fields as "" or [].
Write all analysis text in {lang}. Respond with ONLY a JSON array of {n} objects, no other text.

POSTINGS:
{postings}"""


def _build_queue(cfg, db):
    """Prefilter + dedupe status='new' messages into a scoring queue.

    Each item also carries its link analysis (see links.py): the apply link
    worth fetching for extra JD context, and whether a direct recruiter
    contact was found (independent of whether an apply link exists too).
    """
    pf = cfg["prefilter"]
    queue = []  # (channel_id, msg_id, hash, text, apply_url, has_recruiter_contact)
    seen = set()
    for m in db.pending_messages():
        h = m.text_hash
        if db.has_verdict(h):  # cross-post already scored earlier
            db.set_status(m.channel_id, m.msg_id, "scored")
            continue
        passed, _ = prefilter.is_job_post(m.text, pf["min_length"], pf["my_keywords"])
        if not passed:
            db.set_status(m.channel_id, m.msg_id, "filtered_out")
            continue
        if h in seen:  # dupe inside this run
            db.set_status(m.channel_id, m.msg_id, "duplicate")
            continue
        seen.add(h)
        raw_links = json.loads(m.links or "[]")
        apply_url, _kind, has_contact = links_mod.analyze(raw_links)
        queue.append((m.channel_id, m.msg_id, h, m.text, apply_url, has_contact))
    return queue


def _enrich(cfg, db, apply_url, budget):
    """Fetched apply-link text for one queued item, or "" if unavailable.

    Cached by URL (db.get_link_fetch) so a link shared across cross-posted
    duplicates, or seen again on a rerun, is only ever fetched once — cache
    hits are free and don't count against `budget` (a per-run new-fetch cap).
    """
    cached = db.get_link_fetch(apply_url)
    if cached is not None:
        return cached.text or "" if cached.status == "ok" else ""
    if budget["n"] <= 0:
        return ""
    budget["n"] -= 1
    lc = cfg.get("links") or {}
    text = links_mod.fetch_text(apply_url, timeout=int(lc.get("fetch_timeout", 10)))
    db.save_link_fetch(apply_url, "ok" if text else "failed", text)
    return text or ""


def _apply_rules(cfg, v, has_contact):
    """Deterministic, code-side overlay on top of the LLM's own judgement.

    Never touches `relevant` — these flags dim a row on the dashboard, they
    never hide it (a misfiring rule should be easy to notice and override).
    """
    pf = cfg["prefilter"]
    reject_seniority = {s.lower() for s in pf.get("reject_seniority", ["middle"])}
    flags = []
    seniority = (v.get("seniority") or "").strip().lower()
    if seniority in reject_seniority:
        flags.append(f"seniority:{seniority}")
    if v.get("requires_russia") and pf.get("reject_russia_only", True):
        flags.append("requires_russia")
    v["flags"] = flags
    if has_contact:
        v["contact_type"] = "recruiter_contact"
        bonus = int((cfg.get("links") or {}).get("recruiter_bonus", 8))
        v["score"] = min(100, int(v.get("score") or 0) + bonus)
    else:
        v["contact_type"] = ""
    return v


class _BatchError(Exception):
    """Wraps a batch failure together with the raw LLM reply, for logging."""

    def __init__(self, cause, raw=""):
        super().__init__(str(cause))
        self.cause = cause
        self.raw = raw


def _run_batch(backend, prompt):
    """Returns parsed verdicts or raises _BatchError."""
    try:
        raw = backend.complete(prompt)
    except Exception as e:
        raise _BatchError(e) from e
    try:
        return extract_json_array(raw)
    except Exception as e:
        raise _BatchError(e, raw) from e


def _worker(backend, job_q, result_q):
    """Pull (batch, prompt) jobs off job_q until empty; push results to result_q.

    Never touches the DB — every result goes back to the main thread, which is
    the only place verdicts and statuses are written.
    """
    while True:
        try:
            batch, prompt = job_q.get_nowait()
        except queue.Empty:
            return
        try:
            result_q.put((batch, _run_batch(backend, prompt), None))
        except _BatchError as e:
            result_q.put((batch, None, e))
        except Exception as e:  # never let a worker die silently and hang main
            result_q.put((batch, None, _BatchError(e)))
        finally:
            job_q.task_done()


def _log_batch_error(err, log_path=ERROR_LOG):
    """Append the raw LLM reply for a failed batch so it can be inspected later."""
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n=== {datetime.datetime.now().isoformat(timespec='seconds')} "
                f"— {err.cause} ===\n")
        f.write(err.raw or "(no reply captured — request itself failed)\n")


def score_pending(cfg, db, backend, resume, log=print):
    """Take status='new' messages, prefilter, batch-score in parallel, cache."""
    lang = {"en": "English", "ru": "Russian"}.get(
        cfg["llm"]["output_language"], cfg["llm"]["output_language"])
    batch_size = int(cfg["llm"]["batch_size"])
    concurrency = max(1, int(cfg["llm"].get("concurrency", 4)))

    pending = _build_queue(cfg, db)
    if not pending:
        log(f"  0 new unique postings to score (backend: {backend.name})")
        return 0

    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
    log(f"  {len(pending)} postings in {len(batches)} batches "
        f"× {concurrency} workers (backend: {backend.name})")

    # Follow apply links for postings too thin to judge from the TG text alone
    # (see links.py) — sequential, capped, cached by URL across dupes/reruns.
    lc = cfg.get("links") or {}
    min_chars = int(lc.get("skip_fetch_min_chars", 400))
    budget = {"n": int(lc.get("max_fetches_per_run", 60)) if lc.get("enabled", True) else 0}
    enrichment = {}  # hash -> extra text
    for _cid, _mid, h, text, apply_url, _has_contact in pending:
        if apply_url and len(text) < min_chars:
            extra = _enrich(cfg, db, apply_url, budget)
            if extra:
                enrichment[h] = extra
    if enrichment:
        log(f"  followed {len(enrichment)} apply link(s) for extra context")

    # Build prompts up front (pure CPU), then farm the LLM calls to threads.
    jobs = []
    for batch in batches:
        chunks = []
        for j, item in enumerate(batch):
            h, text = item[2], item[3]
            full_text = text + "\n\n" + enrichment[h] if h in enrichment else text
            chunks.append(f"--- posting id={j + 1} ---\n{prefilter.compact(full_text)}")
        postings = "\n\n".join(chunks)
        jobs.append((batch, PROMPT.format(resume=resume, n=len(batch),
                                          lang=lang, postings=postings)))

    job_q = queue.Queue()
    for job in jobs:
        job_q.put(job)
    result_q = queue.Queue()
    workers = [threading.Thread(target=_worker, args=(backend, job_q, result_q),
                                daemon=True)
               for _ in range(min(concurrency, len(jobs)))]
    for w in workers:
        w.start()

    scored = failed = 0
    bar = Progress(len(jobs), label="  scoring")
    # DB writes happen ONLY here on the main thread as results arrive.
    for _ in range(len(jobs)):
        batch, verdicts, err = result_q.get()
        if err is not None:
            failed += 1
            _log_batch_error(err)
            for cid, mid, *_ in batch:
                db.set_status(cid, mid, "error")
            bar.advance(suffix=f"⚠ {str(err)[:40]}")
            continue
        by_id = {int(v.get("id", 0)): v for v in verdicts if isinstance(v, dict)}
        for j, (cid, mid, h, _text, _apply_url, has_contact) in enumerate(batch):
            v = by_id.get(j + 1)
            if v is None:
                db.set_status(cid, mid, "error")
                continue
            db.save_verdict(h, _apply_rules(cfg, v, has_contact), backend.name)
            db.set_status(cid, mid, "scored")
            scored += 1
        bar.advance(suffix=f"{scored} scored")
    for w in workers:
        w.join()
    bar.close()
    if failed:
        log(f"  !! {failed} batch(es) failed — raw replies in {ERROR_LOG}; "
            "rerun with --retry-errors")
    return scored


def retry_errors(db):
    """Reset error messages to 'new' so the next run retries them."""
    return db.reset_errors()


def refilter(cfg, db, log=print):
    """Re-run the prefilter over every cached message.

    Catches postings the current rules now reject (e.g. a candidate's own CV
    that an older prefilter let through and the LLM already scored): they move
    to 'filtered_out' and their verdict is dropped. Messages that now pass but
    were previously 'filtered_out' move back to 'new' for the next scoring pass.
    Tracked postings (an application past 'new') keep their verdict.
    """
    pf = cfg["prefilter"]
    demoted = promoted = 0
    seen = set()
    for m in db.all_messages():
        if m.status == "duplicate" or m.text_hash in seen:
            continue
        seen.add(m.text_hash)
        passed, _ = prefilter.is_job_post(m.text, pf["min_length"], pf["my_keywords"])
        if not passed and m.status in ("new", "scored", "error"):
            db.set_status_by_hash(m.text_hash, "filtered_out")
            db.delete_verdict(m.text_hash)
            demoted += 1
        elif passed and m.status == "filtered_out":
            db.set_status(m.channel_id, m.msg_id, "new")
            promoted += 1
    log(f"  refilter: {demoted} now filtered out, {promoted} re-queued for scoring")
    return demoted, promoted
