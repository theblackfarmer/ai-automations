"""Frozen ASTRA NQ-only Vincent interpretation, ASTRA6-R1-20261002.

Four-market alignment is unavailable. Fills and risk checks belong to execution.
"""
from datetime import timedelta
import pandas as pd
from .data import resample

ET = "America/New_York"
TICK = 0.25
COLUMNS = ["strategy", "direction", "decision_time", "stop", "target",
           "expiry_time", "structure_known_time", "indication_time",
           "correction_time", "level", "level_label", "ntz_lower", "ntz_upper",
           "target_known_time", "source_alignment_available"]


def _time(day, hour, minute=0):
    return pd.Timestamp(day).tz_localize(ET) + pd.Timedelta(hours=hour, minutes=minute)


def _complete(raw, start, end):
    x = raw[(raw.timestamp >= start) & (raw.timestamp < end)]
    expected = pd.date_range(start, end, freq="1min", inclusive="left").tz_convert("UTC")
    return x if pd.DatetimeIndex(x.timestamp).equals(expected) else None


def signals(raw, ltf_minutes=2, tolerance_ticks=1, strategy="vincent_2m"):
    return signals_from_bars(raw, resample(raw, ltf_minutes), ltf_minutes,
                             tolerance_ticks, strategy)


def signals_from_bars(raw, bars, ltf_minutes=2, tolerance_ticks=1, strategy="vincent_2m"):
    """Cached-bar entry point; caller must slice raw/bars to decision availability.

    Missing previous weekday RTH causes a skip, including unsourced holidays.
    A complete raw prefix produces identical already-known candidates.
    """
    rows = []
    if raw.empty or bars.empty:
        return pd.DataFrame(columns=COLUMNS)
    raw = raw.sort_values("timestamp")
    local_dates = raw.timestamp.dt.tz_convert(ET).dt.date
    days = {day: group for day, group in raw.groupby(local_dates, sort=False)}
    bar_dates = bars.timestamp.dt.tz_convert(ET).dt.date
    bar_days = {day: group for day, group in bars.groupby(bar_dates, sort=False)}
    for day, current in days.items():
        if day.weekday() >= 5:
            continue
        previous = day - timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        if previous not in days or day not in bar_days:
            continue
        start, end = _time(day, 9, 30), _time(day, 16)
        prior = _complete(days[previous], _time(previous, 9, 30), _time(previous, 16))
        pre = _complete(current, _time(day, 4), start)
        opening = current[current.timestamp == start]
        if prior is None or pre is None or opening.empty:
            continue
        levels = [(float(prior.high.max()), "PDH"), (float(prior.low.min()), "PDL"),
                  (float(pre.high.max()), "PMH"), (float(pre.low.min()), "PML")]
        op = float(opening.iloc[0].open)
        lower = sorted((x for x in levels if x[0] <= op), key=lambda x: (op-x[0], x[1]))
        upper = sorted((x for x in levels if x[0] >= op), key=lambda x: (x[0]-op, x[1]))
        selected = {}
        if lower and upper:
            selected = {1: (upper[0], False), -1: (lower[0], False)}
        elif lower:
            selected = {1: (lower[0], True)}
        elif upper:
            selected = {-1: (upper[0], True)}
        lo = lower[0][0] if lower else float("nan")
        hi = upper[0][0] if upper else float("nan")
        states = {d: None for d in selected}
        previous_close = op
        first = True
        intraday = bar_days[day]
        intraday = intraday[(intraday.timestamp >= start + pd.Timedelta(minutes=ltf_minutes)) &
                            (intraday.timestamp <= end)]
        for bar in intraday.itertuples(index=False):
            now = pd.Timestamp(bar.timestamp)
            for direction, ((level, label), gap) in selected.items():
                state = states[direction]
                outward = direction * (bar.close-level) > 0
                crossing = outward and direction * (previous_close-level) <= 0
                if state is None:
                    if crossing or (first and gap and outward):
                        states[direction] = {"phase": "broken", "indication": now,
                                             "extreme": float(bar.high if direction == 1 else bar.low),
                                             "extreme_known": now}
                    continue  # Breakout bar cannot also retest.
                if state["phase"] == "broken":
                    touches = (bar.low <= level + tolerance_ticks*TICK and
                               bar.high >= level - tolerance_ticks*TICK)
                    if touches and outward:
                        state.update(phase="retested", correction=now, high=float(bar.high),
                                     low=float(bar.low), target=state["extreme"],
                                     target_known=state["extreme_known"])
                    else:
                        value = float(bar.high if direction == 1 else bar.low)
                        if direction * (value-state["extreme"]) > 0:
                            state.update(extreme=value, extreme_known=now)
                    continue  # Retest bar cannot also confirm.
                opposite = state["low"] if direction == 1 else state["high"]
                if direction * (bar.close-opposite) < 0:
                    states[direction] = None
                    continue
                boundary = state["high"] if direction == 1 else state["low"]
                if (outward and direction*(bar.close-boundary) > 0 and
                        direction*(previous_close-boundary) <= 0):
                    rows.append(dict(strategy=strategy, direction=direction,
                                     decision_time=now, stop=opposite-direction*TICK,
                                     target=state["target"], expiry_time=end.tz_convert("UTC"),
                                     structure_known_time=state["correction"],
                                     indication_time=state["indication"],
                                     correction_time=state["correction"], level=level,
                                     level_label=label, ntz_lower=lo, ntz_upper=hi,
                                     target_known_time=state["target_known"],
                                     source_alignment_available=False))
                    states[direction] = None
            previous_close = float(bar.close)
            first = False
    return pd.DataFrame(rows, columns=COLUMNS).sort_values(
        ["decision_time", "direction"], ignore_index=True)
