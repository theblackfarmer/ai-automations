"""ASTRA 6 native-return target fallback probe (RESEARCH ONLY).

Passive sidecar; never changes MarketContextHooks._target, entry policy, orders,
fills, existing far-wick stop, or the canonical opposing-zone exit. Raw minute
OHLC, native snapshot, and stable parent IDs are mandatory; unverified evidence
cannot graduate a liquidity proxy to a trade.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from typing import Mapping
from zoneinfo import ZoneInfo

from adaptive_market_paths_v1 import MinuteBar, PeriodEvidence
from causal_target_fallback_v1 import select_fallback_target
from native_funnel_capture_v1 import capture_return


def source_period_verified(period, bars):
    """Recompute a named period from an exact, contiguous raw 1m source prefix.

    Does not independently prove vendor authenticity; external source manifest
    verification is still required before any execution backtest.
    """
    if not isinstance(period, PeriodEvidence) or not period.coverage_complete:
        return False
    if not isinstance(bars, (tuple, list)) or not bars:
        return False
    length = int((period.end - period.start).total_seconds())
    if length != len(bars) * 60 or length % 60:
        return False
    if (period.start.second or period.end.second or
        period.start.microsecond or period.end.microsecond):
        return False
    seen = set()
    for i, bar in enumerate(bars):
        if not isinstance(bar, MinuteBar):
            return False
        if (bar.start != period.start + timedelta(minutes=i) or
            bar.end != period.start + timedelta(minutes=i + 1) or
            bar.dataset_id != period.dataset_id or
            bar.contract_segment_id != period.contract_segment_id or
            bar.source_id in seen):
            return False
        seen.add(bar.source_id)
    if (max(b.high for b in bars) != period.high or
        min(b.low for b in bars) != period.low):
        return False
    ny = ZoneInfo("America/New_York")
    start, end = period.start.astimezone(ny), period.end.astimezone(ny)
    clocks = (start.hour, start.minute, end.hour, end.minute)
    if period.kind == "PREMARKET_0300_0800":
        return len(bars) == 300 and clocks == (3, 0, 8, 0) and start.date() == end.date()
    if period.kind == "PD_RTH":
        return len(bars) == 390 and clocks == (9, 30, 16, 0) and start.date() == end.date()
    if period.kind == "OPENING_RANGE_0930_1000":
        return len(bars) == 30 and clocks == (9, 30, 10, 0) and start.date() == end.date()
    if period.kind == "PD_ETH":
        return (len(bars) == 1380 and clocks == (18, 0, 17, 0)
                and (end.date() - start.date()).days == 1)
    # Week/month and other named sessions cross calendar closures: no
    # invented bars or unchecked external holiday/roll evidence.
    return False


def review_native_return(*, source_namespace, event, context, packet, catalog,
                         candidate_zone, dataset_id, contract_segment_id,
                         liquidity_levels=(), source_periods=(),
                         raw_period_minutes=None, known_completed_minutes=(),
                         confirmed_swings=(), confirmed_pivot_events=None):
    """Research-only comparison on an EXISTING CANDIDATE_RETURN decision.

    Native pipeline owner supplies the original packet/zone, source event,
    context and zone catalog at the exact return close. No mutation or fill.
    """
    original = capture_return(source_namespace=source_namespace, event=event,
                              context=context, packet=packet, catalog=catalog)
    if packet.get("candidate_id") != original["candidate_id"]:
        raise ValueError("Candidate identity mismatch")
    parent = packet["parent"]
    if parent.get("parent_id") != original["parent_id"]:
        raise ValueError("Parent identity mismatch")
    if not isinstance(candidate_zone, Mapping) or candidate_zone.get("direction") != parent["direction"]:
        raise ValueError("Selected origin zone mismatch")
    formation = packet["formation_result"]["candidate"]
    if (candidate_zone.get("low") != formation.get("zone_low") or
        candidate_zone.get("high") != formation.get("zone_high")):
        raise ValueError("Selected origin exact far-wick mismatch")
    side = parent["direction"]
    if side not in ("long", "short"):
        raise ValueError("Unknown direction")
    stop = candidate_zone["low"] if side == "long" else candidate_zone["high"]
    reference = (max(formation["proximal"], event["bar"]["close"]) if side == "long"
                 else min(formation["proximal"], event["bar"]["close"]))
    if (type(stop) not in (int, float) or not isfinite(stop) or
        not (stop < reference if side == "long" else stop > reference)):
        raise ValueError("Invalid original stop geometry")
    reasons = context.get("reasons")
    if not isinstance(reasons, (list, tuple)):
        raise ValueError("Explicit native veto reasons required")
    reason_set = set(reasons)
    canonical = context.get("target")
    if ("NO_RELEVANT_OPPOSING_TARGET" in reason_set) == (canonical is not None):
        raise ValueError("Native canonical selector and reason disagree")
    current = (isinstance(context.get("parent"), Mapping) and
               context["parent"].get("parent_id") == original["parent_id"] and
               isinstance(context.get("contact"), Mapping) and
               context["contact"].get("contact_id") == packet["contact"]["contact_id"])
    aligned = original["m1"] == original["m5"] == "ALIGNED"
    decision = datetime.fromtimestamp(original["decision_at"], tz=timezone.utc)
    identity = sha256((source_namespace + "|" + original["candidate_id"] + "|"
                       + original["return_source_id"] + "|" +
                       str(original["decision_at"])).encode()).hexdigest()
    result = dict(decision_id=identity, decision_at=original["decision_at"],
                  candidate_id=original["candidate_id"], parent_id=original["parent_id"],
                  original_stage=original["stage"], native_m1_m5_aligned=aligned,
                  native_parent_contact_current=current,
                  native_other_veto=bool(reason_set - {"NO_RELEVANT_OPPOSING_TARGET"}),
                  original_stop_unchanged=stop, original_entry_reference=reference,
                  canonical_target_id=original["selected_zone_id"],
                  canonical_parent_relevance=original["parent_relevance"],
                  source_periods_verified=0, pivots_source_events_verified=0,
                  target_id=None, target_price=None, status="UNASSESSED",
                  execution_allowed=False, research_only=True, orders_changed=False,
                  native_fills_verified=False, profitability_verified=False,
                  original_entry_rules_unchanged=True, original_exit_rules_unchanged=True)
    if canonical is not None:
        return dict(result, status="CANONICAL_ZONE_UNCHANGED",
                    target_id=canonical["zone_id"], target_price=canonical["price"])
    if not aligned or not current or reason_set != {"NO_RELEVANT_OPPOSING_TARGET"}:
        return dict(result, status="BLOCKED_NATIVE_ENTRY_GATE")
    periods = tuple(p for p in source_periods if source_period_verified(
        p, (raw_period_minutes or {}).get((p.kind, p.period_id), ())))
    verified_swings = []
    for pivot in confirmed_swings:
        e = (confirmed_pivot_events or {}).get(pivot.id)
        if not isinstance(e, Mapping) or e.get("kind") != "CONFIRMED_STRUCTURAL_PIVOT":
            continue
        if any(e.get(field) != value for field, value in (
                ("pivot_id", pivot.id), ("source_id", pivot.source_id),
                ("parent_id", pivot.parent_id), ("dataset_id", pivot.dataset_id),
                ("contract_segment_id", pivot.contract_segment_id),
                ("timeframe_minutes", pivot.timeframe_minutes),
                ("side", pivot.side), ("price", pivot.price),
                ("known_at", pivot.known_at))):
            continue
        if pivot.known_at <= decision:
            verified_swings.append(pivot)
    proposed = select_fallback_target(
        side="LONG" if side == "long" else "SHORT", decision_at=decision,
        decision_reference=reference, exact_far_wick_stop=stop,
        canonical_target=None, liquidity_levels=tuple(liquidity_levels),
        source_periods=periods, known_completed_minutes=tuple(known_completed_minutes),
        dataset_id=dataset_id, contract_segment_id=contract_segment_id,
        confirmed_swings=tuple(verified_swings), selected_parent_id=original["parent_id"])
    return dict(result, status=proposed.status, target_id=proposed.target_id,
                target_price=proposed.target_price,
                target_source_kind=proposed.source_kind,
                source_periods_verified=len(periods),
                pivots_source_events_verified=len(verified_swings),
                exclusion_counts=proposed.exclusion_counts)
