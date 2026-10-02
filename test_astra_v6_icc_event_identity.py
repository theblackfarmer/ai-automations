#!/usr/bin/env python3
"""Regression tests for ICC V2 event identity."""

import pandas as pd

from astra_v6_icc_event_engine_v2 import deduplicate_icc_events


def event(**overrides):
    row = {
        "direction": "short",
        "indication_level": 19874.75,
        "indication_time": pd.Timestamp("2024-05-08 07:00"),
        "source_pivot_time": pd.Timestamp("2024-05-07 18:00"),
        "source_pivot_known_time": pd.Timestamp("2024-05-07 20:00"),
        "correction_start_time": pd.Timestamp("2024-05-08 07:15"),
        "correction_structure_known_time": pd.Timestamp("2024-05-08 12:45"),
        "correction_first_pivot_time": pd.Timestamp("2024-05-08 10:45"),
        "correction_reaction_pivot_time": pd.Timestamp("2024-05-08 11:45"),
        "correction_transition_pivot_time": pd.Timestamp("2024-05-08 12:15"),
        "continuation_time": pd.Timestamp("2024-05-08 12:50"),
        "continuation_price": 19863.25,
    }
    row.update(overrides)
    return row


def main():
    # Older and newer source pivots resolving to the identical ICC path collapse to one event.
    duplicate = pd.DataFrame([
        event(),
        event(
            source_pivot_time=pd.Timestamp("2024-05-08 01:00"),
            source_pivot_known_time=pd.Timestamp("2024-05-08 03:00"),
        ),
    ])
    out = deduplicate_icc_events(duplicate)
    assert len(out) == 1
    assert out.iloc[0].source_pivot_time == pd.Timestamp("2024-05-08 01:00")

    # A genuinely different correction path remains a separate event.
    distinct = pd.DataFrame([
        event(),
        event(
            source_pivot_time=pd.Timestamp("2024-05-08 01:00"),
            source_pivot_known_time=pd.Timestamp("2024-05-08 03:00"),
            continuation_time=pd.Timestamp("2024-05-08 13:00"),
        ),
    ])
    out = deduplicate_icc_events(distinct)
    assert len(out) == 2

    print("PASS: ICC V2 event-identity regression tests")


if __name__ == "__main__":
    main()
