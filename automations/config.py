"""Configuration via environment variables. No secrets are committed.

Copy .env.example to .env (or export these in your shell):
    ANTHROPIC_API_KEY, AUTOMATIONS_MODEL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    NEWS_FEEDS (comma-separated RSS URLs)
"""
from __future__ import annotations

import os

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("AUTOMATIONS_MODEL", "claude-sonnet-4-6")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_DEFAULT_FEEDS = "https://feeds.marketwatch.com/marketwatch/topstories/"
NEWS_FEEDS = [u.strip() for u in os.environ.get("NEWS_FEEDS", _DEFAULT_FEEDS).split(",") if u.strip()]
