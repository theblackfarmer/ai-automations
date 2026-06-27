"""Trading News Monitor — scan market headlines, flag what moves NAS100 & Gold."""
from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import List

from . import claude, config, notify

SYSTEM = (
    "You are a markets analyst. From the headlines, identify ONLY items likely to move "
    "NAS100 (US tech / Nasdaq) or GOLD (XAUUSD). For each relevant item give one line: "
    "[NAS100 or GOLD] | direction (up/down/volatile) | impact (HIGH/MED/LOW) | one-line why. "
    "Ignore everything irrelevant. If nothing is relevant, reply exactly: "
    "'No high-impact NAS100/Gold news right now.' Be concise and honest — this is "
    "decision-support, not financial advice."
)


def parse_rss(xml_text: str) -> List[str]:
    """Extract <item><title> values from RSS XML. Returns [] on malformed input."""
    titles: List[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return titles
    for item in root.iter("item"):
        title = item.find("title")
        if title is not None and title.text:
            titles.append(title.text.strip())
    return titles


def fetch_headlines(feeds: List[str] = None, limit: int = 30) -> List[str]:
    """Fetch and de-duplicate the latest headlines across one or more RSS feeds."""
    feeds = feeds or config.NEWS_FEEDS
    collected: List[str] = []
    for url in feeds:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                collected.extend(parse_rss(resp.read().decode("utf-8", "ignore")))
        except (urllib.error.URLError, urllib.error.HTTPError):
            continue
    seen, unique = set(), []
    for title in collected:
        if title not in seen:
            seen.add(title)
            unique.append(title)
    return unique[:limit]


def build_prompt(headlines: List[str]) -> str:
    return "Headlines:\n" + "\n".join(f"- {h}" for h in headlines)


def run() -> str:
    """Fetch news, analyze with Claude, deliver an alert. Returns the message."""
    headlines = fetch_headlines()
    if not headlines:
        msg = "No headlines could be fetched (check NEWS_FEEDS / connection)."
        notify.deliver(msg)
        return msg
    analysis = claude.ask(build_prompt(headlines), system=SYSTEM, max_tokens=700)
    msg = "📊 NAS100 / GOLD news scan\n\n" + analysis
    notify.deliver(msg)
    return msg
