import pandas as pd

from astra_v6_icc_event_engine_v2 import _correction_structure, causal_pivots


def bars(rows):
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])


def test_correction_structure_rejects_pivot_whose_left_context_predates_c():
    t = pd.date_range("2026-01-01 00:00", periods=12, freq="15min")
    rows = []
    prices = [
        (100, 101, 99, 100), (100, 102, 99.5, 101),
        (101, 103, 100, 102), (102, 102.5, 98, 99),
        (99, 99.5, 97, 98), (98, 100, 97.5, 99),
        (99, 104, 98.5, 103), (103, 104, 100, 101),
        (101, 102, 99, 101), (101, 103, 100.5, 102),
        (102, 103, 101, 102), (102, 103, 101.5, 102),
    ]
    for ts, (o, h, l, c) in zip(t, prices):
        rows.append((ts, o, h, l, c))
    df = bars(rows)
    # C occurs after the first candidate pivot's left-context bars.
    structure = _correction_structure(
        df,
        direction="long",
        correction_start_time=t[4],
        pivots_left=2,
        pivots_right=2,
    )
    if structure is not None:
        for p in structure:
            assert p.idx - 2 >= 4


def test_causal_pivot_known_time_is_after_pivot_bar():
    t = pd.date_range("2026-01-01", periods=8, freq="15min")
    df = bars([(ts, 10+i, 12+i, 9+i, 11+i) for i, ts in enumerate(t)])
    highs, lows = causal_pivots(df, left=2, right=2)
    for p in highs + lows:
        assert p.time < p.known_time
