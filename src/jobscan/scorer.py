"""Score prefiltered messages against the resume, in batches, with caching."""
from . import prefilter
from .db import text_hash
from .llm import extract_json_array

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


def score_pending(cfg, db, backend, resume, log=print):
    """Take status='new' messages, prefilter, batch-score, cache verdicts."""
    pf = cfg["prefilter"]
    lang = {"en": "English", "ru": "Russian"}.get(
        cfg["llm"]["output_language"], cfg["llm"]["output_language"])
    batch_size = int(cfg["llm"]["batch_size"])

    queue = []  # (channel_id, msg_id, hash, text)
    for m in db.pending_messages():
        h = m["text_hash"]
        if db.has_verdict(h):  # cross-post already scored earlier
            db.set_status(m["channel_id"], m["msg_id"], "scored")
            continue
        passed, reason = prefilter.is_job_post(m["text"], pf["min_length"], pf["my_keywords"])
        if not passed:
            db.set_status(m["channel_id"], m["msg_id"], "filtered_out")
            continue
        if any(q[2] == h for q in queue):  # dupe inside this run
            db.set_status(m["channel_id"], m["msg_id"], "duplicate")
            continue
        queue.append((m["channel_id"], m["msg_id"], h, m["text"]))

    log(f"  {len(queue)} new unique postings to score (backend: {backend.name})")
    scored = 0
    for i in range(0, len(queue), batch_size):
        batch = queue[i:i + batch_size]
        postings = "\n\n".join(
            f"--- posting id={j + 1} ---\n{prefilter.compact(item[3])}"
            for j, item in enumerate(batch))
        prompt = PROMPT.format(resume=resume, n=len(batch), lang=lang, postings=postings)
        try:
            verdicts = extract_json_array(backend.complete(prompt))
        except Exception as e:
            log(f"  !! batch failed, marking as error: {e}")
            for cid, mid, _, _ in batch:
                db.set_status(cid, mid, "error")
            continue
        by_id = {int(v.get("id", 0)): v for v in verdicts if isinstance(v, dict)}
        for j, (cid, mid, h, _) in enumerate(batch):
            v = by_id.get(j + 1)
            if v is None:
                db.set_status(cid, mid, "error")
                continue
            db.save_verdict(h, v, backend.name)
            db.set_status(cid, mid, "scored")
            scored += 1
        log(f"  scored {min(i + batch_size, len(queue))}/{len(queue)}")
    return scored


def retry_errors(db):
    """Reset error messages to 'new' so the next run retries them."""
    db.conn.execute("UPDATE messages SET status='new' WHERE status='error'")
    db.conn.commit()
