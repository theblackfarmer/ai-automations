"""Outcome-blind ASTRA 6 targetless-return / adaptive-path cross-reference.

READ-ONLY RESEARCH. This module does not recognize structures, manufacture
historical prices, place orders, or infer profitability. It takes frozen
native decision packets and independently source-supplied adaptive structure
packets, then checks causal chronology and next-open geometry.

All adaptive observations are independent *later candidate events*, never
retroactive repairs to a prior no-target decision.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import fields
from datetime import datetime, timezone, timedelta
import argparse
import json
from pathlib import Path

from adaptive_market_paths_v1 import (
    CausalLiquidityBook, MinuteBar, PeriodEvidence, StructureSetup,
    evaluate_break, evaluate_next_open,
)
from targetless_opportunity_audit_v1 import audit as audit_original

TIME_FIELDS = {
    "parent_known_at", "correction_known_at", "origin_known_at",
    "protected_reference_known_at", "previous_close_known_at",
    "break_start", "break_end", "zone_known_at",
    "old_parent_invalidated_at", "new_parent_confirmed_at",
}
PERIOD_TIME_FIELDS = {"start", "end", "known_at"}


def when(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name}: timestamp must be an ISO 8601 UTC-offset string")
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name}: invalid ISO 8601 timestamp") from exc
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"{name}: no timezone offset")
    return t.astimezone(timezone.utc)


def typed(cls, payload, time_fields, name):
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a mapping")
    allowed = {f.name for f in fields(cls)}
    if set(payload) - allowed:
        raise ValueError(f"{name}: unrecognized fields {sorted(set(payload)-allowed)}")
    x = {k: when(v, f"{name}.{k}") if k in time_fields and v is not None else v
         for k, v in payload.items()}
    try:
        return cls(**x)
    except TypeError as exc:
        raise ValueError(f"{name}: required input missing") from exc


def _source_bar(blob, name, *, expected_dataset, expected_segment):
    if not isinstance(blob, dict):
        raise ValueError(f"{name}: missing actual native minute")
    if (blob.get("dataset_id") != expected_dataset or
            blob.get("contract_segment_id") != expected_segment or
            blob.get("observed_from_source") is not True or
            blob.get("fill_forward") is not False):
        raise ValueError(f"{name}: real source and segment proof required")
    if not isinstance(blob.get("source_id"), str) or not blob["source_id"].strip():
        raise ValueError(f"{name}: native source_id required")
    v = {k: blob[k] for k in ("open", "high", "low", "close", "source_id",
                              "dataset_id", "contract_segment_id")}
    v["start"] = when(blob.get("start"), f"{name}.start")
    v["end"] = when(blob.get("end"), f"{name}.end")
    return MinuteBar(**v)


def reconcile(original_rows, new_events, *, expected_total=3069,
              expected_missing=2054):
    """Cross-reference actual source packets, keeping candidate decisions separate.

    A candidate cannot be counted as occurring at the earlier original decision.
    The same later confirmation cannot be used more than once. Incomplete
    evidence stays incomplete. Never output a trading or profit verdict.
    """
    base = audit_original(original_rows, expected_total, expected_missing)
    lookup = {x["observation_id"]: x for x in base["observations"]}
    names = {f.name for f in fields(StructureSetup)}
    seen = set()
    rows = []
    for event in new_events:
        if not isinstance(event, dict):
            raise ValueError("adaptive event must be a mapping")
        event_id = event.get("event_id")
        original_id = event.get("related_original_observation_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen:
            raise ValueError("duplicate or missing adaptive event ID")
        seen.add(event_id)
        if original_id not in lookup:
            raise ValueError(f"{event_id}: original observation was not supplied")
        prior = lookup[original_id]
        src = event.get("setup")
        if not isinstance(src, dict) or set(src) - names:
            raise ValueError(f"{event_id}: invalid setup fields")
        setup = typed(StructureSetup, src, TIME_FIELDS, f"{event_id}.setup")
        if event_id != setup.id:
            raise ValueError(f"{event_id}: event ID and setup ID disagree")
        if setup.break_start.timestamp() < prior["decision_at"]:
            raise ValueError(f"{event_id}: new path relies on future-known state at old decision")
        if (setup.path == "CONTINUATION" and
                (setup.parent_id != prior["parent_id"] or setup.side != prior["side"])):
            raise ValueError(f"{event_id}: continuation must retain original parent and direction")
        if setup.path == "REVERSAL" and (
                event.get("previous_parent_id") != prior["parent_id"] or
                setup.parent_id == prior["parent_id"] or
                setup.side == prior["side"]):
            raise ValueError(f"{event_id}: reversal needs explicit distinct opposite controlling parent")
        if (setup.dataset_id == "UNSPECIFIED_DATASET" or
                setup.contract_segment_id == "UNSPECIFIED_SEGMENT"):
            raise ValueError(f"{event_id}: explicit immutable dataset/contract binding required")
        book = CausalLiquidityBook()
        period_records = []
        for i, period in enumerate(event.get("liquidity_periods", [])):
            obj = typed(PeriodEvidence, period, PERIOD_TIME_FIELDS, f"{event_id}.period[{i}]")
            if (obj.dataset_id != setup.dataset_id or
                    obj.contract_segment_id != setup.contract_segment_id):
                raise ValueError(f"{event_id}: liquidity provenance contradicts setup")
            book.register(obj)
            period_records.append(obj)
        # The caller must supply a *full* actual continuity prefix (since each
        # period became known) to certify statuses. If incomplete, keep a
        # provisional proposal only, not a confirmed ready-for-trade event.
        prefix = event.get("known_completed_minutes", [])
        if not isinstance(prefix, list):
            raise ValueError(f"{event_id}: minute prefix must be a list")
        previous_end = None
        actual_bars = []
        for i, item in enumerate(prefix):
            bar = _source_bar(item, f"{event_id}.prefix[{i}]",
                              expected_dataset=setup.dataset_id,
                              expected_segment=setup.contract_segment_id)
            if bar.end > setup.break_start:
                raise ValueError(f"{event_id}: future bar included in pre-break liquidity state")
            if previous_end is not None:
                if bar.start < previous_end:
                    raise ValueError(f"{event_id}: overlapping or reversed source minutes")
                if bar.start > previous_end:
                    book.note_coverage_unknown(bar.start, f"GAP_OBSERVED_{event_id}_{i}")
            previous_end = bar.end
            actual_bars.append(bar)
            book.observe_completed_bar(bar)
        # Require an actual one-minute source prefix for *each* published
        # liquidity period between its knowledge time and the new break.
        # A summary boolean, or an empty/truncated event stream, cannot prove
        # a level remained available. A scheduled market closure needs a
        # separate explicit audited bridge, not synthetic bars.
        supplied_complete = event.get("known_coverage_complete") is True
        if not isinstance(event.get("known_coverage_complete"), bool):
            raise ValueError(f"{event_id}: explicit source coverage assertion required")
        incomplete_periods = []
        for p in period_records:
            if p.known_at > setup.break_start:
                continue
            cursor = p.known_at
            complete = supplied_complete and p.coverage_complete
            if complete:
                for bar in actual_bars:
                    if bar.end <= cursor:
                        continue
                    if bar.start != cursor:
                        complete = False
                        break
                    cursor = bar.end
                    if cursor == setup.break_start:
                        break
                if cursor != setup.break_start:
                    complete = False
            if not complete:
                incomplete_periods.append((p.kind, p.period_id))
                book.note_coverage_unknown(
                    setup.break_start,
                    f"UNVERIFIED_CONTINUITY:{event_id}:{p.kind}:{p.period_id}",
                    {f"{p.kind}:{p.period_id}:H", f"{p.kind}:{p.period_id}:L"},
                )
        known_coverage_status = (
            "PARTIAL_OR_UNVERIFIED_SOURCE_PREFIX" if incomplete_periods else
            "COMPLETE_SUPPLIED_PREFIX_NOT_INDEPENDENTLY_AUTHENTICATED"
        )
        decision = evaluate_break(setup, book.levels(setup.break_start))
        open_status = "NOT_EVALUATED"
        nxt = event.get("next_minute")
        if nxt is not None:
            # Validate even when an entry was not proposed; optional future
            # evidence cannot evade source/clock checks by taking a WAIT branch.
            b = _source_bar(nxt, f"{event_id}.next_minute",
                            expected_dataset=setup.dataset_id,
                            expected_segment=setup.contract_segment_id)
            if b.start != setup.break_end:
                raise ValueError(f"{event_id}: next-minute open must start at break close")
            if decision.status == "BREAK_CONFIRMED_AWAITING_OPEN":
                probe = evaluate_next_open(decision, b.open, b.start)
                open_status = probe.status
        row = {
            "event_id": event_id,
            "related_original_observation_id": original_id,
            "original_has_target": prior["canonical_target_available"],
            "original_analysis_state": prior["analysis_state"],
            "path": setup.path,
            "parent_id": setup.parent_id,
            "side": setup.side,
            "break_known_at": setup.break_end.isoformat(),
            "original_decision_at_epoch": prior["decision_at"],
            "strictly_later_than_original_decision": (
                setup.break_end.timestamp() > prior["decision_at"]),
            "break_state": decision.status,
            "break_reason": decision.reason,
            "next_open_status": open_status,
            "potential_liquidity_proxy": decision.target_id,
            "level_continuity": known_coverage_status,
            "periods_with_unverified_continuity": [list(k) for k in incomplete_periods],
            "execution_allowed": False,
            "historical_pnl_established": False,
            "candidate_evidence_requires_independent_source_audit": True,
        }
        rows.append(row)
    rows.sort(key=lambda x: (x["break_known_at"], x["event_id"]))
    missing_events = [r for r in rows if not r["original_has_target"]]
    return {
        "version": "ASTRA_ADAPTIVE_POPULATION_RECONCILIATION_V1",
        "summary": {
            "original_observations": base["summary"]["observations"],
            "original_no_target_observations": base["summary"]["canonical_no_target_observations"],
            "original_cohort_complete": base["summary"]["source_complete_for_expected_population"],
            "adaptive_events_supplied": len(rows),
            "adaptive_events_linked_to_original_no_target": len(missing_events),
            "distinct_original_no_target_observations_linked": len({
                r["related_original_observation_id"] for r in missing_events}),
            "new_event_paths": dict(Counter(r["path"] for r in rows)),
            "new_event_break_states": dict(Counter(r["break_state"] for r in rows)),
            "new_event_next_open_states": dict(Counter(r["next_open_status"] for r in rows)),
            "not_a_trade_census": True,
            "no_lookahead_comparison": True,
            "no_profitability_claim": True,
        },
        "original_audit_summary": base["summary"],
        "adaptive_events": rows,
    }


def _jsonl(path):
    with path.open() as fh:
        return [json.loads(x) for x in fh if x.strip()]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original-decisions", type=Path, required=True)
    p.add_argument("--adaptive-events", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--expected-total", type=int, default=3069)
    p.add_argument("--expected-missing", type=int, default=2054)
    a = p.parse_args()
    result = reconcile(_jsonl(a.original_decisions), _jsonl(a.adaptive_events),
                       expected_total=a.expected_total, expected_missing=a.expected_missing)
    if a.output.exists():
        raise FileExistsError("refuse to overwrite prior research receipt")
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
