"""Load and validate config.yaml."""
import os
import sys

import yaml

DEFAULTS = {
    "fetch_mode": "web",  # "web" (public channels, no credentials) or "mtproto"
    "backfill": {"days": 30, "max_messages": 100},
    "prefilter": {"min_length": 80, "my_keywords": []},
    "llm": {
        "backend": "claude-code",
        "batch_size": 5,
        "concurrency": 4,
        "output_language": "en",
        "claude_code": {"command": "claude", "model": "", "use_subscription": True},
        "ollama": {"host": "http://localhost:11434", "model": "qwen2.5:14b"},
    },
    "scoring": {"min_score": 40},
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
        cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    if cfg["fetch_mode"] == "mtproto":
        tg = cfg.get("telegram") or {}
        if not tg.get("api_id") or not tg.get("api_hash"):
            sys.exit("fetch_mode is 'mtproto' but telegram.api_id / api_hash are missing "
                     "(get them at https://my.telegram.org, or set fetch_mode: web)")
    if not cfg.get("channels"):
        sys.exit("No channels listed in config.yaml")
    return cfg


def load_resume(cfg):
    path = cfg["resume_path"]
    if not os.path.exists(path):
        sys.exit(f"Resume not found: {path}")
    text = open(path, encoding="utf-8").read().strip()
    if len(text) < 100:
        sys.exit(f"{path} looks empty — paste your actual resume into it.")
    return text
