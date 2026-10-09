"""Cold-prefix research audit. No prices, examples, orders or teacher approval built in.

Each required decision reruns the captured runner with a newly constructed engine.
When native inputs are supplied, each prefix is regenerated from truncated M1 rows.
The capture manifest is a local byte-binding receipt, not authenticated provenance.
"""
import argparse
import bisect
import copy
import csv
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TFS = (240, 60, 1)
DECISION_KINDS = {"M1_TRANSITION_BOS", "ENTRY_REVIEW", "ENTRY_REVIEW_BLOCKED"}
AUTHORITY = "ASSUMED_RESEARCH_ENCODING"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def byte_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def event_id(event):
    payload = {k: v for k, v in event.items() if k != "id"}
    return payload["kind"].lower() + ":" + digest([payload])[:24]


def is_decision(event):
    return event.get("kind") in DECISION_KINDS or (
        event.get("kind") == "MAJOR_BOS" and event.get("timeframe") in (240, 60))


def _integer(value):
    return type(value) is int and value >= 0


def validate_plan(plan, source_hash=None):
    """Validate clocks, ordering, uniqueness and declared native membership once."""
    errors, previous, seen = [], -1, set()
    for n, item in enumerate(plan):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            errors.append(f"plan[{n}]: expected [known_at,gaps,bars]"); continue
        known, gaps, bars = item
        if not _integer(known) or known <= previous:
            errors.append(f"plan[{n}]: nonincreasing/invalid known_at")
        if _integer(known): previous = known
        def valid_gap(x):
            if isinstance(x,str): return bool(x)
            return (isinstance(x,dict) and x.get('kind') == 'SCHEDULED_COVERAGE_BRIDGE'
                    and _integer(x.get('start')) and x.get('end_exclusive') == known
                    and x['start'] < known and bool(x.get('id')) and bool(x.get('url')))
        if not isinstance(gaps, (list, tuple)) or any(not valid_gap(x) for x in gaps):
            errors.append(f"plan[{n}]: invalid gap reasons")
        if not isinstance(bars, (list, tuple)):
            errors.append(f"plan[{n}]: invalid bars"); continue
        ranks = []
        for pair in bars:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                errors.append(f"plan[{n}]: invalid native pair"); continue
            tf, bar = pair
            if type(tf) is not int or tf not in TFS or not isinstance(bar, dict):
                errors.append(f"plan[{n}]: invalid native timeframe/bar"); continue
            ranks.append(TFS.index(tf)); start = bar.get("time")
            if not _integer(start) or start % (tf * 60) or bar.get("known_at") != known or known != start + tf * 60:
                errors.append(f"plan[{n}]: native clock/phase mismatch {tf}"); continue
            key = (tf, start)
            if key in seen: errors.append(f"plan[{n}]: duplicate native identity {key}")
            seen.add(key)
            if bar.get("complete") is not True or not isinstance(bar.get("source_id"), str) or not bar["source_id"]:
                errors.append(f"plan[{n}]: incomplete/unidentified native bar")
            if source_hash and bar.get("source_id") != f"{source_hash}:{tf}:{start}":
                errors.append(f"plan[{n}]: source/native identity mismatch")
            values = [bar.get(k) for k in ("open", "high", "low", "close")]
            if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in values):
                errors.append(f"plan[{n}]: invalid price")
            elif not values[2] <= min(values[0], values[3]) <= max(values[0], values[3]) <= values[1]:
                errors.append(f"plan[{n}]: invalid OHLC")
            members = bar.get("member_times")
            if members is not None and (not isinstance(members, list) or not members or
                    any(not _integer(t) or t % 60 or not start <= t < known for t in members) or
                    members != sorted(set(members))):
                errors.append(f"plan[{n}]: invalid/future native members")
        if ranks != sorted(set(ranks)):
            errors.append(f"plan[{n}]: duplicate/out-of-order same-cutoff native timeframe")
    return errors


