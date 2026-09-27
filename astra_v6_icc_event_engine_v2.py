#!/usr/bin/env python3
"""Astra 6 ICC V2 — causal structural event engine.

Frozen structural contract:
I -> correction begins -> post-correction structure forms and becomes known -> K.

A correction structure may not reuse a pivot whose structural evidence began
before correction start. Every pivot used by the correction must have its full
left/right confirmation window downstream of C. K must occur strictly after
the final transition pivot is causally known and must cross the ORIGINAL I
level. This module contains no execution/backtest logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
    return out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def causal_pivots(
    bars: pd.DataFrame, *, left: int = 2, right: int = 2
) -> tuple[list[Pivot], list[Pivot]]:
    """Return pivots with known_time equal to the right-confirmation bar."""
    if left < 1 or right < 1:
        raise ValueError("left/right must be >= 1")
    df = _validate_bars(bars)
    highs, lows = [], []
    for known_idx in range(left + right, len(df)):
        pivot_idx = known_idx - right
        ph, pl = float(df.at[pivot_idx, "high"]), float(df.at[pivot_idx, "low"])
        lh = df.loc[pivot_idx-left:pivot_idx-1, "high"]
        rh = df.loc[pivot_idx+1:pivot_idx+right, "high"]
        ll = df.loc[pivot_idx-left:pivot_idx-1, "low"]
        rl = df.loc[pivot_idx+1:pivot_idx+right, "low"]
        if ph > float(lh.max()) and ph >= float(rh.max()):
            highs.append(Pivot(pivot_idx, df.at[pivot_idx, "timestamp"], ph, "high",
                               df.at[known_idx, "timestamp"]))
        if pl < float(ll.min()) and pl <= float(rl.min()):
            lows.append(Pivot(pivot_idx, df.at[pivot_idx, "timestamp"], pl, "low",
                              df.at[known_idx, "timestamp"]))
    return highs, lows


def _first_break_after(
    bars: pd.DataFrame, start_idx: int, *, direction: str, level: float
) -> Optional[int]:
    for i in range(start_idx, len(bars)):
        close = float(bars.at[i, "close"])
        if direction == "long" and close > level:
            return i
        if direction == "short" and close < level:
            return i
    return None


def build_indications(htf_1h: pd.DataFrame, *, left: int = 2, right: int = 2) -> list[Indication]:
    df = _validate_bars(htf_1h)
    highs, lows = causal_pivots(df, left=left, right=right)
    indications = []
    for pivot in highs:
        start = max(pivot.idx + 1, int(df["timestamp"].searchsorted(pivot.known_time, side="left")))
        idx = _first_break_after(df, start, direction="long", level=pivot.price)
        if idx is not None:
            indications.append(Indication("long", pivot.price, idx, df.at[idx, "timestamp"],
                                           pivot.idx, pivot.time, pivot.known_time))
    for pivot in lows:
        start = max(pivot.idx + 1, int(df["timestamp"].searchsorted(pivot.known_time, side="left")))
        idx = _first_break_after(df, start, direction="short", level=pivot.price)
        if idx is not None:
            indications.append(Indication("short", pivot.price, idx, df.at[idx, "timestamp"],
                                           pivot.idx, pivot.time, pivot.known_time))
    return sorted(indications, key=lambda x: x.indication_time)


def _bar_idx_at_or_after(df: pd.DataFrame, t: pd.Timestamp) -> int:
    return int(np.searchsorted(df["timestamp"].to_numpy(), np.datetime64(t), side="left"))


def _correction_structure(
    corr: pd.DataFrame,
    *,
    direction: str,
    correction_start_time: pd.Timestamp,
    pivots_left: int,
    pivots_right: int,
) -> Optional[tuple[Pivot, Pivot, Pivot]]:
    """Find a structure made entirely from evidence downstream of C.

    Critical V2 rule: a pivot is eligible only if its pivot bar AND its entire
    left/right confirmation window occur after C. This prevents a pivot that
    merely becomes known after C from smuggling pre-C structure into K.
    """
    df = _validate_bars(corr)
    c_idx = _bar_idx_at_or_after(df, correction_start_time)
    highs, lows = causal_pivots(df, left=pivots_left, right=pivots_right)

    def post_c(p: Pivot) -> bool:
        return p.time > correction_start_time and p.idx - pivots_left >= c_idx

    highs = [h for h in highs if post_c(h)]
    lows = [l for l in lows if post_c(l)]

    if direction == "long":
        for low1 in lows:
            hs = [h for h in highs if h.idx > low1.idx]
            if not hs:
                continue
            high1 = hs[0]
            ls = [l for l in lows if l.idx > high1.idx and l.price > low1.price]
            if ls:
                return low1, high1, ls[0]
    else:
        for high1 in highs:
            ls = [l for l in lows if l.idx > high1.idx]
            if not ls:
                continue
            low1 = ls[0]
            hs = [h for h in highs if h.idx > low1.idx and h.price < high1.price]
            if hs:
                return high1, low1, hs[0]
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
    htf, corr, conf = map(_validate_bars, (htf_1h, corr_15m, confirm_5m))
    rows = []
    for ind in build_indications(htf, left=pivot_left, right=pivot_right):
        corr_start_idx = _bar_idx_at_or_after(corr, ind.indication_time)
        if corr_start_idx >= len(corr):
            continue
        corr_end = min(len(corr), corr_start_idx + max_correction_bars)

        # C is the first observable counter-direction bar AFTER I. I itself
        # can never be C, and C must precede every structural pivot used below.
        correction_start = None
        for i in range(corr_start_idx, corr_end):
            if ind.direction == "long" and float(corr.at[i, "low"]) < float(corr.at[i, "open"]):
                correction_start = corr.at[i, "timestamp"]
                break
            if ind.direction == "short" and float(corr.at[i, "high"]) > float(corr.at[i, "open"]):
                correction_start = corr.at[i, "timestamp"]
                break
        if correction_start is None:
            continue

        structure = _correction_structure(
            corr.iloc[corr_start_idx:corr_end].reset_index(drop=True),
            direction=ind.direction,
            correction_start_time=correction_start,
            pivots_left=pivot_left,
            pivots_right=pivot_right,
        )
        if structure is None:
            continue

        first_pivot, reaction_pivot, transition_pivot = structure
        structure_known = transition_pivot.known_time
        if not (
            ind.indication_time < correction_start
            < first_pivot.time < reaction_pivot.time
            < transition_pivot.time < structure_known
        ):
            continue

        conf_start = _bar_idx_at_or_after(conf, structure_known)
        continuation_idx = None
        for i in range(conf_start, min(len(conf), conf_start + max_continuation_bars)):
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

        rows.append({
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
        })

    columns = [
        "direction", "indication_level", "indication_time", "source_pivot_time",
        "source_pivot_known_time", "correction_start_time",
        "correction_structure_known_time", "correction_first_pivot_time",
        "correction_reaction_pivot_time", "correction_transition_pivot_time",
        "continuation_time", "continuation_price", "causal_ok",
        "continuation_crosses_original_indication",
    ]
    out = pd.DataFrame(rows, columns=columns)
    if out.empty:
        return out
    out["causal_ok"] = (
        (out.indication_time < out.correction_start_time)
        & (out.correction_start_time < out.correction_structure_known_time)
        & (out.correction_structure_known_time < out.continuation_time)
    )
    out["continuation_crosses_original_indication"] = np.where(
        out.direction.eq("long"),
        out.continuation_price > out.indication_level,
        out.continuation_price < out.indication_level,
    )
    return out.reset_index(drop=True)


def audit_invariants(events: pd.DataFrame) -> dict[str, int]:
    if events.empty:
        return {"events": 0, "causal_violations": 0, "original_level_violations": 0,
                "same_bar_correction_structure": 0, "same_bar_correction_continuation": 0}
    causal = ~(
        (events.indication_time < events.correction_start_time)
        & (events.correction_start_time < events.correction_structure_known_time)
        & (events.correction_structure_known_time < events.continuation_time)
    )
    level = ~events.continuation_crosses_original_indication.astype(bool)
    return {
        "events": int(len(events)),
        "causal_violations": int(causal.sum()),
        "original_level_violations": int(level.sum()),
        "same_bar_correction_structure": int(
            (events.correction_start_time == events.correction_structure_known_time).sum()
        ),
        "same_bar_correction_continuation": int(
            (events.correction_structure_known_time == events.continuation_time).sum()
        ),
    }


def main() -> None:
    raise SystemExit("V2 is a library/research substrate; use astra_v6_icc_audit.py.")


if __name__ == "__main__":
    main()
