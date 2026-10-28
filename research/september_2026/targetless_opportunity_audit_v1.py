"""ASTRA targetless-opportunity research audit (not an execution strategy).

Consumes decision-time records exported from a *known* source run. Never
constructs fills, order intents, historical price paths, or synthetic targets.
Even a qualified shadow zone is only a research proposal, never a live target.

Usage:
  python targetless_opportunity_audit_v1.py --input decisions.jsonl \
      --output audit.json --expected-total 3069 --expected-missing 2054

See TARGETLESS_OPPORTUNITY_CONTRACT.md for the input schema. All times UTC epoch.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path

TFS = {1, 5, 15, 60, 240}
SIDES = {"LONG", "SHORT"}
STATES = {"ALIGNED", "OPPOSED", "UNKNOWN"}
ZONE_STATES = {"VALID", "SWEPT_RECLAIM", "CONSUMED", "INVALID", "UNKNOWN"}
OWNERS = {"SELECTED_PARENT", "SELECTED_ANCESTOR", "OTHER", "UNKNOWN"}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite numeric price")
    return float(value)


def _clock(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative UTC epoch integer")
    return value


def _string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _enum(value, choices, name):
    if value not in choices:
        raise ValueError(f"{name} must be one of {sorted(choices)}")
    return value


def classify(row):
    """Score a single frozen decision packet, with no future prices or outcomes.

    Returns diagnostic hypotheses. It does NOT decide to enter a trade.
    Candidate validity is assumed only as reported by the source; a separate
    source-line/provenance audit is still required to authenticate it.
    """
    if not isinstance(row, dict):
        raise ValueError("observation must be an object")
    rid = _string(row.get("observation_id"), "observation_id")
    parent = _string(row.get("parent_id"), "parent_id")
    formation = _string(row.get("formation_id"), "formation_id")
    at = _clock(row.get("decision_at"), "decision_at")
    side = _enum(row.get("side"), SIDES, "side")
    reference = _number(row.get("entry_reference"), "entry_reference")
    stop = _number(row.get("exact_far_wick_stop"), "exact_far_wick_stop")
    if side == "LONG" and not stop < reference or side == "SHORT" and not stop > reference:
        raise ValueError("far-wick stop must be on the adverse side of entry reference")
    m1 = _enum(row.get("m1_state"), STATES, "m1_state")
    m5 = _enum(row.get("m5_state"), STATES, "m5_state")
    parent_state = _enum(row.get("parent_state"), {"ACTIVE", "INVALID", "UNKNOWN"}, "parent_state")
    coverage = _enum(row.get("coverage"), {"COMPLETE", "UNKNOWN"}, "coverage")
    has_target = row.get("canonical_target_available")
    if type(has_target) is not bool:
        raise ValueError("canonical_target_available must be an explicit boolean from the existing selector")
    gates = row.get("knowledge_times", {})
    if not isinstance(gates, dict):
        raise ValueError("knowledge_times must be a mapping")
    required_clocks = ("parent", "m1", "m5", "formation", "return")
    chronology = {k: "UNKNOWN" if gates.get(k) is None else "LATE" if _clock(gates[k], f"knowledge_times.{k}") > at else "KNOWN"
                  for k in required_clocks}
    for key, value in gates.items():
        if key not in required_clocks and value is not None:
            chronology[key] = "LATE" if _clock(value, f"knowledge_times.{key}") > at else "KNOWN"
    if any(value == "LATE" for value in chronology.values()):
        raise ValueError(f"observation {rid}: a required source fact was first known after decision")
    if not isinstance(row.get("candidate_zones", []), list):
        raise ValueError("candidate_zones must be a list")
    reasons = []
    def reason(key):
        if key not in reasons:
            reasons.append(key)
    if parent_state == "INVALID": reason("PARENT_INVALID")
    if parent_state == "UNKNOWN": reason("PARENT_UNKNOWN")
    if coverage != "COMPLETE": reason("COVERAGE_UNKNOWN")
    if m1 == "OPPOSED": reason("M1_OPPOSED")
    if m1 == "UNKNOWN": reason("M1_UNKNOWN")
    if m5 == "OPPOSED": reason("M5_OPPOSED")
    if m5 == "UNKNOWN": reason("M5_UNKNOWN")
    if any(value == "UNKNOWN" for value in chronology.values()): reason("KNOWLEDGE_UNKNOWN")
    core_ready = parent_state == "ACTIVE" and coverage == "COMPLETE" and m1 == "ALIGNED" and m5 == "ALIGNED" and all(value == "KNOWN" for value in chronology.values())

    zones = row.get("candidate_zones", [])
    zone_counts = Counter()
    shadow = []
    for z in zones:
        if not isinstance(z, dict):
            raise ValueError("zone must be an object")
        zid = _string(z.get("id"), "zone.id")
        kind = _enum(z.get("kind"), {"SUPPLY", "DEMAND"}, "zone.kind")
        known = _clock(z.get("known_at"), "zone.known_at")
        source_status_known = _clock(z.get("status_known_at"), "zone.status_known_at")
        state = _enum(z.get("state"), ZONE_STATES, "zone.state")
        owner = _enum(z.get("owner_relation"), OWNERS, "zone.owner_relation")
        tf = z.get("timeframe_minutes")
        if type(tf) is not int or tf not in TFS:
            raise ValueError("zone.timeframe_minutes must be 1,5,15,60,240")
        low = _number(z.get("low"), "zone.low")
        high = _number(z.get("high"), "zone.high")
        if low >= high:
            raise ValueError("zone low must be less than zone high")
        status_coverage = _enum(z.get("coverage"), {"COMPLETE", "UNKNOWN"}, "zone.coverage")
        if known > at or source_status_known > at:
            zone_counts["NOT_KNOWN_AT_DECISION"] += 1
            continue
        if status_coverage != "COMPLETE":
            zone_counts["LIFECYCLE_COVERAGE_UNKNOWN"] += 1
            continue
        if state in {"CONSUMED", "INVALID"}:
            zone_counts["CONSUMED_OR_INVALID"] += 1
            continue
        if state == "UNKNOWN":
            zone_counts["LIFECYCLE_UNKNOWN"] += 1
            continue
        if owner not in {"SELECTED_PARENT", "SELECTED_ANCESTOR"}:
            zone_counts["PARENT_RELEVANCE_UNVERIFIED"] += 1
            continue
        if (side == "LONG" and kind != "SUPPLY") or (side == "SHORT" and kind != "DEMAND"):
            zone_counts["WRONG_OPPOSING_SIDE"] += 1
            continue
        ahead = (low > reference) if side == "LONG" else (high < reference)
        if not ahead:
            zone_counts["NOT_WHOLLY_AHEAD"] += 1
            continue
        zone_counts["POSSIBLE_TARGET_REQUIRES_SOURCE_VALIDATION"] += 1
        shadow.append({"id": zid, "timeframe_minutes": tf, "owner_relation": owner,
                       "state": state, "distance_points": round(low - reference if side == "LONG" else reference - high, 8),
                       "proximal": low if side == "LONG" else high, "known_at": known})
    shadow.sort(key=lambda z: (z["distance_points"], z["timeframe_minutes"], z["known_at"], z["id"]))
    if not has_target:
        reason("NO_CANONICAL_TARGET")
        if not zones: reason("NO_CATALOG_ZONES")
        for key in zone_counts:
            if key != "POSSIBLE_TARGET_REQUIRES_SOURCE_VALIDATION": reason(key)
        if shadow: reason("POTENTIAL_SELECTOR_DISAGREEMENT_NEEDS_SOURCE_REVIEW")
    # Do not infer an executable target or entry from shadow observations.
    if not core_ready:
        state = "INELIGIBLE_OR_UNKNOWN_CONTEXT"
    elif has_target:
        state = "BASELINE_SOURCE_READY_TARGET_RECORDED"
    elif shadow:
        state = "TARGETLESS_POSSIBLE_ZONE_MAPPING_DEFECT"
    elif zone_counts.get("LIFECYCLE_COVERAGE_UNKNOWN") or zone_counts.get("LIFECYCLE_UNKNOWN"):
        state = "TARGETLESS_UNRESOLVED_LIFECYCLE"
    else:
        state = "TARGETLESS_WITHOUT_PROVEN_OBJECTIVE"
    return {"observation_id": rid, "parent_id": parent, "formation_id": formation,
            "decision_at": at, "side": side, "m1_state": m1, "m5_state": m5,
            "canonical_target_available": has_target, "core_ready": core_ready,
            "analysis_state": state, "reasons": reasons,
            "zone_census": dict(zone_counts),
            "shadow_candidates": shadow,
            "execution_allowed": False,
            "source_audit_required": True}


def audit(rows, expected_total=None, expected_missing=None):
    """Summarize independent observations AND repeated-return transitions.

    Later returns are linked only in an *ex post* report. They are never used
    to alter any individual prior decision or declare the later one a winner.
    """
    out = []
    ids = set()
    for r in rows:
        result = classify(r)
        if result["observation_id"] in ids:
            raise ValueError("duplicate observation_id")
        ids.add(result["observation_id"])
        out.append(result)
    if not out:
        raise ValueError("no actual observations supplied; cannot infer a population")
    out.sort(key=lambda r: (r["decision_at"], r["observation_id"]))
    by_state, by_reason = Counter(), Counter()
    families = defaultdict(list)
    for r in out:
        by_state[r["analysis_state"]] += 1
        by_reason.update(r["reasons"])
        families[(r["parent_id"], r["formation_id"], r["side"])].append(r)
    later_transitions = Counter()
    first_later = []
    for key, events in families.items():
        for i, row in enumerate(events):
            row["return_number_within_family"] = i + 1
            row["later_return_known_only_in_retrospective_audit"] = i + 1 < len(events)
            if i:
                later_transitions[(events[i-1]["analysis_state"], row["analysis_state"])] += 1
        if len(events) > 1:
            first_later.append({"parent_id":key[0], "formation_id":key[1], "side":key[2],
                                "first_at":events[0]["decision_at"], "later_return_count":len(events)-1,
                                "status_first":events[0]["analysis_state"], "status_last":events[-1]["analysis_state"]})
    missing = sum(not r["canonical_target_available"] for r in out)
    summary = {"observations":len(out), "canonical_no_target_observations":missing,
               "canonical_with_target_observations":len(out)-missing,
               "distinct_parent_formation_side_families":len(families),
               "families_with_later_returns":len(first_later),
               "source_complete_for_expected_population":(len(out)==expected_total and missing==expected_missing)
                   if expected_total is not None and expected_missing is not None else None,
               "by_state":dict(by_state), "all_reasons_overlap":dict(by_reason),
               "later_return_state_transitions": [
                   {"from":k[0],"to":k[1],"count":v}
                   for k,v in sorted(later_transitions.items())],
               "not_executed":True,
               "not_a_profitability_test":True,
               "missing_market_outcomes_and_source_coverage_not_imputed":True}
    return {"version":"ASTRA_TARGETLESS_OPPORTUNITY_AUDIT_V1",
            "summary":summary,"observations":deepcopy(out),
            "repeat_family_diagnostics":first_later}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-missing", type=int)
    args = parser.parse_args()
    with args.input.open() as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    result = audit(rows,args.expected_total,args.expected_missing)
    if args.output.exists():
        raise FileExistsError("refusing to overwrite previous audit")
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result["summary"],indent=2,sort_keys=True))


if __name__ == "__main__":
    main()
