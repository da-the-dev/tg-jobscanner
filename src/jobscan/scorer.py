"""Score prefiltered messages against the resume, in batches, with caching."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import prefilter
from .llm import extract_json_array
from .progress import Progress

PROMPT = """You are screening Telegram job postings (Russian or English) against a candidate's resume.

CANDIDATE RESUME:
<resume>
{resume}
</resume>

Below are {n} postings. For EACH one return a JSON object with exactly these keys:
  "id": the posting id (integer, as given),
  "relevant": true only if it is a real job posting that plausibly fits this candidate's profile and seniority; false for ads, courses, digests, off-profile roles,
  "score": 0-100 overall fit (skills match, seniority, remote/location, salary attractiveness),
  "title": job title, "company": company or "" if unknown,
  "salary": salary/fork as stated or "", "location": e.g. "remote", "hybrid Moscow", "onsite Berlin" or "",
  "reasons_apply": 1-4 short bullets why the candidate should apply,
  "reasons_skip": 1-4 short bullets why they might skip it,
  "strengths": 1-4 candidate strengths for THIS job, "weaknesses": 1-4 gaps for THIS job.
If relevant is false: still set id/relevant/score(low)/title, leave other fields as "" or [].
Write all analysis text in {lang}. Respond with ONLY a JSON array of {n} objects, no other text.

POSTINGS:
{postings}"""


def _build_queue(cfg, db):
    """Prefilter + dedupe status='new' messages into a scoring queue."""
    pf = cfg["prefilter"]
    queue = []  # (channel_id, msg_id, hash, text)
    seen = set()
    for m in db.pending_messages():
        h = m["text_hash"]
        if db.has_verdict(h):  # cross-post already scored earlier
            db.set_status(m["channel_id"], m["msg_id"], "scored")
            continue
        passed, _ = prefilter.is_job_post(m["text"], pf["min_length"], pf["my_keywords"])
        if not passed:
            db.set_status(m["channel_id"], m["msg_id"], "filtered_out")
            continue
        if h in seen:  # dupe inside this run
            db.set_status(m["channel_id"], m["msg_id"], "duplicate")
            continue
        seen.add(h)
        queue.append((m["channel_id"], m["msg_id"], h, m["text"]))
    return queue


def _run_batch(backend, prompt):
    """Runs in a worker thread. Returns parsed verdicts or raises."""
    return extract_json_array(backend.complete(prompt))


def score_pending(cfg, db, backend, resume, log=print):
    """Take status='new' messages, prefilter, batch-score in parallel, cache."""
    lang = {"en": "English", "ru": "Russian"}.get(
        cfg["llm"]["output_language"], cfg["llm"]["output_language"])
    batch_size = int(cfg["llm"]["batch_size"])
    concurrency = max(1, int(cfg["llm"].get("concurrency", 4)))

    queue = _build_queue(cfg, db)
    if not queue:
        log(f"  0 new unique postings to score (backend: {backend.name})")
        return 0

    batches = [queue[i:i + batch_size] for i in range(0, len(queue), batch_size)]
    log(f"  {len(queue)} postings in {len(batches)} batches "
        f"× {concurrency} workers (backend: {backend.name})")

    # Build prompts up front (pure CPU), then farm the LLM calls to threads.
    jobs = []
    for batch in batches:
        postings = "\n\n".join(
            f"--- posting id={j + 1} ---\n{prefilter.compact(item[3])}"
            for j, item in enumerate(batch))
        jobs.append((batch, PROMPT.format(resume=resume, n=len(batch),
                                          lang=lang, postings=postings)))

    scored = failed = 0
    bar = Progress(len(jobs), label="  scoring")
    # DB writes happen ONLY here on the main thread as futures complete.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_run_batch, backend, prompt): batch
                   for batch, prompt in jobs}
        for fut in as_completed(futures):
            batch = futures[fut]
            try:
                verdicts = fut.result()
                by_id = {int(v.get("id", 0)): v for v in verdicts
                         if isinstance(v, dict)}
            except Exception as e:
                failed += 1
                for cid, mid, _, _ in batch:
                    db.set_status(cid, mid, "error")
                bar.advance(suffix=f"⚠ {str(e)[:40]}")
                continue
            for j, (cid, mid, h, _) in enumerate(batch):
                v = by_id.get(j + 1)
                if v is None:
                    db.set_status(cid, mid, "error")
                    continue
                db.save_verdict(h, v, backend.name)
                db.set_status(cid, mid, "scored")
                scored += 1
            bar.advance(suffix=f"{scored} scored")
    bar.close()
    if failed:
        log(f"  !! {failed} batch(es) failed — rerun with --retry-errors")
    return scored


def retry_errors(db):
    """Reset error messages to 'new' so the next run retries them."""
    db.conn.execute("UPDATE messages SET status='new' WHERE status='error'")
    db.conn.commit()
