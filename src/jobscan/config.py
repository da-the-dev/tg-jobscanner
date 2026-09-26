"""Load and validate config.yaml."""
import os
import re
import sys

import yaml

DEFAULTS = {
    "fetch_mode": "web",  # "web" (public channels, no credentials) or "mtproto"
    "backfill": {"days": 30, "max_messages": 100},
    "prefilter": {"min_length": 80, "my_keywords": [],
                 "reject_seniority": ["middle", "junior"], "reject_russia_only": True},
    "llm": {
        "backend": "openrouter",
        "batch_size": 5,
        "concurrency": 4,
        "output_language": "en",
        "claude_code": {"command": "claude", "model": "", "use_subscription": True},
        "ollama": {"host": "http://localhost:11434", "model": "qwen2.5:14b"},
        "openrouter": {"host": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat"},
    },
    "scoring": {"min_score": 40},
    "links": {"enabled": True, "fetch_timeout": 10, "max_fetches_per_run": 60,
             "skip_fetch_min_chars": 400, "recruiter_bonus": 8},
    "serve": {"host": "127.0.0.1", "port": 8765},  # `jobscan serve` (status write-back)
    "resume_path": "resume.md",
    "db_path": "jobscan.db",
    "dashboard_path": "dashboard.html",
}


def _merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path="config.yaml"):
    if not os.path.exists(path):
        sys.exit(f"Config not found: {path}. Copy config.example.yaml to config.yaml first.")
        
    with open(path, encoding="utf-8") as f:
        f_str = f.read()
            
    for key in re.findall(r'\${([A-Za-z_][A-Za-z0-9_]*?)}', f_str):
        if var := os.getenv(key):
            f_str = f_str.replace(f'${{{key}}}', var)
        else:
            sys.exit(f'{key} is not defined or blank and is required')
    
    cfg = _merge(DEFAULTS, yaml.safe_load(f_str) or {})

    if cfg["fetch_mode"] == "mtproto":
        tg = cfg.get("telegram") or {}
        if not tg.get("api_id") or not tg.get("api_hash"):
            sys.exit("fetch_mode is 'mtproto' but telegram.api_id / api_hash are missing "
                     "(get them at https://my.telegram.org, or set fetch_mode: web)")
    if not cfg.get("channels"):
        sys.exit("No channels listed in config.yaml")
    return cfg


# Phrases that only appear in the shipped resume.md template. Scoring against
# an unedited template makes the LLM refuse in prose on most batches, which
# surfaces far downstream as "no JSON array in LLM reply" — so catch it here.
_TEMPLATE_MARKERS = (
    "paste your full resume here",
    "the scoring quality is directly proportional",
)


def load_resume(cfg):
    path = cfg["resume_path"]
    if not os.path.exists(path):
        sys.exit(f"Resume not found: {path}")
    text = open(path, encoding="utf-8").read().strip()
    if len(text) < 100:
        sys.exit(f"{path} looks empty — paste your actual resume into it.")
    low = text.lower()
    if any(m in low for m in _TEMPLATE_MARKERS):
        sys.exit(f"{path} is still the unedited template — replace the placeholder "
                 "text with your actual resume. The LLM refuses to score postings "
                 "against a placeholder, which fails every batch.")
    return text
