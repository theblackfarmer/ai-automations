"""Trading Signal Bot — an honest breakout signal for NAS100 / Gold.

PAPER / DECISION-SUPPORT ONLY. This computes a transparent breakout setup and (optionally)
sends it as an alert. It does NOT place trades. The rule is simple and stated plainly so the
output can be trusted and checked — no black box, no hopium.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import notify


def compute_signal(candles: List[Dict], lookback: int = 20, rr: float = 2.0, buffer: float = 0.0) -> Optional[Dict]:
    """Breakout rule on OHLC candles (most recent candle last).

    - LONG  if the last close breaks above the prior `lookback` highs.
    - SHORT if the last close breaks below the prior `lookback` lows.
    - else NONE (inside the range).
    Stop goes at the opposite extreme of the range; target is `rr` x risk.
    Returns None if there isn't enough data.
    """
    if not candles or len(candles) < lookback + 1:
        return None
    window = candles[-(lookback + 1):-1]
    last = candles[-1]
    hi = max(c["high"] for c in window)
    lo = min(c["low"] for c in window)
    close = last["close"]

    if close > hi:
        entry, stop = close, lo - buffer
        risk = entry - stop
        if risk <= 0:
            return {"signal": "NONE", "reason": "Invalid risk (stop above entry)."}
        return {"signal": "LONG", "entry": entry, "stop": stop, "target": entry + rr * risk,
                "reason": f"Close {close} broke the {lookback}-bar high {hi}."}
    if close < lo:
        entry, stop = close, hi + buffer
        risk = stop - entry
        if risk <= 0:
            return {"signal": "NONE", "reason": "Invalid risk (stop below entry)."}
        return {"signal": "SHORT", "entry": entry, "stop": stop, "target": entry - rr * risk,
                "reason": f"Close {close} broke the {lookback}-bar low {lo}."}
    return {"signal": "NONE", "reason": f"Inside the {lookback}-bar range ({lo}-{hi})."}


def format_signal(symbol: str, sig: Optional[Dict]) -> str:
    if not sig:
        return f"⚪ {symbol}: not enough data for a signal."
    if sig["signal"] == "NONE":
        return f"⚪ {symbol}: no breakout setup — {sig['reason']}"
    icon = "🟢" if sig["signal"] == "LONG" else "🔴"
    return (
        f"{icon} {symbol} {sig['signal']}  (paper / decision-support)\n"
        f"Entry {round(sig['entry'], 2)} · Stop {round(sig['stop'], 2)} · Target {round(sig['target'], 2)}\n"
        f"{sig['reason']}"
    )


# A small sample series so `signal --demo` runs with no API/keys (proves the logic).
SAMPLE_CANDLES: List[Dict] = (
    [{"high": 100 + i * 0.2, "low": 99 + i * 0.2, "close": 99.5 + i * 0.2} for i in range(20)]
    + [{"high": 108, "low": 106, "close": 107.5}]  # breakout candle above the range
)


def run(symbol: str = "NAS100", candles: List[Dict] = None) -> str:
    """Compute a signal and deliver it. Uses sample data if none provided."""
    sig = compute_signal(candles if candles is not None else SAMPLE_CANDLES)
    msg = format_signal(symbol, sig)
    notify.deliver(msg)
    return msg
