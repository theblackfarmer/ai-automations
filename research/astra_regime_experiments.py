"""Causal, research-only comparisons for the frozen ASTRA discovery setups."""
import hashlib
import io
import urllib.request

import pandas as pd

from astra_market_regime import Bar, CausalRegime15m

SOURCE_URL = (
    "https://raw.githubusercontent.com/s-k-28/nq-es-trader-5k-payout/main/"
    "data/Dataset_NQ_1min_2022_2025.csv"
)
SOURCE_SHA256 = "1577e60a7feab411e49da7a56c7052a64738cd1757cfd60aa11fd783ff43b60b"
BAR_INTERVAL = pd.Timedelta(minutes=15)
LOOKBACK = 20
VOL_LOOKBACK = 60
TREND_EFFICIENCY = 0.38
CHOP_EFFICIENCY = 0.20
HIGH_VOL_RATIO = 1.8
MIN_TREND_DISPLACEMENT_ATR = 4.0
EXPECTED_REFERENCE = {
    "setups": 72454,
    "accepted": 156,
    "unknown": 1763,
    "warmup": 64759,
}

ARMS = (
    {
        "name": "as_run_reference",
        "timestamp_semantics": "open",
        "reset_on_gap": True,
        "definition": "original",
        "allow_exact_matches": False,
        "decision_offset": pd.Timedelta(0),
    },
    {
        "name": "open_aligned_gap_reset",
        "timestamp_semantics": "open",
        "reset_on_gap": True,
        "definition": "original",
        "allow_exact_matches": True,
        "decision_offset": pd.Timedelta(minutes=1),
    },
    {
        "name": "close_aligned_gap_reset",
        "timestamp_semantics": "close",
        "reset_on_gap": True,
        "definition": "original",
        "allow_exact_matches": True,
        "decision_offset": pd.Timedelta(0),
    },
    {
        "name": "close_aligned_all_gap_carry",
        "timestamp_semantics": "close",
        "reset_on_gap": False,
        "max_gap": None,
        "definition": "original",
        "allow_exact_matches": True,
        "decision_offset": pd.Timedelta(0),
    },
    {
        "name": "close_aligned_90m_gap_carry",
        "timestamp_semantics": "close",
        "reset_on_gap": False,
        "max_gap": pd.Timedelta(minutes=90),
        "definition": "original",
        "allow_exact_matches": True,
        "decision_offset": pd.Timedelta(0),
    },
    {
        "name": "close_aligned_volatility_overlay",
        "timestamp_semantics": "close",
        "reset_on_gap": False,
        "max_gap": None,
        "definition": "volatility_overlay",
        "allow_exact_matches": True,
        "decision_offset": pd.Timedelta(0),
    },
    {
        "name": "close_aligned_displacement_confirmed",
        "timestamp_semantics": "close",
        "reset_on_gap": False,
        "max_gap": None,
        "definition": "displacement_confirmed",
        "allow_exact_matches": True,
        "decision_offset": pd.Timedelta(0),
    },
)


def build_15m_bars(minute_bars, timestamp_semantics):
    """Aggregate only complete, contiguous minute bars under an explicit convention."""
    if timestamp_semantics not in ("open", "close"):
        raise ValueError("timestamp_semantics must be 'open' or 'close'")
    bars = minute_bars.sort_values("timestamp").drop_duplicates("timestamp").copy()
    if timestamp_semantics == "open":
        bars["bucket_end"] = bars.timestamp.dt.floor("15min") + BAR_INTERVAL
    else:
        bars["bucket_end"] = bars.timestamp.dt.ceil("15min")

    grouped = bars.groupby("bucket_end", sort=True)
    complete = []
    for end, group in grouped:
        times = group.timestamp.sort_values()
        if len(group) != 15:
            continue
        if (times.diff().dropna() != pd.Timedelta(minutes=1)).any():
            continue
        if timestamp_semantics == "close":
            expected_first = end - pd.Timedelta(minutes=14)
            expected_last = end
        else:
            expected_first = end - BAR_INTERVAL
            expected_last = end - pd.Timedelta(minutes=1)
        if times.iloc[0] != expected_first or times.iloc[-1] != expected_last:
            continue
        complete.append(
            {
                "regime_at": end,
                "open": group.iloc[0].open,
                "high": group.high.max(),
                "low": group.low.min(),
                "close": group.iloc[-1].close,
            }
        )
    if not complete:
        raise ValueError("no complete 15-minute bars under the selected timestamp convention")
    return pd.DataFrame(complete).sort_values("regime_at").reset_index(drop=True)


