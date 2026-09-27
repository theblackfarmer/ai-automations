import pandas as pd

from astra_v6_icc_event_engine_v2 import audit_invariants, build_icc_events, causal_pivots


def bars(rows):
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])


def test_pivots_are_known_only_after_confirmation():
    df = bars([
        ("2026-01-01 00:00", 10, 11, 9, 10),
        ("2026-01-01 01:00", 10, 12, 9.5, 11),
        ("2026-01-01 02:00", 11, 13, 10, 12),
        ("2026-01-01 03:00", 12, 12.5, 9, 10),
        ("2026-01-01 04:00", 10, 11, 8, 9),
    ])
    highs, lows = causal_pivots(df, left=1, right=1)
    assert all(p.known_time > p.time for p in highs + lows)


def test_empty_inputs_have_stable_schema_and_zero_violations():
    htf = bars([
        ("2026-01-01 00:00", 10, 11, 9, 10),
        ("2026-01-01 01:00", 10, 10.5, 9.5, 10),
        ("2026-01-01 02:00", 10, 10.5, 9.5, 10),
    ])
    corr = htf.copy()
    conf = htf.copy()
    events = build_icc_events(htf, corr, conf)
    audit = audit_invariants(events)
    assert audit["events"] == 0
    assert audit["causal_violations"] == 0
    assert audit["original_level_violations"] == 0


def test_v2_rejects_same_observation_for_structure_and_continuation():
    events = pd.DataFrame(
        [{
            "indication_time": pd.Timestamp("2026-01-01 01:00"),
            "correction_start_time": pd.Timestamp("2026-01-01 02:00"),
            "correction_structure_known_time": pd.Timestamp("2026-01-01 03:00"),
            "continuation_time": pd.Timestamp("2026-01-01 03:00"),
            "direction": "long",
            "indication_level": 100.0,
            "continuation_price": 101.0,
            "continuation_crosses_original_indication": True,
        }]
    )
    audit = audit_invariants(events)
    assert audit["same_bar_correction_continuation"] == 1
    assert audit["causal_violations"] == 1