def validate_input_audit(plan, events, rows):
    errors, head = [], "0" * 64
    by_clock = {}
    for e in events: by_clock.setdefault(e.get("known_at"), []).append(e.get("id"))
    if len(rows) != len(plan): errors.append("input-audit: plan/record count differs")
    for n, ((known, gaps, bars), row) in enumerate(zip(plan, rows)):
        expected = dict(known_at=known, previous_hash=head,
                        input_sha256=digest([gaps, bars]), event_ids=by_clock.pop(known, []))
        head = digest(expected); expected["record_hash"] = head
        if canonical(row) != canonical(expected): errors.append(f"input-audit[{n}]: input/event/hash-chain mismatch")
    if by_clock: errors.append("input-audit: event has no corresponding input action")
    return errors, head


def validate_events(events, spec, plan):
    """Independent ledger checks; no reconstructed teacher labels or chosen outcomes."""
    errors, event_ids, previous = [], set(), -1
    refs = {k: {} for k in ("authority", "bos", "zone", "link", "tap", "child")}
    pivots, returns, respects = {}, {}, {}
    native = {(tf, b["time"]): b for _, _, bs in plan for tf, b in bs}
    clocks = {known for known, _, _ in plan}

    def require(ok, n, message):
        if not ok: errors.append(f"event[{n}]: {message}")

    def register(kind, ident, e, n, allow_repeat=False):
        if not isinstance(ident, str) or not ident:
            require(False, n, f"missing {kind} identity"); return
        old = refs[kind].get(ident)
        require(not old or (allow_repeat and old["direction"] == e["direction"] and old["timeframe"] == e["timeframe"]), n, f"conflicting/reused {kind} identity")
        if old is None: refs[kind][ident] = e

    def bound(kind, ident, e, n, by_start=False, opposite=False):
        old = refs[kind].get(ident)
        require(old is not None, n, f"unknown {kind} identity {ident}")
        if old:
            cutoff = e.get("bar_start", e["known_at"]) if by_start else e["known_at"]
            require(old["known_at"] <= cutoff, n, f"future {kind} binding")
            if e["direction"] is not None and old["direction"] is not None:
                require((old["direction"] != e["direction"]) if opposite else (old["direction"] == e["direction"]), n, f"{kind} direction mismatch")
        return old

    def walk(value, n, cutoff, path=""):
        if isinstance(value, dict):
            if "known_at" in value:
                k = value["known_at"]
                require(_integer(k) and k <= cutoff, n, f"unknown/future nested clock {path}.known_at")
                if "time" in value:
                    require(_integer(value["time"]) and _integer(k) and value["time"] < k, n, f"actual/known clock inversion {path}")
            for key, child in value.items(): walk(child, n, cutoff, f"{path}.{key}")
        elif isinstance(value, list):
            for j, child in enumerate(value): walk(child, n, cutoff, f"{path}[{j}]")

    for n, e in enumerate(events):
        if not isinstance(e, dict): require(False, n, "invalid event"); continue
        known, kind, tf, direction = (e.get(k) for k in ("known_at", "kind", "timeframe", "direction"))
        require(_integer(known) and known >= previous and known in clocks, n, "invalid/unknown/nonmonotone event clock")
        if not _integer(known): continue
        previous = known
        require(isinstance(kind, str) and bool(kind), n, "missing kind")
        require(tf is None or (type(tf) is int and tf in TFS), n, "invalid timeframe")
        require(direction in (None, "long", "short"), n, "invalid direction")
        require(e.get("id") not in event_ids, n, "duplicate event id")
        event_ids.add(e.get("id"))
        require(isinstance(kind, str) and e.get("id") == event_id(e), n, "event id does not bind exact payload")
        require(e.get("spec_hash") == digest(spec), n, "spec identity mismatch")
        require(e.get("authority") == AUTHORITY and e.get("execution_allowed") is False and e.get("teacher_verified") is False, n, "unsupported permission/provenance claim")
        walk(e, n, known)
        start = e.get("bar_start")
        if start is not None:
            require(_integer(start) and tf in TFS and known == start + tf * 60, n, "event native actual/known mismatch")
            b = native.get((tf, start))
            require(b is not None and e.get("source_id") == b["source_id"], n, "event not bound to exact native bar")
            if tf == 1: require(e.get("htf_context_as_of") == start, n, "1M context clock missing/wrong")
        if is_decision(e):
            require(direction in ("long", "short"), n, "decision has no direction")
        if kind == 'PIVOT_CONFIRMED':
            p = e.get('pivot',{})
            key = (tf,p.get('id'))
            require(key not in pivots, n, 'reused pivot identity')
            require(p.get('known_at') == known, n, 'pivot confirmation clock mismatch')
            origin = native.get((tf,p.get('time')))
            require(origin is not None and p.get('source_id') == origin['source_id'] and
                    p.get('kind') in ('high','low') and p.get('price') == origin.get(p.get('kind')), n, 'pivot actual native source/price mismatch')
            pivots[key] = p
        if kind in ("MAJOR_BOS", "M1_TRANSITION_BOS", "CHILD_CANDIDATE"):
            for field in ("reference", "pullback"):
                p = e.get(field, {})
                require(isinstance(p, dict) and _integer(p.get("known_at")) and start is not None and p["known_at"] < start, n, f"{field} unavailable before BOS bar")
                require(pivots.get((tf,p.get('id'))) == p, n, f'{field} differs from the previously confirmed pivot')
            ref, pull = e.get("reference", {}), e.get("pullback", {})
            require(ref.get("kind") == ("high" if direction == "long" else "low"), n, "reference direction/kind mismatch")
            require(pull.get("kind") == ("low" if direction == "long" else "high"), n, "pullback direction/kind mismatch")
            require(type(ref.get("time")) is int and type(pull.get("time")) is int and ref["time"] < pull["time"], n, "reference/pullback chronology")
        if kind == "MAJOR_BOS":
            require(tf in (240, 60), n, "major BOS uses wrong scale")
            register("authority", e.get("authority_id"), e, n, allow_repeat=True)
            register("bos", e.get("bos_id"), e, n)
        elif kind == "ZONE_CANDIDATE":
            bound("authority", e.get("authority_id"), e, n); bound("bos", e.get("bos_id"), e, n)
            register("zone", e.get("zone_id"), e, n)
        elif kind == "HTF_LINK":
            for field, expected_tf in (("h4_authority_id", 240), ("h1_authority_id", 60)):
                a = bound("authority", e.get(field), e, n)
                require(a is not None and a["timeframe"] == expected_tf, n, "HTF link owner scale mismatch")
            register("link", e.get("link_id"), e, n)
        elif kind == "HTF_TAP":
            z = bound("zone", e.get("zone_id"), e, n, True)
            bound("link", e.get("link_id"), e, n, True)
            require(z is not None and e.get("owner_timeframe") == z["timeframe"], n, "tap parent scale mismatch")
            register("tap", e.get("tap_id"), e, n)
        elif kind == "M1_TRANSITION_BOS":
            tap = bound("tap", e.get("tap_id"), e, n, True)
            bound("link", e.get("link_id"), e, n, True)
            require(tap is not None and tap["link_id"] == e.get("link_id"), n, "transition tap/link mismatch")
            require(_integer(e.get("structure_known_at")) and e["structure_known_at"] < start, n, "future structure clock")
            for chain in e.get("comparison_chains", {}).values():
                require(len(chain) >= 3 and tap is not None and all(p["time"] > tap["bar_start"] for p in chain[-2:]), n, "post-tap pivot chronology")
            register("bos", e.get("bos_id"), e, n)
        elif kind == "CHILD_CANDIDATE":
            parent = bound("zone", e.get("parent_zone_id"), e, n, True)
            tap = bound("tap", e.get("tap_id"), e, n, True)
            bos = bound("bos", e.get("bos_id"), e, n)
            bound("link", e.get("link_id"), e, n, True)
            require(tap is not None and tap["zone_id"] == e.get("parent_zone_id") and tap["link_id"] == e.get("link_id"), n, "child tap/parent/link mismatch")
            require(bos is not None and bos["timeframe"] == 1 and bos["known_at"] == known, n, "child BOS binding mismatch")
            require(e.get("zone_id") == e.get("child_id") and parent is not None, n, "child/zone identity mismatch")
            register("child", e.get("child_id"), e, n)
            register("zone", e.get("zone_id"), e, n)
            for zone in e.get("opposing_known_zones", []):
                bound("zone", zone.get("zone_id"), e, n, True, True)
        elif e.get("child_id"):
            child = bound("child", e.get("child_id"), e, n)
            if child:
                require(e.get("zone_id") == e["child_id"], n, "lifecycle zone identity mismatch")
                if kind in ("RETURN_OBSERVED", "RESPECT_OBSERVED", "ENTRY_REVIEW", "ENTRY_REVIEW_BLOCKED"):
                    require(start is not None and child["known_at"] <= start, n, "lifecycle predates child selection")
                for key in ("zone_low", "zone_high", "parent_zone_id"):
                    if key in e: require(e[key] == child[key], n, f"lifecycle changed frozen {key}")
            if kind == 'RETURN_OBSERVED':
                require(e['child_id'] not in returns,n,'duplicate return for child')
                returns[e['child_id']] = e
            elif kind == 'RESPECT_OBSERVED':
                prior = returns.get(e['child_id'])
                require(prior is not None and start is not None and prior['known_at'] <= start,n,'respect lacks earlier separate return')
                require(e['child_id'] not in respects,n,'duplicate respect for child')
                respects[e['child_id']] = e
            elif kind in ('ENTRY_REVIEW','ENTRY_REVIEW_BLOCKED'):
                prior = respects.get(e['child_id'])
                require(prior is not None and prior['known_at'] == known,n,'entry review lacks same-cutoff respect')
        if kind in ("ZONE_CANDIDATE", "CHILD_CANDIDATE"):
            members = e.get("member_times", [])
            require(bool(members) and all(type(t) is int for t in members) and members == sorted(set(members)), n, "invalid origin membership")
            require(e.get("selected_known_at") == known, n, "selection backdated")
            if members and tf in TFS:
                require(e.get("origin_start") == members[0] and e.get("origin_end") == members[-1], n, "origin range/member mismatch")
                require(all((tf, t) in native and t + tf*60 <= start for t in members), n, "origin contains departure/future/missing native candle")
                source_ids = e.get("source_ids")
                require(source_ids == [native[(tf,t)]["source_id"] for t in members if (tf,t) in native], n, "origin source identities mismatch")
        if e.get("target_known") is True:
            target = e.get("target")
            require(isinstance(target, dict) and start is not None and _integer(target.get("known_at")) and target["known_at"] <= start, n, "target unavailable at deciding bar start")
        elif "target_known" in e:
            require(e.get("target_known") is False and e.get("target") is None, n, "inconsistent target availability")
    return errors


