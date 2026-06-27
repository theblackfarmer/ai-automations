"""Daily Market Briefing — a concise pre-market note on NAS100 & Gold."""
from __future__ import annotations

from typing import List

from . import claude, news_monitor, notify

SYSTEM = (
    "You are a pre-market analyst writing for a trader of NAS100 and GOLD (XAUUSD). Using the "
    "headlines, write a concise pre-market briefing with four short sections: 1) Overnight tone, "
    "2) Key items for NAS100, 3) Key items for GOLD, 4) What to watch today. Keep it under 200 "
    "words, bullet style, and honest about uncertainty. Decision-support, not financial advice."
)


def build_prompt(headlines: List[str]) -> str:
    return "Today's headlines:\n" + "\n".join(f"- {h}" for h in headlines)


def run() -> str:
    """Build and deliver the pre-market briefing. Returns the message."""
    headlines = news_monitor.fetch_headlines(limit=40)
    if not headlines:
        msg = "No headlines could be fetched for the briefing."
        notify.deliver(msg)
        return msg
    briefing = claude.ask(build_prompt(headlines), system=SYSTEM, max_tokens=600)
    msg = "🗞️ Pre-Market Briefing — NAS100 & Gold\n\n" + briefing
    notify.deliver(msg)
    return msg
