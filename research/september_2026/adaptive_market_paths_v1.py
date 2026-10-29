"""ASTRA 6: causal liquidity landmarks + distinct break-entry research path.

Research-only. No orders, no QuantConnect/cloud actions, no profit claims.
The established responsible-zone-return policy is not imported or modified.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
SIDES = {"LONG", "SHORT"}
CONTEXTS = {
    "PD_ETH",              # Previous *completed* Globex trading day, calendar-supplied
    "PD_RTH",              # Previous *completed* 09:30-16:00 New York regular window
    "PW_ETH",              # Previous *completed* exchange week
    "PM_ETH",              # Previous *completed* exchange month (NOT premarket)
    "PREMARKET_0300_0800",# Current complete 03:00-08:00 New York period
    "OVERNIGHT", "ASIA", "LONDON", "OPENING_RANGE_0930_1000",
}


def _utc(t: datetime, name: str) -> datetime:
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return t.astimezone(UTC)


def _price(x: float, name: str) -> float:
    if type(x) not in (float, int) or not isfinite(x) or x <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(x)


@dataclass(frozen=True)
class MinuteBar:
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    source_id: str
    dataset_id: str = "UNSPECIFIED_DATASET"
    contract_segment_id: str = "UNSPECIFIED_SEGMENT"

    def __post_init__(self):
        a, b = _utc(self.start, "bar.start"), _utc(self.end, "bar.end")
        if b - a != timedelta(minutes=1):
            raise ValueError("Only complete one-minute bars are accepted")
        o, h, lo, c = (_price(getattr(self, n), n) for n in ("open", "high", "low", "close"))
        if (not lo <= min(o, c) <= max(o, c) <= h or
            any(not isinstance(s, str) or not s.strip() for s in
                (self.source_id, self.dataset_id, self.contract_segment_id))):
            raise ValueError("Invalid OHLC/source")
        object.__setattr__(self, "start", a)
        object.__setattr__(self, "end", b)


@dataclass(frozen=True)
class PeriodEvidence:
    """Causally sealed period from independently verified session/calendar data."""
    kind: str
    period_id: str
    start: datetime
    end: datetime
    known_at: datetime
    high: float
    low: float
    source_id: str
    coverage_complete: bool
    dataset_id: str = "UNSPECIFIED_DATASET"
    contract_segment_id: str = "UNSPECIFIED_SEGMENT"

    def __post_init__(self):
        a, b, k = (_utc(getattr(self, n), n) for n in ("start", "end", "known_at"))
        if (self.kind not in CONTEXTS or not self.period_id or not self.source_id or
            not self.dataset_id or not self.contract_segment_id or not a < b <= k):
            raise ValueError("Invalid period identity or availability clocks")
        if _price(self.low, "low") > _price(self.high, "high"):
            raise ValueError("low > high")
        if type(self.coverage_complete) is not bool:
            raise ValueError("Explicit coverage_complete boolean required")
        for n, value in (("start", a), ("end", b), ("known_at", k)):
            object.__setattr__(self, n, value)


@dataclass(frozen=True)
class LiquidityLevel:
    id: str
    kind: str
    side: str
    price: float
    known_at: datetime
    source_id: str
    period_end: datetime
    coverage_complete: bool
    label: str
    dataset_id: str = "UNSPECIFIED_DATASET"
    contract_segment_id: str = "UNSPECIFIED_SEGMENT"
    state: str = "AVAILABLE"
    state_known_at: datetime | None = None

    @property
    def target_proxy_only(self) -> bool:
        return True  # OHLC extremes are NOT authenticated resting stop inventory.


class CausalLiquidityBook:
    def __init__(self):
        self._periods: dict[tuple[str, str], PeriodEvidence] = {}
        self._events: dict[str, list[tuple[datetime, str, str]]] = {}

    def _record(self, level_id: str, known_at: datetime, state: str, source_id: str):
        if not source_id:
            raise ValueError("Coverage/price events need source provenance")
        time = _utc(known_at, "known_at")
        prior = self._events.setdefault(level_id, [])
        if prior and time <= prior[-1][0]:
            raise ValueError("Level events must advance causal clocks")
        prior.append((time, state, source_id))

    def note_coverage_unknown(self, known_at: datetime, source_id: str,
                              level_ids: set[str] | None = None):
        """Mark only affected known levels unknown; never revive stale liquidity."""
        t = _utc(known_at, "known_at")
        for level in self.levels(t, latest_only=False):
            if (level.state == "AVAILABLE" and
                (level_ids is None or level.id in level_ids)):
                self._record(level.id, t, "UNKNOWN_COVERAGE", source_id)

    def observe_completed_bar(self, bar: MinuteBar):
        """Track breaches after recognition; no invented intrabar order.

        Both high/low can be crossed in one OHLC bar. Record the observed
        strict crossings, but do not infer which occurred first.
        """
        for level in self.levels(bar.start, latest_only=False):
            if (level.state != "AVAILABLE" or not level.coverage_complete or
                level.dataset_id != bar.dataset_id or
                level.contract_segment_id != bar.contract_segment_id or
                bar.dataset_id == "UNSPECIFIED_DATASET" or
                bar.contract_segment_id == "UNSPECIFIED_SEGMENT"):
                continue
            if level.side == "BUY_SIDE":
                crossed = bar.high > level.price
                reclaimed = bar.close < level.price
            else:
                crossed = bar.low < level.price
                reclaimed = bar.close > level.price
            if not crossed:
                continue
            state = ("SWEPT_RECLAIM" if reclaimed else "CLOSED_BEYOND" if
                     (bar.close > level.price if level.side == "BUY_SIDE" else bar.close < level.price)
                     else "CROSSED_CLOSE_EQUAL_UNRESOLVED")
            self._record(level.id, bar.end, state, bar.source_id)

    def register(self, period: PeriodEvidence):
        key = (period.kind, period.period_id)
        if key in self._periods:
            if self._periods[key] != period:
                raise ValueError("Cannot rewrite an already-sealed period")
            return
        self._periods[key] = period

    def levels(self, as_of: datetime, latest_only: bool = True) -> list[LiquidityLevel]:
        t = _utc(as_of, "as_of")
        available = [p for p in self._periods.values() if p.known_at <= t]
        if latest_only:
            latest: dict[str, PeriodEvidence] = {}
            for p in available:
                if p.kind not in latest or (p.end, p.known_at, p.period_id) > (
                    latest[p.kind].end, latest[p.kind].known_at, latest[p.kind].period_id
                ):
                    latest[p.kind] = p
            available = list(latest.values())
        result = []
        for p in available:
            for suffix, side, price in (("H", "BUY_SIDE", p.high), ("L", "SELL_SIDE", p.low)):
                lid = f"{p.kind}:{p.period_id}:{suffix}"
                known_events = [e for e in self._events.get(lid, []) if e[0] <= t]
                last = known_events[-1] if known_events else None
                result.append(LiquidityLevel(
                    id=lid, kind=p.kind, side=side, price=float(price), known_at=p.known_at,
                    source_id=p.source_id, period_end=p.end,
                    coverage_complete=p.coverage_complete, label=f"{p.kind}_{suffix}",
                    dataset_id=p.dataset_id, contract_segment_id=p.contract_segment_id,
                    state=last[1] if last else "AVAILABLE",
                    state_known_at=last[0] if last else p.known_at,
                ))
        return sorted(result, key=lambda x: (x.kind, x.id))


class Premarket0300To0800:
    """Exact 03:00-08:00 America/New_York; never backfills missing bars.

    Feed completed 1m source bars in actual arrival order. Seal at/after 08:00
    NY. Incomplete periods are recorded UNKNOWN, never guessed from partial data.
    """
    def __init__(self):
        self._last_end: datetime | None = None
        self._observed: dict[date, dict[int, MinuteBar]] = {}
        self._sealed: dict[date, PeriodEvidence] = {}

    def feed(self, bar: MinuteBar):
        if self._last_end is not None and bar.end <= self._last_end:
            raise ValueError("Source minute stream must advance monotonically")
        self._last_end = bar.end
        local = bar.start.astimezone(NY)
        if local.weekday() > 4 or not (time(3) <= local.time().replace(tzinfo=None) < time(8)):
            return
        slot = local.hour * 60 + local.minute - 180
        day = self._observed.setdefault(local.date(), {})
        if slot in day:
            raise ValueError("Duplicate local premarket minute")
        day[slot] = bar

    def provisional(self, as_of: datetime) -> dict | None:
        t = _utc(as_of, "as_of")
        local = t.astimezone(NY)
        bars = [b for b in self._observed.get(local.date(), {}).values() if b.end <= t]
        if not bars:
            return None
        return {"date": local.date().isoformat(), "known_at": max(b.end for b in bars),
                "high_so_far": max(b.high for b in bars),
                "low_so_far": min(b.low for b in bars),
                "bars_seen": len(bars), "state": "PROVISIONAL_NOT_FINAL_TARGET"}

    def seal(self, day: date, as_of: datetime, source_id: str,
             dataset_id: str = "UNSPECIFIED_DATASET",
             contract_segment_id: str = "UNSPECIFIED_SEGMENT") -> PeriodEvidence:
        if not isinstance(day, date) or not isinstance(source_id, str) or not source_id:
            raise ValueError("Explicit day/source required")
        start = datetime.combine(day, time(3), NY).astimezone(UTC)
        end = datetime.combine(day, time(8), NY).astimezone(UTC)
        if _utc(as_of, "as_of") < end:
            raise ValueError("Cannot seal before 08:00 NY")
        bars = self._observed.get(day, {})
        full = len(bars) == 300 and set(bars) == set(range(300))
        if day in self._sealed:
            if self._sealed[day].source_id != source_id:
                raise ValueError("Cannot replace a sealed source")
            return self._sealed[day]
        if not bars:
            # An unavailable period has no actual levels and cannot be registered.
            raise ValueError("No premarket source bars: no price levels exist")
        if (dataset_id == "UNSPECIFIED_DATASET" or
            contract_segment_id == "UNSPECIFIED_SEGMENT" or
            any(b.dataset_id != dataset_id or
                b.contract_segment_id != contract_segment_id for b in bars.values())):
            raise ValueError("Premarket source/contract does not match sealed period")
        high, low = max(b.high for b in bars.values()), min(b.low for b in bars.values())
        p = PeriodEvidence("PREMARKET_0300_0800", day.isoformat(), start, end,
                           end, high, low, source_id, full,
                           dataset_id, contract_segment_id)
        self._sealed[day] = p
        return p


@dataclass(frozen=True)
class StructureSetup:
    """Complete *supplied* causal setup; not an autonomous structure detector."""
    id: str
    path: str               # CONTINUATION or REVERSAL
    side: str
    parent_id: str
    parent_side: str
    parent_state: str
    parent_known_at: datetime
    correction_id: str
    correction_validated: bool
    correction_known_at: datetime
    selected_origin_id: str
    origin_known_at: datetime
    exact_far_wick_stop: float
    protected_reference_id: str
    protected_reference_price: float
    protected_reference_known_at: datetime
    previous_close: float
    previous_close_known_at: datetime
    confirmation_tf_minutes: int
    break_start: datetime
    break_end: datetime
    break_close: float
    zone_id: str | None = None
    opposing_zone_kind: str | None = None
    zone_far_edge: float | None = None
    zone_known_at: datetime | None = None
    old_parent_invalidated_at: datetime | None = None
    new_parent_confirmed_at: datetime | None = None
    m1_structure: str = "UNKNOWN"
    m5_structure: str = "UNKNOWN"
    dataset_id: str = "UNSPECIFIED_DATASET"
    contract_segment_id: str = "UNSPECIFIED_SEGMENT"


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    path: str
    setup_id: str
    decision_at: datetime
    stop: float | None = None
    target: float | None = None
    target_id: str | None = None
    entry_open: float | None = None
    side: str | None = None
    m1_structure: str = "UNKNOWN"
    m5_structure: str = "UNKNOWN"
    execution_allowed: bool = False


def evaluate_break(setup: StructureSetup, levels: list[LiquidityLevel]) -> Decision:
    """Recognize an alternative completed-bar break; NO order or actual entry.

    This path intentionally does NOT inherit the canonical 1M+5M AND entry gate:
    its stricter independent 15M/60M correction+BOS+parent rules are an
    unapproved research hypothesis. Explicit opposing child structure stays
    in diagnostics; later outcome cannot select the path.
    """
    t0 = _utc(setup.break_start, "break_start")
    t1 = _utc(setup.break_end, "break_end")
    other = (setup.m1_structure, setup.m5_structure)
    def decision(status, reason, stop=None, target=None, target_id=None):
        return Decision(status, reason, setup.path, setup.id, t1, stop, target,
                        target_id, side=setup.side, m1_structure=other[0], m5_structure=other[1])
    if (not setup.id or not setup.parent_id or not setup.correction_id or
        not setup.selected_origin_id or not setup.protected_reference_id or
        setup.path not in ("CONTINUATION", "REVERSAL") or
        setup.side not in SIDES or setup.parent_side not in SIDES or
        setup.parent_state not in ("ACTIVE", "INVALID", "UNKNOWN") or
        setup.confirmation_tf_minutes not in (15, 60) or
        not setup.dataset_id or not setup.contract_segment_id or
        other[0] not in ("ALIGNED", "OPPOSED", "UNKNOWN") or
        other[1] not in ("ALIGNED", "OPPOSED", "UNKNOWN") or
        t1 - t0 != timedelta(minutes=setup.confirmation_tf_minutes)):
        return decision("REJECT", "INVALID_OR_UNSUPPORTED_EVIDENCE")
    if setup.parent_state != "ACTIVE" or setup.side != setup.parent_side:
        return decision("WAIT", "SELECTED_PARENT_NOT_ACTIVE_OR_NOT_ALIGNED")
    if not setup.correction_validated:
        return decision("WAIT", "CLEAR_CORRECTION_NOT_SOURCE_CONFIRMED")
    clocks = [setup.parent_known_at, setup.correction_known_at,
              setup.origin_known_at, setup.protected_reference_known_at,
              setup.previous_close_known_at]
    if any(_utc(c, "known_at") > t0 for c in clocks):
        return decision("WAIT", "REQUIRED_CONTEXT_NOT_KNOWN_BEFORE_BREAK")
    if _utc(setup.previous_close_known_at, "previous_close_known_at") != t0:
        return decision("WAIT", "PREVIOUS_STRUCTURE_CLOSE_NOT_AT_BREAK_START")
    if setup.path == "REVERSAL":
        if (setup.old_parent_invalidated_at is None or setup.new_parent_confirmed_at is None or
            _utc(setup.old_parent_invalidated_at, "old_parent_invalidated_at") > t0 or
            _utc(setup.new_parent_confirmed_at, "new_parent_confirmed_at") > t0 or
            _utc(setup.old_parent_invalidated_at, "old_parent_invalidated_at") >
                _utc(setup.new_parent_confirmed_at, "new_parent_confirmed_at")):
            return decision("WAIT", "REVERSAL_REQUIRES_CONFIRMED_NEW_PARENT")
    ref, prev, close = (_price(getattr(setup, n), n) for n in
                        ("protected_reference_price", "previous_close", "break_close"))
    if not (prev <= ref < close if setup.side == "LONG" else prev >= ref > close):
        return decision("WAIT", "NO_NEW_CAUSAL_PROTECTED_STRUCTURE_BREAK")
    stop = _price(setup.exact_far_wick_stop, "exact_far_wick_stop")
    if not (stop < close if setup.side == "LONG" else stop > close):
        return decision("REJECT", "BAD_SELECTED_ORIGIN_STOP_GEOMETRY")
    if setup.zone_id is not None:
        if (not setup.zone_id or setup.zone_far_edge is None or setup.zone_known_at is None or
            setup.opposing_zone_kind != ("SUPPLY" if setup.side == "LONG" else "DEMAND") or
            _utc(setup.zone_known_at, "zone_known_at") > t0):
            return decision("WAIT", "UNVERIFIED_OPPOSING_ZONE_CONTEXT")
        edge = _price(setup.zone_far_edge, "zone_far_edge")
        # A wick through a supply/demand zone is not an established breakout.
        if not (prev <= edge < close if setup.side == "LONG" else prev >= edge > close):
            return decision("WAIT", "OPPOSING_ZONE_NOT_BROKEN_BY_COMPLETED_CLOSE")
    candidates = []
    for l in levels:
        if (not l.coverage_complete or l.state != "AVAILABLE" or
            l.dataset_id != setup.dataset_id or
            l.contract_segment_id != setup.contract_segment_id or
            _utc(l.known_at, "known_at") > t0 or
            l.side != ("BUY_SIDE" if setup.side == "LONG" else "SELL_SIDE")):
            continue
        if (l.price > close if setup.side == "LONG" else l.price < close):
            candidates.append(l)
    if not candidates:
        return decision("WATCH", "NO_CAUSALLY_KNOWN_AHEAD_LIQUIDITY_OBJECTIVE", stop=stop)
    target_level = min(candidates, key=lambda l: (abs(l.price-close), l.id))
    return decision("BREAK_CONFIRMED_AWAITING_OPEN", "ALTERNATE_PATH_RESEARCH_ONLY",
                    stop=stop, target=target_level.price, target_id=target_level.id)


def evaluate_next_open(result: Decision, next_open: float, open_at: datetime) -> Decision:
    """Geometry-only next-minute probe. No submitted, modeled, or live fill."""
    if result.status != "BREAK_CONFIRMED_AWAITING_OPEN":
        return result
    at = _utc(open_at, "open_at")
    if at != result.decision_at:
        return Decision("WAIT", "NEXT_MINUTE_OPEN_NOT_OBSERVED", result.path,
                        result.setup_id, result.decision_at)
    price = _price(next_open, "next_open")
    # Preserve the source direction; never infer it from convenient geometry.
    valid = ((result.stop < price < result.target) if result.side == "LONG" else
             (result.target < price < result.stop) if result.side == "SHORT" else False)
    if not valid:
        return Decision("REJECT", "NEXT_OPEN_OUTSIDE_FROZEN_STOP_TARGET",
                        result.path, result.setup_id, result.decision_at,
                        stop=result.stop, target=result.target, target_id=result.target_id,
                        entry_open=price, side=result.side,
                        m1_structure=result.m1_structure,
                        m5_structure=result.m5_structure)
    return Decision("RESEARCH_ENTRY_GEOMETRY_ONLY", "NO_NATIVE_FILL_OR_PERFORMANCE_VALIDATION",
                    result.path, result.setup_id, result.decision_at,
                    stop=result.stop, target=result.target, target_id=result.target_id,
                    entry_open=price, side=result.side,
                    m1_structure=result.m1_structure,
                    m5_structure=result.m5_structure, execution_allowed=False)
