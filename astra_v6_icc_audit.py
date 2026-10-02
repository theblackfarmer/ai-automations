#!/usr/bin/env python3
"""Astra 6 ICC V2 structural audit.

Runs the frozen ICC V2 event engine on the public NQ 1-minute research dataset,
resamples to 1H/15M/5M, writes event/summary/invariant artifacts, and fails on
hard causal or structural invariants.

This is structural research only: no entries, stops, targets, or backtest logic.
"""
from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from astra_v6_icc_event_engine_v2 import build_icc_events, audit_invariants

URL = "https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv"


def load_1m() -> pd.DataFrame:
    req = urllib.request.Request(URL, headers={"User-Agent": "Astra6-ICC-V2/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = ["timestamp", "open", "high", "low", "close", "volume", "vwap_rth", "vwap_eth"]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = df.set_index("timestamp")
    out = x.resample(rule, label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum")
    ).dropna(subset=["open", "high", "low", "close"]).reset_index()
    return out


def main() -> None:
    bars = load_1m()
    h1 = resample(bars, "1h")
    m15 = resample(bars, "15min")
    m5 = resample(bars, "5min")

    events = build_icc_events(h1, m15, m5)
    inv = audit_invariants(events)

    invariant_rows = [
        {"invariant": "causal_order", "violations": inv["causal_violations"],
         "required": 0, "pass": inv["causal_violations"] == 0},
        {"invariant": "original_indication_level", "violations": inv["original_level_violations"],
         "required": 0, "pass": inv["original_level_violations"] == 0},
        {"invariant": "same_bar_correction_structure", "violations": inv["same_bar_correction_structure"],
         "required": 0, "pass": inv["same_bar_correction_structure"] == 0},
        {"invariant": "same_bar_correction_continuation", "violations": inv["same_bar_correction_continuation"],
         "required": 0, "pass": inv["same_bar_correction_continuation"] == 0},
    ]
    invariant_df = pd.DataFrame(invariant_rows)

    summary = pd.DataFrame([{
        "source_rows_1m": len(bars),
        "bars_1h": len(h1),
        "bars_15m": len(m15),
        "bars_5m": len(m5),
        **inv,
        "long_events": int((events["direction"] == "long").sum()) if len(events) else 0,
        "short_events": int((events["direction"] == "short").sum()) if len(events) else 0,
    }])

    events.to_parquet("astra6_icc_v2_events.parquet", index=False)
    summary.to_csv("astra6_icc_v2_summary.csv", index=False)
    invariant_df.to_csv("astra6_icc_v2_invariant_audit.csv", index=False)

    print(summary.to_string(index=False))
    print(invariant_df.to_string(index=False))

    failed = invariant_df[~invariant_df["pass"]]
    if not failed.empty:
        raise SystemExit("FAIL: ICC V2 structural invariant violation")


if __name__ == "__main__":
    main()
