"""Research replay runner. Strict observed-minute coverage; no prices forward-filled.

The provider calendar is an explicit research assumption, not broker equivalence.
Incomplete data can be inspected only with --diagnostic; never a full-month claim.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TF = (240, 60, 1)
CALENDAR_ID = 'DUKASCOPY_CURRENT_SUMMER_HOURS_HISTORICAL_APPLICABILITY_ASSUMED_V1'
CALENDAR_URL = 'https://www.dukascopy.com/swiss/english/cfd/range-of-markets/'
UNKNOWN_DAYS = {'2026-09-07'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)


def epoch(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())


def clocks(t):
    return {k: datetime.fromtimestamp(t, ZoneInfo(z)).isoformat() for k, z in
            [('UTC', 'UTC'), ('CT', 'America/Chicago'), ('ET', 'America/New_York')]}


def session(t):
    """None means unresolved holiday schedule; False is documented regular closure."""
    d = datetime.fromtimestamp(t, timezone.utc)
    if d.date().isoformat() in UNKNOWN_DAYS:
        return None
    minute = d.hour * 60 + d.minute
    if d.weekday() == 5:
        return False
    if d.weekday() == 6:
        return minute >= 1320
    if d.weekday() == 4:
        return minute < 1215
    return minute < 1215 or minute >= 1320


def read_csv(path):
    return parse_csv(Path(path).read_bytes())


def parse_csv(data):
    """Parse exactly the captured bytes whose hash is bound to the receipt."""
    rows, previous = {}, None
    with io.StringIO(data.decode('utf-8'), newline='') as f:
        for row in csv.DictReader(f):
            t = int(row.get('epoch', row.get('time', '')))
            if t < 0 or t % 60 or (previous is not None and t <= previous):
                raise ValueError('Non-increasing/duplicate/off-grid minute')
            values = {k: float(row[k]) for k in ('open', 'high', 'low', 'close')}
            if not all(math.isfinite(v) and v > 0 for v in values.values()):
                raise ValueError('Non-finite or nonpositive prices')
            if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
                raise ValueError('Invalid OHLC bounds')
            rows[t] = dict(time=t, **values)
            previous = t
    return rows


def accounted_gap(start, end, native, resets, closures):
    """Construct a past-only partition from already delivered plan facts.

    Unknown minutes stay unknown. This evidence only prevents reapplying a
    prior reset when a delayed aggregate resumes after the same missing data.
    Return None unless every omitted minute has evidence.
    """
    proof = dict(start=start, end_exclusive=end, observed_minutes=[],
                 unknown_intervals=[], scheduled_intervals=[])
    t = start
    while t < end:
        if t in native:
            proof['observed_minutes'].append(t); t += 60; continue
        closure = next(((a,z) for a,z in closures if a <= t < z), None)
        if closure:
            stop = min(end, closure[1])
            proof['scheduled_intervals'].append(dict(start=t, end_exclusive=stop))
            t = stop; continue
        reason = resets.get(t+60)
        if reason is None:
            return None
        stop = t+60
        while (stop < end and stop not in native and stop+60 not in resets and
               not any(a <= stop < z for a,z in closures)):
            stop += 60
        proof['unknown_intervals'].append(dict(start=t,end_exclusive=stop,
            reason=reason,reset_known_at=t+60))
        t = stop
    return proof if proof['unknown_intervals'] else None


def assemble(rows, start, end, source_hash):
    """Materialized causal input plan. Each aggregate only reads its own closed interval.

    Missing data is delivered as a reset at its first unavailable minute, not early.
    Calendar/coverage metadata never selects price geometry or a winning branch.
    """
    if start % 14400 or end % 14400 or end <= start:
        raise ValueError('Study boundaries must be increasing UTC 4H grid points')
    actions, coverage = defaultdict(list), []
    state, closed_start = None, None
    for t in range(start, end, 60):
        expected = session(t)
        reason = ('UNRESOLVED_HOLIDAY_SCHEDULE' if expected is None else
                  'QUOTE_DURING_DECLARED_CLOSURE' if expected is False and t in rows else
                  'MISSING_ACTIVE_MINUTE' if expected is True and t not in rows else None)
        if reason is not None and reason != state:
            actions[t + 60].append(('gap', reason))
        if expected is False and reason is None:
            if closed_start is None:
                closed_start = t
        else:
            if expected is True and closed_start is not None:
                actions[t].append(('gap', dict(kind='SCHEDULED_COVERAGE_BRIDGE',
                    start=closed_start, end_exclusive=t, id=CALENDAR_ID, url=CALENDAR_URL)))
            closed_start = None
        state = reason
    for tf in TF:
        step = tf * 60
        previous_end = None
        for t in range(start, end, step):
            grid = list(range(t, t + step, 60))
            unknown = [m for m in grid if session(m) is None]
            expected = [m for m in grid if session(m) is True]
            actual = [m for m in grid if m in rows]
            missing = [m for m in expected if m not in rows]
            outside = [m for m in actual if session(m) is False]
            status = ('UNKNOWN_CALENDAR' if unknown else 'SCHEDULE_CONTRADICTION' if outside else
                      'INCOMPLETE' if missing else 'CLOSED' if not expected else 'COMPLETE')
            coverage.append(dict(timeframe=tf, time=t, known_at=t+step, status=status,
                                 expected_minutes=len(expected), observed_minutes=len(actual),
                                 missing_minutes=len(missing), closure_quote_minutes=len(outside)))
            if status != 'COMPLETE':
                continue
            bs = [rows[m] for m in actual]
            bar = dict(time=t, known_at=t+step, open=bs[0]['open'], high=max(b['high'] for b in bs),
                       low=min(b['low'] for b in bs), close=bs[-1]['close'], complete=True,
                       source_id=f'{source_hash}:{tf}:{t}', member_times=actual,
                       observed_minutes=len(actual), expected_minutes=len(expected),
                       calendar_id=CALENDAR_ID)
            if previous_end is not None and t > previous_end:
                bridge = all(session(m) is False and m not in rows for m in range(previous_end, t, 60))
                bar['gap_before'] = 'scheduled' if bridge else 'unclassified'
                if bridge:
                    bar['schedule_evidence'] = dict(id=CALENDAR_ID, url=CALENDAR_URL,
                                                    start=previous_end, end_exclusive=t)
            previous_end = t + step
            actions[t+step].append(('bar', tf, bar))
    ordered = []
    native, resets, closures, prior = {}, {}, [], {}
    for known in sorted(actions):
        gaps = [a[1] for a in actions[known] if a[0] == 'gap']
        bars = [(a[1], a[2]) for a in actions[known] if a[0] == 'bar']
        for gap in gaps:
            if isinstance(gap, dict):
                closures.append((gap['start'], gap['end_exclusive']))
            else:
                resets[known] = gap
        for tf, bar in sorted(bars, key=lambda pair: TF.index(pair[0])):
            if tf in prior and bar['time'] > prior[tf] and bar.get('gap_before') == 'unclassified':
                proof = accounted_gap(prior[tf], bar['time'], native, resets, closures)
                if proof is not None:
                    bar['gap_evidence'] = proof
            prior[tf] = known
            if tf == 1:
                native[bar['time']] = bar
        ordered.append((known, gaps, sorted(bars, key=lambda pair: TF.index(pair[0]))))
    return ordered, coverage


def load_engine(path, data=None):
    # Compile captured bytes; do not execute stale timestamp bytecode.
    data = Path(path).read_bytes() if data is None else data
    namespace = {'__name__': 'month_engine_captured', '__file__': str(path)}
    exec(compile(data, str(path), 'exec'), namespace)
    return namespace['Engine'], hashlib.sha256(data).hexdigest()


def validate_plan(plan):
    """Reject false clocks, source IDs, members and aggregates before execution.

    A complete higher bar must reconstruct exactly from closed native minutes
    earlier in this same plan. Validation reads no future prices into decisions.
    """
    native, previous, identities = {}, None, set()
    resets, closures, prior = {}, [], {}
    for known, gaps, bars in plan:
        if type(known) is not int or known % 60 or (previous is not None and known <= previous):
            raise ValueError('Plan cutoffs must strictly increase on minute grid')
        previous = known
        for gap in gaps:
            if isinstance(gap, dict):
                start, end = gap.get('start'), gap.get('end_exclusive')
                if (gap.get('kind') != 'SCHEDULED_COVERAGE_BRIDGE' or gap.get('id') != CALENDAR_ID
                    or gap.get('url') != CALENDAR_URL or type(start) is not int or start % 60
                    or end != known or end <= start
                    or any(session(t) is not False for t in range(start, end, 60))):
                    raise ValueError('Unproved scheduled bridge')
                closures.append((start, end))
            elif gap not in ('MISSING_ACTIVE_MINUTE','UNRESOLVED_HOLIDAY_SCHEDULE','QUOTE_DURING_DECLARED_CLOSURE'):
                raise ValueError('Unknown gap classification')
            else:
                resets[known] = gap
        if [tf for tf, _ in bars] != sorted(set(tf for tf, _ in bars), key=TF.index):
            raise ValueError('Nonunique or reversed timeframe order')
        # Same-cutoff M1 is available for aggregate verification, not decisions.
        for tf, b in bars:
            if tf == 1:
                native[b['time']] = b
        for tf, b in bars:
            t, members = b['time'], b.get('member_times')
            if 'gap_evidence' in b:
                if b.get('gap_before') != 'unclassified' or tf not in prior or type(t) is not int or t <= prior[tf]:
                    raise ValueError('Accounted gap lacks prior discontinuity')
                expected_proof = accounted_gap(prior[tf], t, native, resets, closures)
                if expected_proof is None or b['gap_evidence'] != expected_proof:
                    raise ValueError('Accounted gap does not match prior plan evidence')
            prior[tf] = known
            if (type(t) is not int or t % (tf*60) or b.get('known_at') != known
                or known != t+tf*60 or b.get('complete') is not True
                or not isinstance(members, list) or not members
                or any(type(m) is not int or m % 60 or not t <= m < known for m in members)
                or members != sorted(set(members))):
                raise ValueError('Invalid native membership/clock')
            expected = [m for m in range(t, known, 60) if session(m) is True]
            if (members != expected or any(session(m) is None for m in range(t, known, 60))
                or b.get('expected_minutes') != len(expected) or b.get('observed_minutes') != len(members)):
                raise ValueError('Incomplete or falsely completed aggregate')
            identity = b.get('source_id')
            if not isinstance(identity, str) or not identity.endswith(f':{tf}:{t}') or identity in identities:
                raise ValueError('False or reused native source identity')
            identities.add(identity)
            try:
                parts = [native[m] for m in members]
            except KeyError as exc:
                raise ValueError('Aggregate member is absent from observed minute plan') from exc
            prefix = identity.rsplit(':',2)[0]
            if any(p['source_id'] != f'{prefix}:1:{p["time"]}' for p in parts):
                raise ValueError('Aggregate members from different source')
            actual = [parts[0]['open'], max(p['high'] for p in parts), min(p['low'] for p in parts), parts[-1]['close']]
            if actual != [b[k] for k in ('open','high','low','close')]:
                raise ValueError('Aggregate OHLC differs from native members')
    return True


def replay(engine_type, spec, plan, out=None):
    plan = list(plan)
    validate_plan(plan)
    engine = engine_type(spec)
    head = '0' * 64
    journal = (out / 'events.jsonl').open('x') if out else None
    inputs = (out / 'input-audit.jsonl').open('x') if out else None
    try:
        for known, gaps, bars in plan:
            emitted = []
            for reason in gaps:
                if isinstance(reason, dict):
                    emitted.extend(engine.gap(known, 'DOCUMENTED_REGULAR_CLOSURE', timeframes=TF,
                                               scheduled=True, schedule_evidence=reason))
                else:
                    emitted.extend(engine.gap(known, reason, timeframes=TF))
            if bars:
                emitted.extend(engine.feed_batch(bars))
            if journal:
                for event in emitted:
                    journal.write(canon(event)+'\n')
                journal.flush()
                if emitted:
                    os.fsync(journal.fileno())
            if inputs:
                row = dict(known_at=known, previous_hash=head,
                           input_sha256=hashlib.sha256(canon([gaps,bars]).encode()).hexdigest(),
                           event_ids=[e['id'] for e in emitted])
                head = hashlib.sha256(canon(row).encode()).hexdigest()
                row['record_hash'] = head
                inputs.write(canon(row)+'\n')
                inputs.flush()
                if emitted:
                    os.fsync(inputs.fileno())
        return engine.events, engine.snapshot(), head
    finally:
        if journal:
            journal.flush(); os.fsync(journal.fileno())
            journal.close()
        if inputs:
            inputs.flush(); os.fsync(inputs.fileno())
            inputs.close()


def verify_saved(out, events, snapshot, head, plan):
    """Reconcile durable output against executed state before publishing counts."""
    saved = [json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
    inputs = [json.loads(line) for line in (out/'input-audit.jsonl').read_text().splitlines()]
    if saved != events or len(saved) != snapshot['event_count'] or hashlib.sha256(canon(saved).encode()).hexdigest() != snapshot['event_head']:
        raise ValueError('Saved event journal does not match executed state')
    if len(inputs) != len(plan) or len({e['id'] for e in saved}) != len(saved):
        raise ValueError('Input count or event identity mismatch')
    prior, recorded_ids = '0'*64, []
    for record, (known, gaps, bars) in zip(inputs, plan):
        held = dict(record); current = held.pop('record_hash')
        if (held['previous_hash'] != prior or held['known_at'] != known
            or held['input_sha256'] != hashlib.sha256(canon([gaps,bars]).encode()).hexdigest()
            or current != hashlib.sha256(canon(held).encode()).hexdigest()):
            raise ValueError('Saved input audit chain mismatch')
        recorded_ids.extend(held['event_ids']); prior = current
    if prior != head or recorded_ids != [e['id'] for e in saved]:
        raise ValueError('Input audit events/head mismatch')
    return saved


def validate_bindings(captured, paths):
    spec = json.loads(captured['spec']); acquisition = json.loads(captured['acquisition'])
    clarification = json.loads(captured['clarifications'])
    scope = spec['scope']; csv_receipt = acquisition.get('csv', {})
    if clarification.get('spec_sha256') != hashlib.sha256(captured['spec']).hexdigest():
        raise ValueError('Clarifications bind a different specification')
    receipt_path = csv_receipt.get('path')
    if not isinstance(receipt_path, str):
        raise ValueError('Missing acquisition CSV path')
    if ((Path(paths['acquisition']).parent/receipt_path).resolve() != Path(paths['source']).resolve()
        or csv_receipt.get('sha256') != hashlib.sha256(captured['source']).hexdigest()
        or csv_receipt.get('bytes') != len(captured['source'])):
        raise ValueError('Acquisition receipt does not bind captured CSV')
    if (acquisition.get('symbol') != scope['symbol'] or acquisition.get('feed') != scope['provider']
        or acquisition.get('side') != scope['side']
        or epoch(acquisition['start']['utc']) != epoch(scope['warmup_start'])
        or epoch(acquisition['end_exclusive']['utc']) != epoch(scope['end_exclusive'])):
        raise ValueError('Acquisition identity/window mismatch')
    expected = (epoch(scope['end_exclusive'])-epoch(scope['warmup_start']))//3600
    states = acquisition.get('hour_states', {})
    if (not isinstance(states, dict) or any(type(v) is not int or v < 0 for v in states.values())
        or any(type(acquisition.get(k)) is not int or acquisition[k] < 0 for k in ('expected_hours','processed_hours'))):
        raise ValueError('Acquisition counts must be nonnegative integers')
    if acquisition.get('all_hours_validated_or_explicit_empty_200') is True:
        if (acquisition.get('expected_hours') != expected or acquisition.get('processed_hours') != expected
            or sum(states.values()) != expected or any(v for k,v in states.items() if k not in ('OK','EMPTY_200'))
            or csv_receipt.get('incomplete') is not False or acquisition.get('unresolved_hours')
            or acquisition.get('unattempted_hours')):
            raise ValueError('Contradictory full acquisition claim')
    return spec, acquisition


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True); p.add_argument('--acquisition', required=True)
    p.add_argument('--engine', required=True); p.add_argument('--spec', required=True)
    p.add_argument('--clarifications', required=True); p.add_argument('--output', required=True)
    p.add_argument('--diagnostic', action='store_true')
    a = p.parse_args()
    paths = {k: str(Path(getattr(a,k)).resolve()) for k in ('source','acquisition','engine','spec','clarifications')}
    paths['runner'] = str(Path(__file__).resolve())
    captured = {k: Path(path).read_bytes() for k,path in paths.items()}
    spec, acquisition = validate_bindings(captured, paths)
    full = acquisition.get('all_hours_validated_or_explicit_empty_200') is True
    if not full and not a.diagnostic:
        raise SystemExit('BLOCKED_FULL_MONTH_ACQUISITION: use --diagnostic only for an explicitly partial check')
    output = Path(a.output); output.mkdir(exist_ok=False, parents=True)
    captures = output/'captures'; captures.mkdir()
    names = dict(source='source.csv', acquisition='acquisition.json', engine='engine.py',
                 spec='SPEC.json', clarifications='IMPLEMENTATION_CLARIFICATIONS.json', runner='month_replay.py')
    for key, data in captured.items():
        with (captures/names[key]).open('xb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
    cls, engine_hash = load_engine(a.engine, captured['engine'])
    start = epoch(spec['scope']['warmup_start']); end = epoch(spec['scope']['end_exclusive'])
    manifest = dict(status='PROSPECTIVELY_BOUND', created_at=datetime.now(timezone.utc).isoformat(),
                    source_sha256=hashlib.sha256(captured['source']).hexdigest(),
                    acquisition_sha256=hashlib.sha256(captured['acquisition']).hexdigest(), engine_sha256=engine_hash,
                    spec_sha256=hashlib.sha256(captured['spec']).hexdigest(),
                    clarifications_sha256=hashlib.sha256(captured['clarifications']).hexdigest(),
                    runner_sha256=hashlib.sha256(captured['runner']).hexdigest(), full_acquisition=full, diagnostic=a.diagnostic,
                    captures={key:dict(path='captures/'+name,sha256=hashlib.sha256(captured[key]).hexdigest()) for key,name in names.items()},
                    calendar_assumed=CALENDAR_ID, unresolved_calendar_dates=sorted(UNKNOWN_DAYS),
                    start=clocks(start), end_exclusive=clocks(end), execution_allowed=False,
                    teacher_fidelity=False, performance=None)
    (output/'MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    rows = parse_csv(captured['source'])
    if len(rows) != acquisition.get('minute_count') or any(t<start or t>=end for t in rows):
        raise ValueError('Source minute count/window mismatch')
    plan, coverage = assemble(rows,start,end,manifest['source_sha256'])
    with (output/'sealed-plan.jsonl').open('x') as f:
        for action in plan: f.write(canon(action)+'\n')
        f.flush(); os.fsync(f.fileno())
    with (output/'coverage.jsonl').open('x') as f:
        for row in coverage: f.write(canon(row)+'\n')
    events, snapshot, head = replay(cls,spec,plan,output)
    events = verify_saved(output,events,snapshot,head,plan)
    if [json.loads(line) for line in (output/'sealed-plan.jsonl').read_text().splitlines()] != json.loads(canon(plan)):
        raise ValueError('Saved input plan differs from executed plan')
    for key,name in names.items():
        if (captures/name).read_bytes() != captured[key]:
            raise ValueError('Captured dependency changed before publication')
    study_start = epoch(spec['scope']['study_start'])
    month = [e for e in events if study_start < e['known_at'] <= end]
    counts = defaultdict(int)
    for e in month: counts[e['kind']] += 1
    report = dict(status='PARTIAL_DIAGNOSTIC' if not full else 'RESEARCH_SCAN_WITH_COVERAGE_LIMITS',
                  source_minutes=len(rows), september_events=len(month), event_counts=dict(counts),
                  last_input_hash=head, complete_month_claim=False,
                  unknown_holiday='2026-09-07', teacher_fidelity=False, execution_allowed=False, performance=None)
    (output/'RESULT.json').write_text(json.dumps(report,indent=2)+'\n')
    (output/'SNAPSHOT.json').write_text(json.dumps(snapshot,indent=2)+'\n')
    receipt = dict(status='SAVED_OUTPUT_RECONCILED', execution_allowed=False,
                   captured_input_hashes={k:hashlib.sha256(v).hexdigest() for k,v in captured.items()},
                   output_hashes={name:sha(output/name) for name in
                     ('MANIFEST.json','sealed-plan.jsonl','coverage.jsonl','events.jsonl','input-audit.jsonl','RESULT.json','SNAPSHOT.json')})
    with (output/'COMPLETION.json').open('x') as f:
        f.write(json.dumps(receipt,indent=2)+'\n'); f.flush(); os.fsync(f.fileno())
    print(json.dumps(report))


if __name__ == '__main__': main()
