"""Tests for the pure logic — runs with `python tests/test_logic.py` or pytest.

These cover everything that doesn't need network or API keys: RSS parsing, prompt
building, and the trading-signal rule. The AI calls are thin wrappers tested by hand.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automations import news_monitor, daily_briefing, lead_response, trading_signal

SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Fed signals possible rate cut as inflation cools</title></item>
  <item><title>Nvidia earnings beat lifts Nasdaq futures</title></item>
  <item><title>Local bake sale raises $300</title></item>
</channel></rss>"""


def test_parse_rss_extracts_titles():
    titles = news_monitor.parse_rss(SAMPLE_RSS)
    assert len(titles) == 3
    assert "Nvidia earnings beat lifts Nasdaq futures" in titles


def test_parse_rss_bad_xml_returns_empty():
    assert news_monitor.parse_rss("not xml <<<") == []


def test_news_prompt_includes_headlines():
    p = news_monitor.build_prompt(["A", "B"])
    assert "- A" in p and "- B" in p


def test_briefing_prompt_includes_headlines():
    p = daily_briefing.build_prompt(["CPI report due"])
    assert "CPI report due" in p


def test_lead_prompt_has_fields():
    p = lead_response.build_prompt("Jane", "j@x.com", "Plans?")
    assert "Jane" in p and "j@x.com" in p and "Plans?" in p


def test_signal_long_breakout():
    sig = trading_signal.compute_signal(trading_signal.SAMPLE_CANDLES)
    assert sig["signal"] == "LONG"
    assert sig["target"] > sig["entry"] > sig["stop"]


def test_signal_short_breakout():
    candles = ([{"high": 100, "low": 99, "close": 99.5}] * 20) + [{"high": 98, "low": 96, "close": 96.5}]
    sig = trading_signal.compute_signal(candles)
    assert sig["signal"] == "SHORT"
    assert sig["target"] < sig["entry"] < sig["stop"]


def test_signal_inside_range_is_none():
    candles = ([{"high": 100, "low": 95, "close": 97} for _ in range(20)]) + [{"high": 99, "low": 96, "close": 97.5}]
    sig = trading_signal.compute_signal(candles)
    assert sig["signal"] == "NONE"


def test_signal_insufficient_data():
    assert trading_signal.compute_signal([{"high": 1, "low": 1, "close": 1}]) is None


def test_format_signal_long_has_levels():
    sig = trading_signal.compute_signal(trading_signal.SAMPLE_CANDLES)
    out = trading_signal.format_signal("NAS100", sig)
    assert "NAS100 LONG" in out and "Entry" in out and "Stop" in out and "Target" in out


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
