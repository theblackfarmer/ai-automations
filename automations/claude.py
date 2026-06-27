"""Thin, dependency-free client for the Anthropic (Claude) Messages API.

Uses only the standard library (urllib) so the toolkit installs with zero packages.
Raises ClaudeError with a clear message when the key is missing or the API errors,
so callers can degrade gracefully instead of crashing.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config

API_URL = "https://api.anthropic.com/v1/messages"


class ClaudeError(Exception):
    """Raised when the Claude call cannot be made or fails."""


def ask(prompt: str, system: str = "", model: str = "", max_tokens: int = 700, timeout: int = 60) -> str:
    """Send a single user message to Claude and return the text response."""
    if not config.ANTHROPIC_API_KEY:
        raise ClaudeError("ANTHROPIC_API_KEY is not set — add it to your environment to enable AI calls.")

    payload = {
        "model": model or config.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise ClaudeError(f"Anthropic API HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ClaudeError(f"Network error calling Anthropic: {e.reason}")

    try:
        return data["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise ClaudeError(f"Unexpected Anthropic response shape: {json.dumps(data)[:300]}")
