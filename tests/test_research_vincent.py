import pandas as pd
import pytest

from astra_research.vincent import signals, signals_from_bars
from astra_research.data import resample


def sample(direction=1):
    def span(start, end, o, h, l, c):
        t = pd.date_range(start, end, freq="1min", inclusive="left", tz="America/New_York")
        return pd.DataFrame({"timestamp": t.tz_convert("UTC"), "open": o, "high": h,
                             "low": l, "close": c, "volume": 100})
    prior = span("2024-01-02 09:30", "2024-01-02 16:00", 95., 100., 90., 95.)
    pre = span("2024-01-03 04:00", "2024-01-03 09:30", 97., 102., 92., 97.)
    rth = pd.concat([
        span("2024-01-03 09:30", "2024-01-03 09:32", 99., 106., 99., 103.),
        span("2024-01-03 09:32", "2024-01-03 09:34", 103., 104., 100., 101.),
        span("2024-01-03 09:34", "2024-01-03 09:36", 101., 105., 101., 104.5),
        span("2024-01-03 09:36", "2024-01-03 09:40", 104.5, 106., 104., 105.)])
    raw = pd.concat([prior, pre, rth], ignore_index=True)
    if direction == -1:
        old = raw.copy()
        raw["open"], raw["close"] = 200-old.open, 200-old.close
        raw["high"], raw["low"] = 200-old.low, 200-old.high
    raw["known_time"] = raw.timestamp + pd.Timedelta(minutes=1)
    return raw


@pytest.mark.parametrize("direction", [1, -1])
def test_nonvacuous_mirrored_signals(direction):
    result = signals(sample(direction))
    assert len(result) == 1
    s = result.iloc[0]
    assert s.direction == direction
    assert s.indication_time < s.correction_time < s.decision_time
    assert s.structure_known_time == s.correction_time
    assert s.target_known_time < s.correction_time
    assert s.target == (106 if direction == 1 else 94)
    assert s.stop == (99.75 if direction == 1 else 100.25)
    assert not s.source_alignment_available


def test_no_same_bar_retest_or_entry():
    raw = sample()
    for cutoff in ["2024-01-03 14:32Z", "2024-01-03 14:34Z", "2024-01-03 14:35Z"]:
        assert signals(raw[raw.known_time <= pd.Timestamp(cutoff)]).empty
    assert len(signals(raw[raw.known_time <= pd.Timestamp("2024-01-03 14:36Z")])) == 1


def test_full_raw_prefix_stability_and_cached_path():
    raw = sample()
    full = signals(raw)
    for minutes in range(1, 11):
        cutoff = pd.Timestamp("2024-01-03 14:30Z") + pd.Timedelta(minutes=minutes)
        prefix = raw[raw.known_time <= cutoff]
        expected = full[full.decision_time <= cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(signals(prefix), expected, check_dtype=False)
        bars = resample(raw, 2)
        pd.testing.assert_frame_equal(
            signals_from_bars(prefix, bars[bars.timestamp <= cutoff]), expected,
            check_dtype=False)


@pytest.mark.parametrize("missing", ["2024-01-02 16:00Z", "2024-01-03 12:00Z"])
def test_missing_source_session_minute_rejects_day(missing):
    raw = sample()
    assert signals(raw[raw.timestamp != pd.Timestamp(missing)]).empty


def test_confirmation_extreme_not_used_as_target():
    raw = sample()
    raw.loc[raw.timestamp >= pd.Timestamp("2024-01-03 14:34Z"), "high"] = 200.
    s = signals(raw).iloc[0]
    assert s.target == 106.
    assert s.target_known_time == pd.Timestamp("2024-01-03 14:32Z")


def test_retest_extreme_is_excluded_from_frozen_target():
    raw = sample()
    raw.loc[raw.timestamp.isin(pd.date_range("2024-01-03 14:32Z", periods=2, freq="1min")), "high"] = 107.
    later = raw.timestamp >= pd.Timestamp("2024-01-03 14:34Z")
    raw.loc[later, ["high", "close"]] = [110., 108.]
    s = signals(raw).iloc[0]
    assert s.target == 106.
    assert s.target_known_time < s.correction_time
    # Filled-entry geometry is deliberately handled by the execution layer.


def test_gap_outside_ntz_selects_nearest_level_behind_open():
    raw = sample()
    for minute, values in [(30, [103., 110., 103., 106.]),
                           (32, [106., 106., 102., 104.]),
                           (34, [104., 108., 104., 107.])]:
        start = pd.Timestamp(f"2024-01-03 14:{minute}Z")
        raw.loc[raw.timestamp.isin(pd.date_range(start, periods=2, freq="1min")),
                ["open", "high", "low", "close"]] = values
    result = signals(raw)
    assert len(result) == 1
    s = result.iloc[0]
    assert s.level == 102. and s.level_label == "PMH"
    assert s.target == 110.
    assert s.indication_time < s.correction_time < s.decision_time


def test_rejection_bound_failure_cancels_before_confirmation():
    raw = sample()
    failure = raw.timestamp.isin(pd.date_range("2024-01-03 14:34Z", periods=2, freq="1min"))
    raw.loc[failure, ["open", "high", "low", "close"]] = [101., 102., 98., 99.]
    # A later new breakout cannot resurrect the old retest.
    assert signals(raw).empty


def test_duplicate_source_minutes_not_treated_as_complete():
    raw = sample()
    duplicate = raw.iloc[[5]]
    assert signals(pd.concat([raw, duplicate], ignore_index=True)).empty
