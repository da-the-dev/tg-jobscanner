"""Pluggable LLM backends: Claude Code subscription (headless CLI), or any
OpenAI-compatible endpoint (OpenRouter, a self-hosted Ollama/vLLM/LM Studio,
or anything else that speaks the same API)."""

import json
import os
import subprocess
import tempfile

import openai


class ClaudeCodeBackend:
    """Uses your Claude Code subscription via `claude -p` — no API key needed."""

    def __init__(self, cfg):
        cc = cfg["llm"]["claude_code"]
        self.command = cc.get("command", "claude")
        self.model = cc.get("model") or None
        # Force subscription auth: a stray ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
        # in the environment makes the CLI try API-key auth and 401 on a
        # subscription plan. Strip them from the child env unless disabled.
        self.use_subscription = cc.get("use_subscription", True)
        self.name = f"claude-code:{self.model or 'default'}"

    def _env(self):
        env = os.environ.copy()
        if self.use_subscription:
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
        return env

    def complete(self, prompt, timeout=300):
        # --strict-mcp-config with no --mcp-config => load ZERO MCP servers.
        # Running from a neutral temp cwd => the CLI won't pick up this project's
        # CLAUDE.md / settings / .mcp.json. Both keep the call fast, single-turn
        # and hermetic (no tool prompts that can stall a headless run).
        args = [self.command, "-p", "--output-format", "json", "--strict-mcp-config"]
        if self.model:
            args += ["--model", self.model]
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self._env(),
            cwd=tempfile.gettempdir(),
        )
        # The CLI reports API/auth/limit errors as is_error in stdout JSON while
        # STILL exiting non-zero, leaving stderr empty. So parse stdout first and
        # surface its message before falling back to stderr.
        wrapper = None
        if proc.stdout.strip():
            try:
                wrapper = json.loads(proc.stdout)
            except json.JSONDecodeError:
                pass
        if wrapper is not None and wrapper.get("is_error"):
            raise RuntimeError(
                f"claude CLI error: {wrapper.get('result', '') or 'unknown'}"
            )
        if proc.returncode != 0:
            msg = (
                proc.stderr.strip()
                or proc.stdout.strip()
                or f"exited {proc.returncode} with no output"
            )
            raise RuntimeError(f"claude CLI failed: {msg[:800]}")
        if wrapper is None:
            raise RuntimeError(
                f"claude CLI: could not parse output: {proc.stdout[:300]!r}"
            )
        return wrapper["result"]


class OpenAIBackend:
    """Any OpenAI-compatible /responses endpoint.

    OpenRouter (the recommended default: hosted, pay-per-token, every model
    worth using) is just one instance of this — a self-hosted Ollama, vLLM,
    or LM Studio server speaks the same API. Only OpenRouter needs a real API
    key and gets the OpenRouter-only tuning (no-reasoning, throughput
    routing); anything else is called plain.
    """

    def __init__(self, cfg):
        oc = cfg["llm"]["openai"]
        self.host = oc.get("host", "https://openrouter.ai/api/v1").rstrip("/")
        self.model = oc["model"]
        self.name = f"openai:{self.model}"
        self.is_openrouter = "openrouter.ai" in self.host
        key_env = oc.get("api_key_env", "OPENROUTER_API_KEY")
        self.api_key = os.environ.get(key_env, "")
        if self.is_openrouter and not self.api_key:
            raise ValueError(f"No {key_env} specified in the env (required for OpenRouter)")
        self.api_key = self.api_key or "not-needed"  # most self-hosted endpoints don't check

    def complete(self, prompt, timeout=600):
        extra = {}
        if self.is_openrouter:
            extra["reasoning"] = {"effort": "none"}
            extra["extra_body"] = {"provider": {"sort": "throughput"}}
        with openai.Client(
            api_key=self.api_key, base_url=self.host, timeout=timeout
        ) as client:
            resp = client.responses.create(
                model=self.model,
                stream=False,
                input=[{"role": "user", "content": prompt}],
                temperature=0.2,
                **extra,
            )
        return resp.output_text


def get_backend(cfg):
    backend = cfg["llm"]["backend"]
    if backend == "claude-code":
        return ClaudeCodeBackend(cfg)
    if backend == "openai":
        return OpenAIBackend(cfg)
    raise ValueError(f"Unknown llm.backend: {backend} (use 'claude-code' or 'openai')")


def extract_json_array(text):
    """Pull the first JSON array out of an LLM reply (tolerates prose/fences)."""
    start = text.find("[")
    if start == -1:
        raise ValueError("no JSON array in LLM reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unterminated JSON array in LLM reply")
