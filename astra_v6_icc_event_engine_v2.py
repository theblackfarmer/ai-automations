#!/usr/bin/env python3
"""Astra 6 ICC V2 — source-grounded research event engine.

Frozen semantic contract
------------------------
LONG:
    1H causal swing HIGH is broken -> indication level/time.
    15M counter-direction correction follows.
    15M correction structure develops and turns back toward bullish direction:
        correction low -> reaction high -> HIGHER LOW.
    5M or 15M price subsequently returns through the ORIGINAL 1H indication
    level -> continuation.

SHORT is mirrored:
    1H causal swing LOW is broken -> indication level/time.
    15M counter-direction correction follows.
    correction high -> reaction low -> LOWER HIGH.
    5M or 15M price subsequently returns below the ORIGINAL indication level.

This is a research substrate only. It contains no entry/stop/target/backtest logic.

Important: the 2-left/2-right pivot rule is an explicit ASTRA structural
substrate, not claimed to be an exact rule specified by the educator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Pivot:
    idx: int
    time: pd.Timestamp
    price: float
    kind: str
    known_time: pd.Timestamp


@dataclass(frozen=True)
class Indication:
    direction: str
    level: float
    indication_idx: int
    indication_time: pd.Timestamp
    source_pivot_idx: int
    source_pivot_time: pd.Timestamp
    source_pivot_known_time: pd.Timestamp


@dataclass(frozen=True)
class ICCEvent:
    direction: str
    indication_level: float
    indication_time: pd.Timestamp
    correction_start_time: pd.Timestamp
    correction_structure_known_time: pd.Timestamp
    continuation_time: pd.Timestamp
    continuation_price: float
    correction_low_time: Optional[pd.Timestamp]
    correction_high_time: Optional[pd.Timestamp]
    transition_pivot_time: Optional[pd.Timestamp]

    @property
    def causal_ok(self) -> bool:
        return (
            self.indication_time
            < self.correction_start_time
            < self.correction_structure_known_time
            < self.continuation_time
        )


def _validate_bars(df: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out = out.sort_values("timestamp").reset_index(drop=True)

    if out["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps are not allowed")

    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return out


def causal_pivots(
    bars: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
) -> tuple[list[Pivot], list[Pivot]]:
    """Return pivots with known_time equal to the first bar after right confirmation bars."""
    if left < 1 or right < 1:
        raise ValueError("left/right must be >= 1")

    df = _validate_bars(bars)
    highs: list[Pivot] = []
    lows: list[Pivot] = []

    for known_idx in range(left + right, len(df)):
        pivot_idx = known_idx - right
        if pivot_idx - left < 0:
            continue

        ph = float(df.at[pivot_idx, "high"])
        pl = float(df.at[pivot_idx, "low"])
        left_highs = df.loc[pivot_idx - left:pivot_idx - 1, "high"]
        right_highs = df.loc[pivot_idx + 1:pivot_idx + right, "high"]
        left_lows = df.loc[pivot_idx - left:pivot_idx - 1, "low"]
        right_lows = df.loc[pivot_idx + 1:pivot_idx + right, "low"]

        if ph > float(left_highs.max()) and ph >= float(right_highs.max()):
            highs.append(
                Pivot(
                    pivot_idx,
                    df.at[pivot_idx, "timestamp"],
                    ph,
                    "high",
                    df.at[known_idx, "timestamp"],
                )
            )

        if pl < float(left_lows.min()) and pl <= float(right_lows.min()):
            lows.append(
                Pivot(
                    pivot_idx,
                    df.at[pivot_idx, "timestamp"],
                    pl,
                    "low",
                    df.at[known_idx, "timestamp"],
                )
            )

    return highs, lows


def _first_break_after(
    bars: pd.DataFrame,
    start_idx: int,
    *,
    direction: str,
    level: float,
) -> Optional[int]:
    for i in range(start_idx, len(bars)):
        close = float(bars.at[i, "close"])
        if direction == "long" and close > level:
            return i
        if direction == "short" and close < level:
            return i
    return None


def build_indications(
    htf_1h: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
) -> list[Indication]:
    """Build indications only from causally known 1H swing levels."""
    df = _validate_bars(htf_1h)
    highs, lows = causal_pivots(df, left=left, right=right)

    indications: list[Indication] = []

    # A bullish indication is a close above a causally known swing high.
    for pivot in highs:
        start = max(
            pivot.idx + 1,
            int(df["timestamp"].searchsorted(pivot.known_time, side="left")),
        )
        idx = _first_break_after(df, start, direction="long", level=pivot.price)
        if idx is None:
            continue
        indications.append(
            Indication(
                "long",
                pivot.price,
                idx,
                df.at[idx, "timestamp"],
                pivot.idx,
                pivot.time,
                pivot.known_time,
            )
        )

    # A bearish indication is a close below a causally known swing low.
    for pivot in lows:
        start = max(
            pivot.idx + 1,
            int(df["timestamp"].searchsorted(pivot.known_time, side="left")),
        )
        idx = _first_break_after(df, start, direction="short", level=pivot.price)
        if idx is None:
            continue
        indications.append(
            Indication(
                "short",
                pivot.price,
                idx,
                df.at[idx, "timestamp"],
                pivot.idx,
                pivot.time,
                pivot.known_time,
            )
        )

    return sorted(indications, key=lambda x: x.indication_time)


def _time_index(df: pd.DataFrame) -> dict[pd.Timestamp, int]:
    return {pd.Timestamp(t): i for i, t in enumerate(df["timestamp"])}


def _bar_idx_at_or_after(df: pd.DataFrame, t: pd.Timestamp) -> int:
    values = df["timestamp"].to_numpy()
    return int(np.searchsorted(values, np.datetime64(t), side="left"))


def _correction_structure(
    corr: pd.DataFrame,
    *,
    direction: str,
    pivots_left: int,
    pivots_right: int,
) -> Optional[tuple[Pivot, Pivot, Pivot]]:
    """Find a causal 15M structure transition.

    LONG research interpretation:
        low -> reaction high -> higher low.

    SHORT research interpretation:
        high -> reaction low -> lower high.

    The returned final pivot's known_time is the earliest time the transition
    can be known. No future bar after that known_time is used.
    """
    highs, lows = causal_pivots(corr, left=pivots_left, right=pivots_right)

    if direction == "long":
        for i, low1 in enumerate(lows):
            highs_after = [h for h in highs if h.idx > low1.idx]
            if not highs_after:
                continue
            high1 = highs_after[0]
            lows_after = [l for l in lows if l.idx > high1.idx and l.price > low1.price]
            if lows_after:
                low2 = lows_after[0]
                return low1, high1, low2

    else:
        for i, high1 in enumerate(highs):
            lows_after = [l for l in lows if l.idx > high1.idx]
            if not lows_after:
                continue
            low1 = lows_after[0]
            highs_after = [h for h in highs if h.idx > low1.idx and h.price < high1.price]
            if highs_after:
                high2 = highs_after[0]
                return high1, low1, high2

    return None


def build_icc_events(
    htf_1h: pd.DataFrame,
    corr_15m: pd.DataFrame,
    confirm_5m: pd.DataFrame,
    *,
    pivot_left: int = 2,
    pivot_right: int = 2,
    max_correction_bars: int = 96,
    max_continuation_bars: int = 192,
) -> pd.DataFrame:
    """Build V2 ICC events without execution or performance logic."""
    htf = _validate_bars(htf_1h)
    corr = _validate_bars(corr_15m)
    conf = _validate_bars(confirm_5m)

    indications = build_indications(
        htf, left=pivot_left, right=pivot_right
    )

    rows: list[dict] = []

    for ind in indications:
        corr_start = _bar_idx_at_or_after(corr, ind.indication_time)
        if corr_start >= len(corr):
            continue

        corr_end = min(len(corr), corr_start + max_correction_bars)
        corr_slice = corr.iloc[corr_start:corr_end].reset_index(drop=True)
        if len(corr_slice) < (pivot_left + pivot_right + 1):
            continue

        structure = _correction_structure(
            corr_slice,
            direction=ind.direction,
            pivots_left=pivot_left,
            pivots_right=pivot_right,
        )
        if structure is None:
            continue

        first_pivot, reaction_pivot, transition_pivot = structure
        structure_known = transition_pivot.known_time

        # Correction starts at the first counter-direction movement after I.
        correction_start = None
        for i in range(corr_start, corr_end):
            if ind.direction == "long":
                if float(corr.at[i, "low"]) < float(corr.at[i, "open"]):
                    correction_start = corr.at[i, "timestamp"]
                    break
            else:
                if float(corr.at[i, "high"]) > float(corr.at[i, "open"]):
                    correction_start = corr.at[i, "timestamp"]
                    break

        if correction_start is None or correction_start >= structure_known:
            continue

        conf_start = _bar_idx_at_or_after(conf, structure_known)
        conf_end = min(len(conf), conf_start + max_continuation_bars)

        continuation_idx = None
        for i in range(conf_start, conf_end):
            # Strictly after the structural transition's known time.
            if conf.at[i, "timestamp"] <= structure_known:
                continue
            close = float(conf.at[i, "close"])
            if ind.direction == "long" and close > ind.level:
                continuation_idx = i
                break
            if ind.direction == "short" and close < ind.level:
                continuation_idx = i
                break

        if continuation_idx is None:
            continue

        rows.append(
            {
                "direction": ind.direction,
                "indication_level": ind.level,
                "indication_time": ind.indication_time,
                "source_pivot_time": ind.source_pivot_time,
                "source_pivot_known_time": ind.source_pivot_known_time,
                "correction_start_time": correction_start,
                "correction_structure_known_time": structure_known,
                "correction_first_pivot_time": first_pivot.time,
                "correction_reaction_pivot_time": reaction_pivot.time,
                "correction_transition_pivot_time": transition_pivot.time,
                "continuation_time": conf.at[continuation_idx, "timestamp"],
                "continuation_price": float(conf.at[continuation_idx, "close"]),
                "causal_ok": True,
                "continuation_crosses_original_indication": True,
            }
        )

    out = pd.DataFrame(rows)

    if out.empty:
        return pd.DataFrame(
            columns=[
                "direction",
                "indication_level",
                "indication_time",
                "source_pivot_time",
                "source_pivot_known_time",
                "correction_start_time",
                "correction_structure_known_time",
                "correction_first_pivot_time",
                "correction_reaction_pivot_time",
                "correction_transition_pivot_time",
                "continuation_time",
                "continuation_price",
                "causal_ok",
                "continuation_crosses_original_indication",
            ]
        )

    out["causal_ok"] = (
        (out["indication_time"] < out["correction_start_time"])
        & (out["correction_start_time"] < out["correction_structure_known_time"])
        & (out["correction_structure_known_time"] < out["continuation_time"])
    )

    out["continuation_crosses_original_indication"] = np.where(
        out["direction"].eq("long"),
        out["continuation_price"] > out["indication_level"],
        out["continuation_price"] < out["indication_level"],
    )

    return out.reset_index(drop=True)


def audit_invariants(events: pd.DataFrame) -> dict[str, int]:
    """Return counts for the two V2 hard invariants."""
    if events.empty:
        return {
            "events": 0,
            "causal_violations": 0,
            "original_level_violations": 0,
            "same_bar_correction_structure": 0,
            "same_bar_correction_continuation": 0,
        }

    causal = ~(
        (events["indication_time"] < events["correction_start_time"])
        & (events["correction_start_time"] < events["correction_structure_known_time"])
        & (events["correction_structure_known_time"] < events["continuation_time"])
    )

    level = ~events["continuation_crosses_original_indication"].astype(bool)

    return {
        "events": int(len(events)),
        "causal_violations": int(causal.sum()),
        "original_level_violations": int(level.sum()),
        "same_bar_correction_structure": int(
            (events["correction_start_time"] == events["correction_structure_known_time"]).sum()
        ),
        "same_bar_correction_continuation": int(
            (events["correction_structure_known_time"] == events["continuation_time"]).sum()
        ),
    }


def main() -> None:
    raise SystemExit(
        "V2 is a library/research substrate. Supply 1H/15M/5M bars to "
        "build_icc_events(); no dataset download or backtest is performed."
    )


if __name__ == "__main__":
    main()
