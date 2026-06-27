"""Delivery: send to Telegram if configured, otherwise print to the console.

Never raises on a delivery failure — automations should still complete and log.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from . import config


def send_telegram(text: str) -> bool:
    """Return True if sent via Telegram, False if not configured or it failed."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": config.TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError):
        return False


def deliver(text: str) -> str:
    """Send via Telegram if possible, else print. Returns the channel used."""
    if send_telegram(text):
        return "telegram"
    print(text)
    return "console"
