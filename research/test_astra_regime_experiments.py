"""Regression tests for causal regime experiment candle and alignment logic."""
import unittest
from datetime import datetime, timedelta

import pandas as pd

from astra_regime_experiments import (
    ARMS,
    build_15m_bars,
    classify_bars,
    is_direction_aligned,
    _classify_setups,
)


class RegimeExperimentTests(unittest.TestCase):
    def minute_rows(self, first, count):
        rows = []
        for offset in range(count):
            timestamp = first + timedelta(minutes=offset)
            price = 100 + offset
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": price - 0.25,
                    "high": price + 0.5,
                    "low": price - 0.5,
                    "close": price,
                }
            )
        return pd.DataFrame(rows)

    def test_close_stamped_minutes_end_on_exact_quarter_hour(self):
        minutes = self.minute_rows(datetime(2024, 1, 2, 9, 1), 15)
        bars = build_15m_bars(minutes, "close")
        self.assertEqual(bars.regime_at.iloc[0], datetime(2024, 1, 2, 9, 15))
        self.assertEqual(bars.close.iloc[0], 114)

    def test_open_stamped_minutes_end_on_next_quarter_hour(self):
        minutes = self.minute_rows(datetime(2024, 1, 2, 9, 0), 15)
        bars = build_15m_bars(minutes, "open")
        self.assertEqual(bars.regime_at.iloc[0], datetime(2024, 1, 2, 9, 15))

    def test_incomplete_or_noncontiguous_candle_is_discarded(self):
        minutes = self.minute_rows(datetime(2024, 1, 2, 9, 1), 15)
        minutes = minutes.drop(index=7)
        with self.assertRaisesRegex(ValueError, "no complete 15-minute bars"):
            build_15m_bars(minutes, "close")

    def test_session_gap_reset_and_continuous_history_are_distinct(self):
        start = datetime(2024, 1, 2, 9, 0)
        rows = []
        for index in range(81):
            if index < 45:
                close_time = start + timedelta(minutes=15 * index)
            else:
                close_time = start + timedelta(minutes=15 * index + 60)
            close = 100 + index
            rows.append(
                {
                    "regime_at": close_time,
                    "open": close - 0.25,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                }
            )
        bars = pd.DataFrame(rows)
        reset = classify_bars(bars, reset_on_gap=True, definition="original")
        carry = classify_bars(bars, reset_on_gap=False, definition="original")
        session_carry = classify_bars(
            bars,
            reset_on_gap=False,
            definition="original",
            max_gap=timedelta(minutes=90),
        )
        long_gap_bars = bars.copy()
        long_gap_bars.loc[45:, "regime_at"] += timedelta(minutes=120)
        long_gap = classify_bars(
            long_gap_bars,
            reset_on_gap=False,
            definition="original",
            max_gap=timedelta(minutes=90),
        )
        self.assertEqual(reset.regime.iloc[-1], "WARMUP")
        self.assertEqual(carry.regime.iloc[-1], "TREND_UP")
        self.assertEqual(session_carry.regime.iloc[-1], "TREND_UP")
        self.assertEqual(long_gap.regime.iloc[-1], "WARMUP")

    def test_alignment_is_directional_and_not_just_trending(self):
        self.assertTrue(is_direction_aligned("long", "TREND_UP"))
        self.assertTrue(is_direction_aligned("short", "TREND_DOWN"))
        self.assertFalse(is_direction_aligned("long", "TREND_DOWN"))
        self.assertFalse(is_direction_aligned("short", "TRANSITION"))

    def test_open_timestamp_decision_can_use_only_the_completed_bar(self):
        setup = pd.DataFrame(
            [
                {
                    "setup_id": 1,
                    "direction": "long",
                    "confirmation_timestamp": datetime(2024, 1, 2, 9, 14),
                }
            ]
        )
        regimes = pd.DataFrame(
            [
                {
                    "regime_at": datetime(2024, 1, 2, 9, 15),
                    "regime": "TREND_UP",
                    "efficiency": 0.5,
                    "atr_relative": 1.0,
                    "displacement": 4.0,
                }
            ]
        )
        arm = next(arm for arm in ARMS if arm["name"] == "open_aligned_gap_reset")
        classified = _classify_setups(setup, regimes, arm)
        self.assertEqual(classified.regime_at.iloc[0], datetime(2024, 1, 2, 9, 15))
        self.assertEqual(classified.decision_time.iloc[0], datetime(2024, 1, 2, 9, 15))
        self.assertTrue(classified.accepted.iloc[0])

    def test_displacement_experiment_is_stricter_than_efficiency_only(self):
        start = datetime(2024, 1, 2, 9, 0)
        rows = []
        for index in range(81):
            close = 100 + 0.1 * index
            rows.append(
                {
                    "regime_at": start + timedelta(minutes=15 * index),
                    "open": close - 0.05,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                }
            )
        bars = pd.DataFrame(rows)
        baseline = classify_bars(bars, reset_on_gap=False, definition="original")
        stricter = classify_bars(
            bars, reset_on_gap=False, definition="displacement_confirmed"
        )
        self.assertEqual(baseline.regime.iloc[-1], "TREND_UP")
        self.assertEqual(stricter.regime.iloc[-1], "TRANSITION")


if __name__ == "__main__":
    unittest.main()