def first_difference(expected, actual):
    for n, (a, b) in enumerate(zip(expected, actual)):
        if canonical(a) != canonical(b):
            return dict(index=n, expected_id=a.get("id"), actual_id=b.get("id"),
                        expected_kind=a.get("kind"), actual_kind=b.get("kind"),
                        expected_sha256=digest(a), actual_sha256=digest(b))
    if len(expected) != len(actual): return dict(index=min(len(expected),len(actual)), expected_count=len(expected), actual_count=len(actual))
    return None


def clocks(t):
    return {label:datetime.fromtimestamp(t,ZoneInfo(zone)).isoformat()
            for label,zone in [('UTC','UTC'),('CT','America/Chicago'),('ET','America/New_York')]}


def decision_clock_report(events):
    """Actual origin/pivot clocks stay separate from later recognition clocks."""
    zones = {e['zone_id']:e for e in events if e['kind'] in ('ZONE_CANDIDATE','CHILD_CANDIDATE')}
    children = {e['child_id']:e for e in events if e['kind']=='CHILD_CANDIDATE'}
    taps = {e['tap_id']:e for e in events if e['kind']=='HTF_TAP'}
    result = []
    for e in events:
        if not is_decision(e): continue
        row = {k:e.get(k) for k in ('id','kind','timeframe','direction','bar_start','known_at','htf_context_as_of')}
        row['known_clocks'] = clocks(e['known_at'])
        local = children.get(e.get('child_id'),e)
        for field in ('reference','pullback'):
            pivot = local.get(field)
            if pivot: row[field] = {k:pivot.get(k) for k in ('id','kind','time','known_at','source_id')}
        parent = zones.get(local.get('parent_zone_id'))
        if parent is None and local.get('tap_id') in taps:
            parent = zones.get(taps[local['tap_id']]['zone_id'])
        if parent:
            row['parent'] = {k:parent.get(k) for k in ('zone_id','timeframe','direction','member_times','origin_start','origin_end','selected_known_at')}
        if local.get('child_id'):
            row['child'] = {k:local.get(k) for k in ('child_id','timeframe','direction','member_times','origin_start','origin_end','selected_known_at')}
        result.append(row)
    return result