def _label_regime(regime, classifier, definition):
    label = regime.label
    if regime.efficiency is None:
        return label
    if definition == "volatility_overlay" and regime.efficiency >= classifier.trend_eff:
        return "TREND_UP" if regime.displacement > 0 else "TREND_DOWN"
    if (
        definition == "displacement_confirmed"
        and label in ("TREND_UP", "TREND_DOWN")
        and abs(regime.displacement) < MIN_TREND_DISPLACEMENT_ATR
    ):
        return "TRANSITION"
    return label


def classify_bars(bars, reset_on_gap, definition, max_gap=None):
    if definition not in ("original", "volatility_overlay", "displacement_confirmed"):
        raise ValueError("unknown regime definition")
    classifier = CausalRegime15m(
        lookback=LOOKBACK,
        vol_lookback=VOL_LOOKBACK,
        trend_eff=TREND_EFFICIENCY,
        chop_eff=CHOP_EFFICIENCY,
        high_vol_ratio=HIGH_VOL_RATIO,
    )
    observations = []
    previous_end = None
    for row in bars.itertuples(index=False):
        end = row.regime_at
        gap = end - previous_end if previous_end is not None else None
        if previous_end is not None and (
            (reset_on_gap and gap != BAR_INTERVAL)
            or (max_gap is not None and gap > max_gap)
        ):
            classifier = CausalRegime15m(
                lookback=LOOKBACK,
                vol_lookback=VOL_LOOKBACK,
                trend_eff=TREND_EFFICIENCY,
                chop_eff=CHOP_EFFICIENCY,
                high_vol_ratio=HIGH_VOL_RATIO,
            )
        regime = classifier.update_closed_bar(
            Bar(end, float(row.open), float(row.high), float(row.low), float(row.close))
        )
        observations.append(
            {
                "regime_at": end,
                "regime": _label_regime(regime, classifier, definition),
                "efficiency": regime.efficiency,
                "atr_relative": regime.atr_relative,
                "displacement": regime.displacement,
            }
        )
        previous_end = end
    result = pd.DataFrame(observations)
    result["regime_at"] = pd.to_datetime(result.regime_at).astype("datetime64[ns]")
    return result


def is_direction_aligned(direction, regime):
    return (direction == "long" and regime == "TREND_UP") or (
        direction == "short" and regime == "TREND_DOWN"
    )


def _classify_setups(setups, regime_rows, arm):
    candidates = setups.sort_values("confirmation_timestamp").copy()
    candidates["decision_time"] = (
        pd.to_datetime(candidates.confirmation_timestamp).astype("datetime64[ns]")
        + arm["decision_offset"]
    )
    regime_rows = regime_rows.copy()
    regime_rows["regime_at"] = pd.to_datetime(regime_rows.regime_at).astype("datetime64[ns]")
    merged = pd.merge_asof(
        candidates,
        regime_rows.sort_values("regime_at"),
        left_on="decision_time",
        right_on="regime_at",
        direction="backward",
        allow_exact_matches=arm["allow_exact_matches"],
        tolerance=BAR_INTERVAL,
    )
    matched = merged.regime_at.notna()
    if (merged.loc[matched, "regime_at"] > merged.loc[matched, "decision_time"]).any():
        raise RuntimeError("future regime bar matched to a candidate")
    if (
        merged.loc[matched, "decision_time"] - merged.loc[matched, "regime_at"]
        > BAR_INTERVAL
    ).any():
        raise RuntimeError("stale regime bar matched beyond the 15-minute tolerance")
    merged["regime"] = merged.regime.fillna("UNKNOWN")
    merged["accepted"] = [
        is_direction_aligned(direction, regime)
        for direction, regime in zip(merged.direction, merged.regime)
    ]
    merged["rejection_reason"] = [
        "aligned"
        if accepted
        else (
            "regime_unavailable"
            if regime == "UNKNOWN"
            else "warmup"
            if regime == "WARMUP"
            else "direction_mismatch"
            if regime in ("TREND_UP", "TREND_DOWN")
            else "non_directional_regime"
        )
        for accepted, regime in zip(merged.accepted, merged.regime)
    ]
    return merged


