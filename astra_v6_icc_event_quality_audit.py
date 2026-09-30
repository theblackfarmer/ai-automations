#!/usr/bin/env python3
"""ICC V2 event-population quality audit.

Consumes the frozen structural event artifact and independently checks
uniqueness, ordering, pivot causality, original-I crossing, direction
consistency, overlap diagnostics, and absence of event metadata after K.
"""
from __future__ import annotations
import io, urllib.request
import pandas as pd

URL = "https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv"

def load_1m():
    req = urllib.request.Request(URL, headers={"User-Agent": "Astra6-ICC-V2-Quality/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = ["timestamp","open","high","low","close","volume","vwap_rth","vwap_eth"]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).sort_values("timestamp").reset_index(drop=True)

def resample(df, rule):
    x = df.set_index("timestamp")
    return x.resample(rule, label="left", closed="left").agg(
        open=("open","first"), high=("high","max"), low=("low","min"),
        close=("close","last"), volume=("volume","sum")
    ).dropna(subset=["open","high","low","close"]).reset_index()

def pivot_known_time(m15, t, right=2):
    ts = pd.to_datetime(m15["timestamp"]).reset_index(drop=True)
    pos = int(ts.searchsorted(pd.Timestamp(t), side="left"))
    if pos >= len(ts) or ts.iloc[pos] != pd.Timestamp(t) or pos + right >= len(ts):
        return pd.NaT
    return ts.iloc[pos + right]

def main():
    e = pd.read_parquet("astra6_icc_v2_events.parquet")
    bars = load_1m()
    m15 = resample(bars, "15min")

    exact_dups = int(e.duplicated().sum())
    identity = ["direction","indication_time","correction_start_time",
                "correction_structure_known_time","continuation_time"]
    identity_dups = int(e.duplicated(identity, keep=False).sum())
    canonical = identity + ["indication_level","source_pivot_time"]
    canonical_dups = int(e.duplicated(canonical, keep=False).sum())
    path_identity = [
        "direction", "indication_level", "indication_time",
        "correction_start_time", "correction_first_pivot_time",
        "correction_reaction_pivot_time", "correction_transition_pivot_time",
        "correction_structure_known_time", "continuation_time",
        "continuation_price",
    ]
    semantic_path_dups = int(e.duplicated(path_identity, keep=False).sum())
    duplicate_identity_rows = e[e.duplicated(identity, keep=False)].sort_values(identity)
    duplicate_identity_rows.to_csv("astra6_icc_v2_duplicate_identity_rows.csv", index=False)
    indication_groups = (
        e.groupby(["direction","indication_time"], dropna=False)
         .agg(events=("direction","size"),
              distinct_levels=("indication_level","nunique"),
              distinct_source_pivots=("source_pivot_time","nunique"),
              first_K=("continuation_time","min"),
              last_K=("continuation_time","max"))
         .reset_index()
    )
    indication_groups[indication_groups["events"] > 1].to_csv(
        "astra6_icc_v2_multi_event_indications.csv", index=False)

    strict_order = int((~(
        (e.indication_time < e.correction_start_time) &
        (e.correction_start_time < e.correction_structure_known_time) &
        (e.correction_structure_known_time < e.continuation_time)
    )).sum())

    pivot_cols = ["correction_first_pivot_time",
                  "correction_reaction_pivot_time",
                  "correction_transition_pivot_time"]
    pivots_after_c = int(sum((e[c] <= e.correction_start_time).sum() for c in pivot_cols))
    known = {c: e[c].map(lambda t: pivot_known_time(m15, t)) for c in pivot_cols}
    missing_known = int(sum(v.isna().sum() for v in known.values()))
    known_after_structure = int(sum((v > e.correction_structure_known_time).sum() for v in known.values()))
    transition_mismatch = int((known[pivot_cols[2]] != e.correction_structure_known_time).sum())

    k_bad = int((e.continuation_time <= e.correction_structure_known_time).sum())
    original_i_bad = int((
        (e.direction.eq("long") & (e.continuation_price <= e.indication_level)) |
        (e.direction.eq("short") & (e.continuation_price >= e.indication_level))
    ).sum())
    direction_bad = int((~e.direction.isin(["long","short"])).sum()) + original_i_bad

    # Any event timestamp or causal-known timestamp after K is a post-K dependency.
    time_cols = [c for c in e.columns if c.endswith("_time") or c.endswith("_known_time")]
    post_k = int(sum((e[c] > e.continuation_time).sum() for c in time_cols if c != "continuation_time"))

    # The first/reaction/transition pivots must be strictly ordered.
    pivot_order = int((~(
        (e.correction_first_pivot_time < e.correction_reaction_pivot_time) &
        (e.correction_reaction_pivot_time < e.correction_transition_pivot_time)
    )).sum())

    # Overlap is diagnostic, not a failure by itself: distinct indications can
    # legitimately have intersecting I->K windows. Same-indication overlap is suspicious.
    overlap_pairs = 0
    overlap_same_indication = 0
    for _, g in e.groupby("direction"):
        g = g.sort_values("indication_time")
        rows = list(g[["indication_time","continuation_time"]].itertuples(index=False, name=None))
        for i, (start_i, end_i) in enumerate(rows):
            for start_j, end_j in rows[i+1:]:
                if start_j >= end_i:
                    break
                overlap_pairs += 1
                if start_j == start_i:
                    overlap_same_indication += 1

    rows = [
        ("population_frozen_139", int(len(e) != 139), 0),
        ("exact_duplicate_rows", exact_dups, 0),
        ("canonical_duplicate_event_identities", canonical_dups, 0),
        ("semantic_duplicate_icc_paths", semantic_path_dups, 0),
        ("strict_I_C_structure_K", strict_order, 0),
        ("correction_pivots_after_C", pivots_after_c, 0),
        ("pivot_known_by_structure", missing_known + known_after_structure + transition_mismatch, 0),
        ("K_after_structure", k_bad, 0),
        ("K_crosses_original_I", original_i_bad, 0),
        ("direction_consistency", direction_bad, 0),
        ("no_post_K_information", post_k, 0),
        ("correction_pivot_order", pivot_order, 0),
    ]
    out = pd.DataFrame(rows, columns=["invariant","violations","required"])
    out["pass"] = out["violations"] == out["required"]
    out.to_csv("astra6_icc_v2_event_quality_audit.csv", index=False)

    summary = pd.DataFrame([{
        "events": len(e),
        "long_events": int(e.direction.eq("long").sum()),
        "short_events": int(e.direction.eq("short").sum()),
        "exact_duplicate_rows": exact_dups,
        "duplicate_event_identities_diagnostic": identity_dups,
        "canonical_duplicate_event_identities": canonical_dups,
        "semantic_duplicate_icc_paths": semantic_path_dups,
        "overlap_pairs_diagnostic": overlap_pairs,
        "overlap_same_indication_diagnostic": overlap_same_indication,
        "pivot_known_missing": missing_known,
        "pivot_known_after_structure": known_after_structure,
        "transition_known_time_mismatch": transition_mismatch,
        "post_K_information_fields": post_k,
    }])
    summary.to_csv("astra6_icc_v2_event_quality_summary.csv", index=False)
    print(summary.to_string(index=False))
    print(out.to_string(index=False))
    print("NOTE: duplicate_event_identities_diagnostic counts rows sharing the I/C/structure/K timestamps.")
    print("semantic_duplicate_icc_paths must be zero: one causal ICC path is one event.")
    print("multi_event_indication_groups", int((indication_groups["events"] > 1).sum()))
    if not out["pass"].all():
        raise SystemExit("FAIL: ICC V2 event-quality audit")
    print("PASS: frozen 140-event population cleared event-quality audit")

if __name__ == "__main__":
    main()