def audit(engine_type, replay_fn, spec, plan, recorded_events, *, input_audit=None,
          source_rows=None, assemble_fn=None, source_hash=None, start=None, end=None, progress=None):
    """Never resumes state: full replay and each unique decision cutoff start empty.

    Passing both source_rows and assemble_fn enables cold native reaggregation at
    every cutoff. Supplying only plan intentionally proves the engine boundary,
    not source resampling. All comparisons include every prior event, not only the
    decision row. No state cache or per-minute engine.events/snapshot access.
    """
    began = time.monotonic(); failures = []
    original_plan_hash = digest(plan)
    errors = validate_plan(plan, source_hash)
    if not errors: errors += validate_events(recorded_events, spec, plan)
    input_head = None
    if input_audit is not None:
        audit_errors, input_head = validate_input_audit(plan, recorded_events, input_audit)
        errors += audit_errors
    native_mode = source_rows is not None and assemble_fn is not None
    if (source_rows is None) != (assemble_fn is None): errors.append("native reaggregation requires both source rows and captured assemble")
    if native_mode and (not source_hash or not _integer(start) or start % 14400): errors.append("native reaggregation needs bound source hash and UTC4H start")
    if errors:
        return dict(status="REFUTED", failures=[dict(gate="INPUT_EVENT_LEDGER", errors=errors)],
                    required_decisions=sum(is_decision(e) for e in recorded_events), audited_cutoffs=0,
                    teacher_fidelity=False, execution_allowed=False, performance=None)
    knowns = [row[0] for row in plan]
    decisions = [e for e in recorded_events if is_decision(e)]
    cutoffs = sorted({e["known_at"] for e in decisions})
    if native_mode:
        full_end = end if end is not None else ((plan[-1][0]+14399)//14400)*14400
        if not _integer(full_end) or full_end % 14400 or full_end <= start:
            raise ValueError('native audit end must be an increasing UTC4H boundary')
        regenerated_full,_ = assemble_fn(source_rows,start,full_end,source_hash)
        if canonical(regenerated_full) != canonical(plan):
            failures.append(dict(gate='FULL_NATIVE_REAGGREGATION',
                                 expected_plan_sha256=digest(plan),actual_plan_sha256=digest(regenerated_full)))
    full_events, _, _ = replay_fn(engine_type, spec, plan, out=None)
    difference = first_difference(recorded_events, full_events)
    if difference: failures.append(dict(gate="RECORDED_FULL_REPLAY", difference=difference))
    if digest(plan) != original_plan_hash:
        return dict(status="REFUTED", failures=[dict(gate="INPUT_MUTATED_DURING_REPLAY")], required_decisions=len(decisions), audited_cutoffs=0, teacher_fidelity=False, execution_allowed=False, performance=None)
    event_clocks = [e["known_at"] for e in recorded_events]
    results = []
    for index, cutoff in enumerate(cutoffs):
        before = time.monotonic()
        sealed_prefix = plan[:bisect.bisect_right(knowns, cutoff)]
        regenerated = None
        if native_mode:
            # Cut RAW M1 before aggregation. Enclosing end may create later empty
            # buckets, but none are supplied to the detector or compared as known.
            raw_prefix = {t: b for t,b in source_rows.items() if t + 60 <= cutoff}
            enclosing_end = ((cutoff + 14399) // 14400) * 14400
            regenerated, _ = assemble_fn(raw_prefix, start, enclosing_end, source_hash)
            regenerated = [row for row in regenerated if row[0] <= cutoff]
            if canonical(regenerated) != canonical(sealed_prefix):
                failures.append(dict(gate="NATIVE_PREFIX_REAGGREGATION", cutoff=cutoff,
                                     expected_plan_sha256=digest(sealed_prefix), actual_plan_sha256=digest(regenerated)))
        prefix_plan = regenerated if native_mode else sealed_prefix
        actual, _, _ = replay_fn(engine_type, spec, prefix_plan, out=None)
        expected = recorded_events[:bisect.bisect_right(event_clocks, cutoff)]
        difference = first_difference(expected, actual)
        if difference: failures.append(dict(gate="COLD_PREFIX", cutoff=cutoff, difference=difference))
        row = dict(cutoff=cutoff, decision_ids=[e["id"] for e in decisions if e["known_at"] == cutoff],
                   input_actions=len(prefix_plan), compared_prior_events=len(expected),
                   exact_event_match=difference is None, expected_event_sha256=digest(expected),
                   actual_event_sha256=digest(actual), native_reaggregated=native_mode,
                   elapsed_seconds=round(time.monotonic()-before,6))
        results.append(row)
        if progress: progress(index+1,len(cutoffs),row)
    if digest(plan) != original_plan_hash: failures.append(dict(gate="INPUT_MUTATED_DURING_REPLAY"))
    if not decisions: failures.append(dict(gate="NO_REQUIRED_DECISIONS", detail="No causal decision claim is established by an empty sample."))
    isolated=isinstance(getattr(replay_fn,'__self__',None),FreshCapturedRuntime)
    return dict(status="REFUTED" if failures else ("SURVIVES_PROVISIONAL_CAUSAL_PREFIX_ONLY" if isolated else "SURVIVES_PROVISIONAL_CALLER_REPLAY_ONLY"),
                scope="RAW_M1_REAGGREGATION_AND_ENGINE" if native_mode else "SEALED_NATIVE_PLAN_ENGINE_ONLY",
                runtime_isolation='FRESH_CAPTURED_PROCESS' if isolated else 'CALLER_SUPPLIED_REPLAY_NO_ISOLATION_GUARANTEE',
                required_decisions=len(decisions), required_unique_cutoffs=len(cutoffs), audited_cutoffs=len(results),
                passing_cutoffs=sum(r["exact_event_match"] for r in results), cutoffs=results, failures=failures,
                decision_clocks=decision_clock_report(recorded_events),
                sealed_plan_sha256=original_plan_hash, recorded_events_sha256=digest(recorded_events),
                input_audit_head=input_head, input_audit_checked=input_audit is not None,
                full_native_reaggregation_checked=native_mode,
                elapsed_seconds=round(time.monotonic()-began,6), teacher_fidelity=False,
                execution_allowed=False, performance=None)


def no_op_replay(replay_fn):
    def wrapped(engine_type,spec,plan,out=None):
        return replay_fn(engine_type,spec,plan,out=out)
    return wrapped


def deliberately_future_reading(replay_fn):
    """AUDIT POSITIVE CONTROL ONLY. Reads last provided close for an earlier BOS.

    IDs are rehashed so payload integrity alone cannot detect the cheating. The
    full baseline must itself be generated with this wrapper; prefix reruns expose
    the change. Never writes/patches the production engine or original journal.
    """
    def wrapped(engine_type,spec,plan,out=None):
        if out is not None: raise ValueError("control cannot write production capture")
        events,snapshot,head = replay_fn(engine_type,spec,plan,out=None)
        last_close = next((b["close"] for _,_,bs in reversed(plan) for _,b in reversed(bs)), None)
        events = copy.deepcopy(events)
        for e in events:
            if is_decision(e):
                e["DELIBERATE_FUTURE_CLOSE_CONTROL"] = dict(last_close=last_close,last_known_at=plan[-1][0])
                e["id"] = event_id(e)
        return events,snapshot,head
    return wrapped


def run_controls(engine_type,replay_fn,spec,plan):
    baseline,_,_ = replay_fn(engine_type,spec,plan,out=None)
    noop = audit(engine_type,no_op_replay(replay_fn),spec,plan,baseline)
    broken = deliberately_future_reading(replay_fn)
    poisoned,_,_ = broken(engine_type,spec,plan,out=None)
    positive = audit(engine_type,broken,spec,plan,poisoned)
    caught = [f for f in positive["failures"] if f["gate"] == "COLD_PREFIX"]
    return dict(no_op_passed=not noop["failures"],
                deliberately_future_reading_detected=bool(caught),
                positive_control_mismatching_cutoffs=len(caught),
                valid=not noop["failures"] and bool(caught),
                no_op=noop, deliberately_future_reading=positive)


def bounded_controls(engine_type,replay_fn,spec,plan,recorded_events):
    """Real-run harness control at first decision with a later input action.

    Replays full input and that prefix twice (no-op and deliberate cheat). The
    main audit still reruns EVERY required cutoff. This deterministic control
    chooses no market example, profitable route or outcome.
    """
    cutoffs=sorted({e['known_at'] for e in recorded_events if is_decision(e) and e['known_at'] < plan[-1][0]}) if plan else []
    if not cutoffs:
        return dict(valid=False,reason='NO_DECISION_WITH_FUTURE_SUFFIX_FOR_POSITIVE_CONTROL')
    cutoff=cutoffs[0]; prefix=[row for row in plan if row[0] <= cutoff]
    expected=[e for e in recorded_events if e['known_at'] <= cutoff]
    noop=no_op_replay(replay_fn)
    noop_full,_,_=noop(engine_type,spec,plan,out=None)
    noop_prefix,_,_=noop(engine_type,spec,prefix,out=None)
    broken=deliberately_future_reading(replay_fn)
    poisoned_full,_,_=broken(engine_type,spec,plan,out=None)
    poisoned_prefix,_,_=broken(engine_type,spec,prefix,out=None)
    poisoned_prior=[e for e in poisoned_full if e['known_at'] <= cutoff]
    difference=first_difference(poisoned_prior,poisoned_prefix)
    noop_pass=first_difference(recorded_events,noop_full) is None and first_difference(expected,noop_prefix) is None
    return dict(valid=noop_pass and difference is not None,no_op_passed=noop_pass,
                deliberately_future_reading_detected=difference is not None,
                chosen_cutoff=cutoff,choice='EARLIEST_REQUIRED_DECISION_WITH_LATER_INPUT',
                compared_prior_events=len(expected),positive_control_difference=difference,
                mechanism='Last provided close and its known-time are illicitly attached to earlier decisions; event IDs are rehashed.')


def load_module(path):
    data = Path(path).read_bytes()
    namespace = {"__name__": "captured_for_cold_audit", "__file__": str(Path(path).resolve())}
    exec(compile(data,str(path),"exec"),namespace)
    return namespace


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def parse_native_csv(data):
    """CSV boundary in trusted verifier code; never executes captured read_csv."""
    rows,previous={},None
    for row in csv.DictReader(io.StringIO(data.decode())):
        t=int(row.get('epoch',row.get('time','')))
        if t < 0 or t % 60 or (previous is not None and t <= previous):
            raise ValueError('Non-increasing/duplicate/off-grid native minute')
        values={k:float(row[k]) for k in ('open','high','low','close')}
        if not all(math.isfinite(x) and x > 0 for x in values.values()): raise ValueError('Invalid native price')
        if not values['low'] <= min(values['open'],values['close']) <= max(values['open'],values['close']) <= values['high']:
            raise ValueError('Invalid native OHLC')
        rows[t]=dict(time=t,**values);previous=t
    return rows


class FreshCapturedRuntime:
    """No captured Python executes in the coordinating verifier process."""
    def __init__(self,files):
        self.worker=Path(__file__).with_name('cold_runtime_worker.py')
        self.worker_hash=byte_hash(self.worker)
        self.base={}
        for key in ('engine','runner'):
            binding=files[key];data=Path(binding['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != binding['sha256']: raise ValueError('changed captured '+key)
            self.base[key+'_source']=data.decode()
            self.base[key+'_filename']=binding['path']

    def call(self,**payload):
        if byte_hash(self.worker) != self.worker_hash: raise ValueError('cold runtime worker changed')
        request=dict(self.base,**payload)
        # -I removes caller PYTHONPATH, user site and current-directory imports.
        # One process per invocation also clears imported-module/global caches.
        env={'LANG':'C','LC_ALL':'C','TZ':'UTC'}
        process=subprocess.run([sys.executable,'-I','-S','-B',str(self.worker)],
            input=canonical(request),text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            cwd=str(self.worker.parent),env=env,close_fds=True)
        try: result=json.loads(process.stdout)
        except Exception as exc: raise RuntimeError('COLD_RUNTIME_INVALID_RESPONSE: '+process.stderr[:1000]) from exc
        if process.returncode or not result.get('ok'):
            raise RuntimeError('COLD_RUNTIME_REJECTED: '+str(result.get('error_type'))+': '+str(result.get('detail')))
        return result

    def replay(self,engine_type,spec,plan,out=None):
        if out is not None: raise ValueError('cold verifier never writes original capture')
        result=self.call(mode='replay',spec=spec,plan=plan)
        return result['events'],result['snapshot'],result['head']

    def assemble(self,rows,start,end,source_hash):
        result=self.call(mode='assemble',rows=list(rows.values()),start=start,end=end,source_hash=source_hash)
        return result['plan'],result['coverage']


def load_capture(path):
    """Byte bindings verified before executing any captured code or parsing source."""
    capture = json.loads(Path(path).read_text())
    if capture.get("schema") != "CAUSAL_AUDIT_CAPTURE_V1": raise ValueError("capture schema")
    required = {"engine","runner","spec","plan","events","input_audit"}
    files = capture.get("files",{})
    if not required <= files.keys(): raise ValueError("missing capture files")
    for name, entry in files.items():
        if not isinstance(entry,dict) or set(entry) != {"path","sha256"}: raise ValueError(f"invalid file binding {name}")
        if byte_hash(entry["path"]) != entry["sha256"]: raise ValueError(f"capture bytes changed: {name}")
    return capture


def audit_capture(path, progress=None):
    capture_hash=byte_hash(path)
    capture = load_capture(path); files = capture["files"]
    runtime=FreshCapturedRuntime(files)
    spec = json.loads(Path(files["spec"]["path"]).read_text())
    plan = read_jsonl(files["plan"]["path"])
    events = read_jsonl(files["events"]["path"])
    inputs = read_jsonl(files["input_audit"]["path"])
    kwargs = {}
    if "source" in files:
        source_data=Path(files['source']['path']).read_bytes()
        if hashlib.sha256(source_data).hexdigest() != files['source']['sha256']: raise ValueError('changed captured source')
        kwargs = dict(source_rows=parse_native_csv(source_data),
                      assemble_fn=runtime.assemble, source_hash=files["source"]["sha256"],
                      start=capture["start"],end=capture.get('end'))
    result = audit(None,runtime.replay,spec,plan,events,input_audit=inputs,progress=progress,**kwargs)
    if not result['failures']:
        result['controls'] = bounded_controls(None,runtime.replay,spec,plan,events)
        if not result['controls']['valid']:
            result['failures'].append(dict(gate='AUDIT_CONTROLS',detail=result['controls']))
            result['status']='REFUTED'
    result["capture_sha256"] = capture_hash
    result["file_bindings"] = files
    result['runtime_isolation']='FRESH_PYTHON_PROCESS_PER_REPLAY_AND_ASSEMBLY_PREFIX_INPUTS_ONLY'
    result['cold_runtime_worker_sha256']=runtime.worker_hash
    result["trust_boundary"] = "Caller-supplied local capture; hashes bind bytes, not provider truth, identity authentication, or teacher fidelity."
    # Recheck after the potentially long run; no silent success over changing files.
    if byte_hash(path) != capture_hash: raise ValueError('capture manifest changed during audit')
    load_capture(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture",required=True); parser.add_argument("--out",required=True)
    args = parser.parse_args()
    out = Path(args.out); out.mkdir(parents=True,exist_ok=False)
    def progress(n,total,row):
        print(canonical(dict(completed=n,total=total,cutoff=row["cutoff"],exact_event_match=row["exact_event_match"])),flush=True)
    try:
        result = audit_capture(args.capture,progress)
    except Exception as exc:
        result = dict(status="REFUTED",failures=[dict(gate="CAPTURE_OR_AUDIT_EXCEPTION",type=type(exc).__name__,detail=str(exc))],teacher_fidelity=False,execution_allowed=False,performance=None)
    (out/"RESULT.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    print(canonical({k:result[k] for k in ("status","failures","audited_cutoffs") if k in result}),flush=True)
    return 0 if result["status"] == "SURVIVES_PROVISIONAL_CAUSAL_PREFIX_ONLY" else 1


if __name__ == "__main__": raise SystemExit(main())