def _summary_row(arm, classified):
    regimes = classified.regime
    total = len(classified)
    if arm.get("max_gap") is not None:
        gap_handling = f"reset_if_gap_over_{int(arm['max_gap'].total_seconds() // 60)}m"
    elif arm["reset_on_gap"]:
        gap_handling = "reset_on_any_gap"
    else:
        gap_handling = "carry_all_gaps"
    return {
        "arm": arm["name"],
        "timestamp_semantics": arm["timestamp_semantics"],
        "gap_handling": gap_handling,
        "definition": arm["definition"],
        "lookback_bars": LOOKBACK,
        "vol_lookback_bars": VOL_LOOKBACK,
        "trend_efficiency": TREND_EFFICIENCY,
        "chop_efficiency": CHOP_EFFICIENCY,
        "high_vol_ratio": HIGH_VOL_RATIO,
        "min_trend_displacement_atr": (
            MIN_TREND_DISPLACEMENT_ATR
            if arm["definition"] == "displacement_confirmed"
            else ""
        ),
        "exact_timestamp_matches": arm["allow_exact_matches"],
        "decision_offset_minutes": int(arm["decision_offset"].total_seconds() // 60),
        "setups": total,
        "accepted": int(classified.accepted.sum()),
        "rejected": total - int(classified.accepted.sum()),
        "unknown": int((regimes == "UNKNOWN").sum()),
        "warmup": int((regimes == "WARMUP").sum()),
        "range": int((regimes == "RANGE").sum()),
        "transition": int((regimes == "TRANSITION").sum()),
        "trend_up": int((regimes == "TREND_UP").sum()),
        "trend_down": int((regimes == "TREND_DOWN").sum()),
        "direction_mismatch": int(
            (classified.rejection_reason == "direction_mismatch").sum()
        ),
    }


def _read_source():
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "ASTRA-research/1"})
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if fingerprint != SOURCE_SHA256:
        raise RuntimeError("Historical source checksum mismatch: " + fingerprint)
    minute_bars = pd.read_csv(io.BytesIO(raw))
    minute_bars.columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap_rth",
        "vwap_eth",
    ]
    minute_bars["timestamp"] = pd.to_datetime(minute_bars.timestamp)
    for column in ("open", "high", "low", "close"):
        minute_bars[column] = pd.to_numeric(minute_bars[column], errors="coerce")
    minute_bars = minute_bars.dropna(
        subset=["timestamp", "open", "high", "low", "close"]
    )
    return minute_bars, fingerprint


def run():
    minute_bars, fingerprint = _read_source()
    setups = pd.read_parquet("astra6_causal_setups.parquet")
    if setups.empty:
        raise RuntimeError("No frozen causal setups; cannot report experiment coverage")
    setups["confirmation_timestamp"] = pd.to_datetime(setups.confirmation_timestamp)

    summaries = []
    detail_rows = []
    for arm in ARMS:
        bars = build_15m_bars(minute_bars, arm["timestamp_semantics"])
        regimes = classify_bars(
            bars, arm["reset_on_gap"], arm["definition"], arm.get("max_gap")
        )
        classified = _classify_setups(setups, regimes, arm)
        summaries.append(_summary_row(arm, classified))
        classified["arm"] = arm["name"]
        detail_rows.append(
            classified[
                [
                    "setup_id",
                    "direction",
                    "confirmation_timestamp",
                    "decision_time",
                    "regime_at",
                    "regime",
                    "efficiency",
                    "atr_relative",
                    "displacement",
                    "accepted",
                    "rejection_reason",
                    "arm",
                ]
            ]
        )

    summary = pd.DataFrame(summaries)
    reference = summary[summary.arm == "as_run_reference"].iloc[0]
    for key, expected in EXPECTED_REFERENCE.items():
        if int(reference[key]) != expected:
            raise RuntimeError(
                f"as-run reference changed for {key}: "
                f"expected {expected}, got {int(reference[key])}"
            )
    summary.insert(1, "source_sha256", fingerprint)
    summary.to_csv("astra_regime_experiment_summary.csv", index=False)
    pd.concat(detail_rows, ignore_index=True).to_csv(
        "astra_regime_experiment_setups.csv", index=False
    )
    print(summary.to_string(index=False))
    print("Causal classification only; no trade P&L or execution was simulated.")


if __name__ == "__main__":
    run()
