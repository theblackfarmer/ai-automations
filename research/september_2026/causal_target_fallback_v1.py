"""ASTRA 6 — research-only target fallback, independent of entry admission.

Policy hypothesis:
  1. Retain the currently selected first relevant opposing-zone target unchanged.
  2. Only if that selector returns None, inspect the nearest source-confirmed,
     as-of-known, not-already-touched directional liquidity level.
  3. If its minute history is incomplete, do NOT assert that the level survived.

This module never substitutes its proposal into an executable order, makes a
native fill, alters the exact selected-origin far-wick stop, or certifies
profitability. Confirmed 1m AND 5m support remains the separate canonical
entry requirement; this is strictly a target search.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from adaptive_market_paths_v1 import (LiquidityLevel, MinuteBar, PeriodEvidence,
                                      _utc, _price, CONTEXTS)


@dataclass(frozen=True)
class TargetProposal:
    status: str  # CANONICAL_ZONE, LIQUIDITY_FALLBACK, NO_VERIFIED_TARGET
    target_id: str | None
    target_price: float | None
    source_kind: str | None
    target_known_at: datetime | None
    selected_origin_far_wick_stop: float
    decision_reference: float
    examined_levels: int
    exclusion_counts: dict[str, int]
    research_only: bool = True
    execution_allowed: bool = False
    changes_entry_confirmation: bool = False
    canonical_target_unchanged: bool = True
    level_represents_actual_resting_orders: bool = False


@dataclass(frozen=True)
class ConfirmedSwingLiquidity:
    """A previously confirmed 1m/5m/15m/1h/4h high/low, never a future pivot.

    The native event adapter must independently bind these IDs to the selected
    parent and source dataset. Source-timeframe pivots are different from
    session-period highs/lows and never masquerade as PeriodEvidence.
    """
    id: str
    timeframe_minutes: int
    side: str
    price: float
    pivot_start: datetime
    pivot_source_end: datetime
    known_at: datetime
    parent_id: str
    source_id: str
    dataset_id: str
    contract_segment_id: str
    state: str = 'AVAILABLE'
    state_known_at: datetime | None = None

    def __post_init__(self):
        if (self.timeframe_minutes not in (1, 5, 15, 60, 240) or
            self.side not in ('BUY_SIDE', 'SELL_SIDE') or
            any(not isinstance(v, str) or not v.strip() for v in
                (self.id, self.parent_id, self.source_id, self.dataset_id,
                 self.contract_segment_id))):
            raise ValueError('Confirmed swing requires sourced timeframe, side and parent')
        start = _utc(self.pivot_start, 'pivot_start')
        end = _utc(self.pivot_source_end, 'pivot_source_end')
        recognized = _utc(self.known_at, 'known_at')
        if end - start != timedelta(minutes=self.timeframe_minutes) or recognized < end:
            raise ValueError('Cannot recognize swing before its actual native source bar completes')
        _price(self.price, 'swing price')
        for field, value in (('pivot_start', start), ('pivot_source_end',end),('known_at',recognized)):
            object.__setattr__(self, field, value)


def _crossed_or_touched(level: LiquidityLevel | ConfirmedSwingLiquidity, bar: MinuteBar) -> bool:
    # A boundary touched exactly is not independently certified as unswept.
    # Do not conflate it with the book's strict cross/sweep labeling.
    return (bar.high >= level.price if level.side == 'BUY_SIDE'
            else bar.low <= level.price)


def _source_freshness(level: LiquidityLevel | ConfirmedSwingLiquidity, decision_at: datetime,
                      bars: Sequence[MinuteBar], dataset_id: str,
                      contract_id: str) -> str:
    """Prove continuous native minute coverage after known_at, or fail closed.

    A scheduled exchange closure requires an independently validated calendar
    bridge; this conservative first version does not accept asserted bridges.
    It never forward-fills absent minutes or reconnects a rolled contract.
    """
    current = level.known_at
    if current == decision_at:
        return 'VERIFIED_PREFIX'
    if current > decision_at:
        return 'FUTURE_LEVEL'
    if current.second or current.microsecond:
        return 'NON_MINUTE_KNOWLEDGE_CLOCK'
    # Bars must be completed by the current decision; never consume the future.
    seen_any = False
    for bar in bars:
        if bar.end <= current:
            continue
        if bar.start >= decision_at:
            break
        if bar.start != current or bar.end > decision_at:
            return 'MISSING_MINUTE_OR_SCHEDULE_BRIDGE'
        if bar.dataset_id != dataset_id or bar.contract_segment_id != contract_id:
            return 'SOURCE_OR_CONTRACT_CHANGED'
        if _crossed_or_touched(level, bar):
            return 'ALREADY_TOUCHED_OR_SWEPT'
        current = bar.end
        seen_any = True
    if current != decision_at or not seen_any:
        return 'MISSING_MINUTE_OR_SCHEDULE_BRIDGE'
    return 'VERIFIED_PREFIX'


def select_fallback_target(*, side: str, decision_at: datetime,
                           decision_reference: float, exact_far_wick_stop: float,
                           canonical_target: Mapping | None,
                           liquidity_levels: Sequence[LiquidityLevel],
                           source_periods: Sequence[PeriodEvidence],
                           known_completed_minutes: Sequence[MinuteBar],
                           dataset_id: str, contract_segment_id: str,
                           confirmed_swings: Sequence[ConfirmedSwingLiquidity] = (),
                           selected_parent_id: str | None = None) -> TargetProposal:
    """Diagnostic target proposal at a frozen decision, never order permission.

    Caller MUST provide the original _target result unchanged as
    canonical_target; no new 1m/5m entry admission is made here. Session
    high/low levels must originate from independently verified period packets.
    """
    now = _utc(decision_at, 'decision_at')
    if side not in ('LONG','SHORT'):
        raise ValueError('Explicit LONG or SHORT required')
    if (not isinstance(dataset_id, str) or not dataset_id.strip() or
        dataset_id == 'UNSPECIFIED_DATASET' or
        not isinstance(contract_segment_id, str) or not contract_segment_id.strip() or
        contract_segment_id == 'UNSPECIFIED_SEGMENT'):
        raise ValueError('Explicit verified dataset and contract identity required')
    ref = _price(decision_reference, 'decision_reference')
    stop = _price(exact_far_wick_stop, 'exact_far_wick_stop')
    if not (stop < ref if side == 'LONG' else stop > ref):
        raise ValueError('Exact original stop must be adverse to decision reference')
    if not isinstance(known_completed_minutes, (list, tuple)):
        raise ValueError('An ordered immutable known-minute prefix is required')
    prior_start = None
    for b in known_completed_minutes:
        if not isinstance(b, MinuteBar):
            raise ValueError('Only native source-backed complete MinuteBar records accepted')
        if b.end > now:
            raise ValueError('Future bar included in decision prefix')
        if prior_start is not None and b.start <= prior_start:
            raise ValueError('Unordered or duplicate source minute')
        prior_start = b.start

    total_examined = len(liquidity_levels) + len(confirmed_swings)

    def result(status, id=None, price=None, kind=None, known=None, counts=None):
        return TargetProposal(status, id, price, kind, known, stop, ref,
                              total_examined if status != 'CANONICAL_ZONE' else 0,
                              dict(sorted((counts or {}).items())))

    # Explicit priority: the frozen native selector is authoritative for this
    # comparison. Never change its ID or price based on session levels.
    if canonical_target is not None:
        if not isinstance(canonical_target, Mapping):
            raise ValueError('Original selected target must be mapping or None')
        zid, price = canonical_target.get('zone_id'), canonical_target.get('price')
        if not isinstance(zid, str) or not zid:
            raise ValueError('Original canonical target missing zone ID')
        p = _price(price, 'canonical_target.price')
        known = canonical_target.get('known_at')
        if known is None or not isinstance(known, (int, float)):
            raise ValueError('Original canonical target missing integer known-at UTC seconds')
        if type(known) is not int:
            raise ValueError('Original canonical known-at must be integer')
        from datetime import timezone
        known_dt = datetime.fromtimestamp(known, tz=timezone.utc)
        if known_dt > now or not (p > ref if side == 'LONG' else p < ref):
            raise ValueError('Original target not causally available ahead of decision')
        if canonical_target.get('valid') is False or canonical_target.get('target_valid') is False:
            raise ValueError('Original selected target was not valid at decision')
        return result('CANONICAL_ZONE', zid, p, 'OPPOSING_ZONE', known_dt)

    if (not isinstance(liquidity_levels, (list, tuple)) or
        not isinstance(source_periods, (list, tuple)) or
        not isinstance(confirmed_swings, (list, tuple))):
        raise ValueError('Liquidity inputs and source periods must be immutable sequences')
    expected_period_levels = {}
    for period in source_periods:
        if not isinstance(period, PeriodEvidence):
            raise ValueError('Original source period must be typed PeriodEvidence')
        if _utc(period.known_at, 'period.known_at') > now:
            continue
        if period.dataset_id != dataset_id or period.contract_segment_id != contract_segment_id:
            continue
        for suffix, price in (('H', period.high), ('L', period.low)):
            identity = f'{period.kind}:{period.period_id}:{suffix}'
            if identity in expected_period_levels:
                raise ValueError('Duplicate source-period liquidity identity')
            expected_period_levels[identity] = (period, price)
    expected_side = 'BUY_SIDE' if side == 'LONG' else 'SELL_SIDE'
    skip = Counter()
    best_by_kind = {}
    # An older PDH/PWH/PMH must not survive as a "previous" level when a newer
    # completed period of the same kind is already known at the decision.
    for l in liquidity_levels:
        if not isinstance(l, LiquidityLevel):
            raise ValueError('Liquidity levels must be typed as source evidence')
        if l.kind not in CONTEXTS:
            raise ValueError('Unsupported period family')
        k = _utc(l.known_at, 'level.known_at')
        if k <= now:
            latest = best_by_kind.get(l.kind)
            if latest is None or (l.period_end, k) > latest:
                best_by_kind[l.kind] = (l.period_end, k)
    candidates = []
    for l in liquidity_levels:
        known = _utc(l.known_at, 'level.known_at')
        if known > now:
            skip['FUTURE_KNOWN'] += 1
            continue
        if (l.period_end, known) < best_by_kind[l.kind]:
            skip['SUPERSEDED_PERIOD'] += 1
            continue
        if l.side != expected_side:
            skip['WRONG_SIDE'] += 1
            continue
        if l.dataset_id != dataset_id or l.contract_segment_id != contract_segment_id:
            skip['WRONG_SOURCE_OR_CONTRACT'] += 1
            continue
        original = expected_period_levels.get(l.id)
        if original is None:
            skip['SOURCE_PERIOD_NOT_AUTHENTICATED'] += 1
            continue
        period, period_price = original
        if (l.source_id != period.source_id or l.kind != period.kind or
            l.price != period_price or l.known_at != period.known_at or
            l.period_end != period.end or
            l.dataset_id != period.dataset_id or
            l.contract_segment_id != period.contract_segment_id):
            skip['SOURCE_PERIOD_MISMATCH'] += 1
            continue
        if not l.source_id or not l.id or not l.coverage_complete or not period.coverage_complete:
            skip['PERIOD_UNVERIFIED_OR_INCOMPLETE'] += 1
            continue
        if l.state != 'AVAILABLE' or (l.state_known_at is not None and
            _utc(l.state_known_at, 'state_known_at') > now):
            skip['LEVEL_CONSUMED_OR_UNRESOLVED'] += 1
            continue
        if not (l.price > ref if side == 'LONG' else l.price < ref):
            skip['NOT_AHEAD'] += 1
            continue
        coverage = _source_freshness(l, now, known_completed_minutes,
                                     dataset_id, contract_segment_id)
        if coverage != 'VERIFIED_PREFIX':
            skip[coverage] += 1
            continue
        candidates.append(l)
    # Confirmed source pivots can supply lower-/higher-timeframe liquidity when
    # no canonical opposing supply/demand target exists. Unlike session highs,
    # structural pivots MUST be bound to the selected parent's identity.
    seen_swings = set()
    for pivot in confirmed_swings:
        if not isinstance(pivot, ConfirmedSwingLiquidity):
            raise ValueError('Untyped or unconfirmed swing liquidity')
        if pivot.id in seen_swings:
            raise ValueError('Duplicate confirmed swing identity')
        seen_swings.add(pivot.id)
        if not selected_parent_id or pivot.parent_id != selected_parent_id:
            skip['WRONG_OR_UNVERIFIED_PARENT'] += 1
            continue
        if pivot.known_at > now:
            skip['FUTURE_KNOWN_SWING'] += 1
            continue
        if pivot.side != expected_side:
            skip['WRONG_SIDE_SWING'] += 1
            continue
        if pivot.dataset_id != dataset_id or pivot.contract_segment_id != contract_segment_id:
            skip['WRONG_SOURCE_OR_CONTRACT_SWING'] += 1
            continue
        if (pivot.state != 'AVAILABLE' or
            (pivot.state_known_at is not None and
             _utc(pivot.state_known_at, 'pivot.state_known_at') > now)):
            skip['SWING_CONSUMED_OR_UNRESOLVED'] += 1
            continue
        if not (pivot.price > ref if side == 'LONG' else pivot.price < ref):
            skip['SWING_NOT_AHEAD'] += 1
            continue
        freshness = _source_freshness(pivot, now, known_completed_minutes,
                                      dataset_id, contract_segment_id)
        if freshness != 'VERIFIED_PREFIX':
            skip['SWING_' + freshness] += 1
            continue
        candidates.append(pivot)
    if not candidates:
        return result('NO_VERIFIED_TARGET', counts=skip)
    target = min(candidates, key=lambda l: (abs(l.price - ref), l.id))
    kind = target.kind if isinstance(target, LiquidityLevel) else (
        f'M{target.timeframe_minutes}_CONFIRMED_SWING')
    return result('LIQUIDITY_FALLBACK', target.id, target.price, kind,
                  _utc(target.known_at, 'known_at'), skip)


def verify_next_open_only(proposal: TargetProposal, next_open: float,
                          original_stop: float) -> bool:
    """Non-executing sanity check. Never claims a fill or changes the stop."""
    if proposal.target_price is None or proposal.target_id is None:
        return False
    if proposal.selected_origin_far_wick_stop != _price(original_stop, 'original_stop'):
        return False
    price = _price(next_open, 'next_open')
    is_long = proposal.selected_origin_far_wick_stop < proposal.decision_reference
    return (proposal.selected_origin_far_wick_stop < price < proposal.target_price
            if is_long else proposal.target_price < price < proposal.selected_origin_far_wick_stop)
