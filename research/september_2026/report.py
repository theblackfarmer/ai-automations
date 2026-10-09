"""Read a sealed research run; render saved interpretations without rerunning the detector.

No network, orders, detector tuning or outcome-ranked selection. The input run is
captured once and reconciled before rendering. This verifies artifact consistency,
not the method, the provider calendar, or teacher fidelity.
"""
import argparse
import csv
import hashlib
import html
import io
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = 'SEPTEMBER_MONTH_REPORT_V1'
FILES = ('MANIFEST.json', 'sealed-plan.jsonl', 'coverage.jsonl', 'events.jsonl',
         'input-audit.jsonl', 'RESULT.json', 'SNAPSHOT.json')
CAPTURES = dict(source='source.csv', acquisition='acquisition.json', engine='engine.py',
                spec='SPEC.json', clarifications='IMPLEMENTATION_CLARIFICATIONS.json',
                runner='month_replay.py')
TF_NAME = {240: '4H', 60: '1H', 1: '1M', None: 'Cross-timeframe'}
OHLC = ('open', 'high', 'low', 'close')


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(x):
    return hashlib.sha256(x if isinstance(x, bytes) else canon(x).encode()).hexdigest()


def epoch(x):
    return int(datetime.fromisoformat(x.replace('Z', '+00:00')).timestamp())


def clocks(t):
    return {k: datetime.fromtimestamp(t, ZoneInfo(z)).isoformat() for k, z in
            [('UTC', 'UTC'), ('CT', 'America/Chicago'), ('ET', 'America/New_York')]}


def display_time(t):
    if t is None:
        return 'not recorded'
    c = clocks(t)
    return f"{c['CT'][:16].replace('T', ' ')} CT / {c['ET'][:16].replace('T', ' ')} ET"


def exact_int(value):
    return type(value) is int


def require(condition, message):
    if not condition:
        raise ValueError(message)


def jsonlines(data):
    return [json.loads(x) for x in data.splitlines() if x.strip()]


def safe_read(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), 'Input path escapes sealed run')
    return path.read_bytes()


def parse_source(data):
    rows = {}
    reader = csv.DictReader(io.StringIO(data.decode('utf-8')))
    require(reader.fieldnames == ['epoch', *OHLC], 'Unexpected native source columns')
    previous = -1
    for row in reader:
        t = int(row['epoch'])
        p = {k: float(row[k]) for k in OHLC}
        require(t % 60 == 0 and t > previous, 'Native times must be increasing UTC minutes')
        require(all(math.isfinite(v) and v > 0 for v in p.values()), 'Invalid native price')
        require(p['low'] <= min(p['open'], p['close']) <= max(p['open'], p['close']) <= p['high'], 'Invalid OHLC')
        rows[t] = dict(time=t, known_at=t + 60, **p)
        previous = t
    return rows



def check_event_source(event, sources, source_timeframes):
    """Bind a candle-backed event to its exact timeframe, opening and close."""
    sid = event.get('source_id')
    if sid is not None:
        require(sid in sources and exact_int(event.get('timeframe'))
                and source_timeframes.get(sid) == event['timeframe']
                and sources[sid]['known_at'] == event['known_at'],
                'Event is not bound to its exact source candle/timeframe')
    if 'bar_start' in event:
        require(sid in sources and exact_int(event['bar_start'])
                and event['bar_start'] == sources[sid]['time'],
                'Event bar_start differs from its source candle')
    if 'htf_context_as_of' in event:
        require(exact_int(event['htf_context_as_of'])
                and event['htf_context_as_of'] == event.get('bar_start'),
                'Event context clock differs from its source candle')


def validate_target_lineage(events, sources, source_timeframes):
    """Verify recorded target facts against prior, source-bound recognition.

    Recognition must precede the deciding minute's opening. A retouch during
    that minute may become known at its close, and is separately evidenced.
    This validates saved evidence, without recomputing strategy decisions.
    """
    pivots, bosses, zones, children, targets, reached = {}, {}, {}, {}, {}, {}
    for event in events:
        kind = event['kind']
        if kind in ('PIVOT_CONFIRMED', 'MAJOR_BOS', 'ZONE_CANDIDATE',
                    'ORIGINAL_TARGET_KNOWN', 'ORIGINAL_TARGET_REACHED'):
            check_event_source(event, sources, source_timeframes)
        if kind == 'PIVOT_CONFIRMED':
            pivot = event['pivot']
            origin = sources.get(pivot.get('source_id'))
            require(origin is not None and pivot.get('kind') in ('high', 'low')
                    and pivot['time'] == origin['time']
                    and origin['known_at'] <= pivot['known_at'] == event['known_at']
                    and pivot['price'] == origin[pivot['kind']]
                    and source_timeframes.get(pivot['source_id']) == event['timeframe'],
                    'Target lineage pivot source/recognition differs')
            pivots[pivot['id']] = event
        elif kind == 'MAJOR_BOS':
            require(event['bos_id'] not in bosses, 'Duplicate target owner BOS')
            bosses[event['bos_id']] = event
        elif kind == 'ZONE_CANDIDATE':
            bos = bosses.get(event.get('bos_id'))
            require(bos is not None and all(event.get(k) == bos.get(k) for k in
                    ('timeframe', 'authority_id', 'direction', 'known_at', 'source_id', 'bar_start')),
                    'Parent zone lacks its recorded owner BOS')
            require(event['zone_id'] not in zones, 'Duplicate parent zone identity')
            zones[event['zone_id']] = event
        elif kind == 'ORIGINAL_TARGET_KNOWN':
            bos = bosses.get(event.get('bos_id'))
            target = event.get('target')
            require(bos is not None and isinstance(target, dict), 'Target lacks its owner BOS')
            pivot = target.get('pivot')
            require(isinstance(pivot, dict), 'Target lacks its confirmed pivot')
            confirmation = pivots.get(pivot.get('id'))
            require(confirmation is not None and confirmation['pivot'] == pivot
                    and confirmation['timeframe'] == event['timeframe'] == bos['timeframe']
                    and confirmation['source_id'] == event['source_id']
                    and all(event.get(k) == bos.get(k) for k in ('authority_id', 'direction'))
                    and pivot['kind'] == ('high' if bos['direction'] == 'long' else 'low')
                    and target.get('price') == pivot['price']
                    and target.get('known_at') == pivot['known_at'] == event['known_at']
                    and bos['known_at'] < event['known_at'] and pivot['time'] >= bos['bar_start']
                    and target.get('reached') is False and target.get('reached_at') is None
                    and event['bos_id'] not in targets,
                    'Target recognition differs from its prior confirmed owner pivot')
            targets[event['bos_id']] = target
        elif kind == 'ORIGINAL_TARGET_REACHED':
            bos = bosses.get(event.get('bos_id'))
            original = targets.get(event.get('bos_id'))
            require(bos is not None and original is not None and event['bos_id'] not in reached,
                    'Target retouch lacks its prior recognition')
            expected = dict(original, reached=True, reached_at=event['known_at'])
            candle = sources[event['source_id']]
            require(event['timeframe'] == 1 and original['known_at'] <= event['bar_start']
                    and event.get('target') == expected
                    and all(event.get(k) == bos.get(k) for k in ('authority_id', 'direction'))
                    and (candle['high'] >= original['price'] if bos['direction'] == 'long'
                         else candle['low'] <= original['price']),
                    'Target retouch source/availability differs')
            reached[event['bos_id']] = expected
        if kind == 'CHILD_CANDIDATE':
            require(event['child_id'] not in children, 'Duplicate child identity')
            children[event['child_id']] = event
            parent = zones.get(event.get('parent_zone_id'))
            require(parent is not None and parent['direction'] == event['direction']
                    and parent['known_at'] <= event['bar_start'], 'Child parent was not known at selection')
            seen_opposing = set()
            for opposing in event.get('opposing_known_zones', []):
                zone = zones.get(opposing.get('zone_id'))
                require(zone is not None and zone['direction'] != event['direction']
                        and zone['known_at'] <= event['bar_start']
                        and all(opposing.get(k) == zone.get(k) for k in
                                ('zone_id', 'timeframe', 'zone_low', 'zone_high', 'known_at'))
                        and zone['zone_id'] not in seen_opposing,
                        'Opposing zone lacks prior source-bound recognition')
                seen_opposing.add(zone['zone_id'])
        if 'target_known' in event:
            check_event_source(event, sources, source_timeframes)
            child = children.get(event.get('child_id'))
            require(child is not None, 'Target flags lack their original child selection')
            parent = zones.get(child.get('parent_zone_id'))
            require(parent is not None, 'Target flags lack their recorded parent')
            original = targets.get(parent['bos_id'])
            expected = None
            if original is not None and original['known_at'] <= event['bar_start']:
                expected = reached.get(parent['bos_id'], original)
            require(type(event.get('target_known')) is bool
                    and event['target_known'] == (expected is not None)
                    and event.get('target') == expected
                    and type(event.get('target_already_reached')) is bool
                    and event['target_already_reached'] == bool(expected and expected['reached']),
                    'Target flags differ from prior owner recognition at the minute opening')


def marker_candle(run, event, cutoff):
    """Defensive display boundary even for direct callers bypassing load_run."""
    t, sid = event.get('bar_start'), event.get('source_id')
    candle, source = run['native'].get(t), run['sources'].get(sid)
    if (not exact_int(event.get('timeframe')) or event['timeframe'] != 1
            or run.get('source_timeframes', {}).get(sid) != 1 or candle is None or source is None
            or not exact_int(t) or source.get('time') != t
            or source.get('source_id') != sid or source.get('member_times') != [t]
            or source.get('known_at') != t + 60 or candle.get('known_at') != t + 60
            or event.get('known_at') != t + 60 or candle['known_at'] > cutoff):
        return None
    return candle


def load_run(folder):
    """Read immutable byte snapshots. Never import or execute captured code."""
    root = Path(folder).resolve()
    completion_bytes = safe_read(root, 'COMPLETION.json')
    completion = json.loads(completion_bytes)
    require(completion.get('status') == 'SAVED_OUTPUT_RECONCILED', 'Run lacks completed reconciliation')
    require(completion.get('execution_allowed') is False, 'Only research runs supported')
    held = {name: safe_read(root, name) for name in FILES}
    for name, content in held.items():
        require(digest(content) == completion.get('output_hashes', {}).get(name), f'Changed sealed output: {name}')
    manifest = json.loads(held['MANIFEST.json'])
    captured = {}
    for key, name in CAPTURES.items():
        meta = manifest.get('captures', {}).get(key, {})
        require(meta.get('path') == 'captures/' + name, f'Unexpected capture path: {key}')
        captured[key] = safe_read(root, meta['path'])
        h = digest(captured[key])
        require(h == meta.get('sha256') == completion.get('captured_input_hashes', {}).get(key)
                == manifest.get(key + '_sha256'), f'Changed captured dependency: {key}')
    spec = json.loads(captured['spec'])
    acquisition = json.loads(captured['acquisition'])
    clarification = json.loads(captured['clarifications'])
    scope = spec['scope']
    require(scope.get('symbol') == 'NAS100 CFD' and scope.get('provider') == 'Dukascopy USATECHIDXUSD'
            and scope.get('side') == 'BID', 'This frozen report supports only the declared NAS100 Dukascopy BID study')
    require(spec.get('execution_allowed') is False and manifest.get('execution_allowed') is False,
            'Execution permission is incompatible with report')
    require(clarification.get('spec_sha256') == digest(captured['spec']), 'Wrong clarification binding')
    require(acquisition.get('csv', {}).get('sha256') == digest(captured['source'])
            and acquisition['csv'].get('bytes') == len(captured['source']), 'Acquisition source binding differs')
    require(acquisition.get('symbol') == scope['symbol'] and acquisition.get('feed') == scope['provider']
            and acquisition.get('side') == scope['side'], 'Acquisition instrument differs')
    require(epoch(acquisition['start']['utc']) == epoch(scope['warmup_start'])
            and epoch(acquisition['end_exclusive']['utc']) == epoch(scope['end_exclusive']), 'Acquisition dates differ')
    native = parse_source(captured['source'])
    require(len(native) == acquisition.get('minute_count'), 'Source minute count differs')
    warm, start, end = (epoch(scope[x]) for x in ('warmup_start', 'study_start', 'end_exclusive'))
    require(warm <= start < end and all(warm <= t < end for t in native), 'Native rows outside frozen scope')
    plan = jsonlines(held['sealed-plan.jsonl'])
    coverage = jsonlines(held['coverage.jsonl'])
    events = jsonlines(held['events.jsonl'])
    inputs = jsonlines(held['input-audit.jsonl'])
    result, snapshot = json.loads(held['RESULT.json']), json.loads(held['SNAPSHOT.json'])
    require(snapshot.get('event_count') == len(events) and snapshot.get('event_head') == digest(events), 'Event count/head differs')
    require(snapshot.get('spec_hash') == digest(spec), 'Snapshot specification differs')
    require(len(inputs) == len(plan), 'Input audit count differs')
    bars, sources, ids, seen = defaultdict(list), {}, [], set()
    source_timeframes = {}
    prior, previous = '0' * 64, warm - 1
    for record, action in zip(inputs, plan):
        known, gaps, batch = action
        require(exact_int(known) and previous < known <= end, 'Plan cutoff not strictly increasing/in scope')
        previous = known
        current = dict(record)
        h = current.pop('record_hash')
        require(current['previous_hash'] == prior and current['known_at'] == known
                and current['input_sha256'] == digest([gaps, batch]) and h == digest(current), 'Input chain differs')
        prior = h
        ids.extend(current['event_ids'])
        tfs = [tf for tf, _ in batch]
        require(tfs == sorted(set(tfs), reverse=True), 'Duplicate or unsorted timeframes')
        for tf, bar in batch:
            require(type(tf) is int and tf in TF_NAME and tf is not None, 'Unsupported timeframe')
            t, members = bar['time'], bar['member_times']
            require(exact_int(t) and t % (tf * 60) == 0 and bar['known_at'] == known == t + tf * 60
                    and bar.get('complete') is True, 'Incomplete/off-grid/misclocked planned bar')
            require(members and members == sorted(set(members))
                    and all(exact_int(m) and t <= m < known and m in native for m in members), 'Invalid/future native membership')
            require(len(members) == bar['observed_minutes'] == bar['expected_minutes'], 'Planned bar counts differ')
            expected = [native[members[0]]['open'], max(native[m]['high'] for m in members),
                        min(native[m]['low'] for m in members), native[members[-1]]['close']]
            require(all(bar[k] == v for k, v in zip(OHLC, expected)), 'Planned OHLC differs from exact native members')
            sid = f"{manifest['source_sha256']}:{tf}:{t}"
            require(bar['source_id'] == sid and sid not in sources, 'Invalid/duplicate planned source identity')
            sources[sid] = bar
            source_timeframes[sid] = tf
            bars[tf].append(bar)
    require(prior == result.get('last_input_hash') and ids == [e['id'] for e in events], 'Final input chain/event order differs')
    previous = warm - 1
    def check_pivot(p, available):
        if not p:
            return
        require(p['source_id'] in sources, 'Pivot origin source is absent')
        origin=sources[p['source_id']]
        require(p['time']==origin['time'] and origin['known_at']<=p['known_at']<=available
                and p['price']==origin[p['kind']], 'Pivot origin/recognition clock or price differs')
        if p.get('comparator'):
            check_pivot(p['comparator'],p['known_at'])
    for event in events:
        require(event['id'] not in seen and exact_int(event['known_at'])
                and previous <= event['known_at'] <= end, 'Duplicate/backdated event')
        require(event.get('spec_hash') == digest(spec) and event.get('execution_allowed') is False,
                'Event authority/specification differs')
        check_event_source(event, sources, source_timeframes)
        for key in ('reference','pullback','pivot'):
            if event.get(key):
                check_pivot(event[key],event['known_at'])
        for chain in event.get('comparison_chains',{}).values():
            for p in chain:check_pivot(p,event['known_at'])
        for p in event.get('pre_tap_pivots',[]):check_pivot(p,event.get('bar_start',event['known_at']))
        if event['kind'] in ('ZONE_CANDIDATE','CHILD_CANDIDATE'):
            members=event['member_times'];tf=event['timeframe']
            expected_ids=[f"{manifest['source_sha256']}:{tf}:{t}" for t in members]
            require(members and members==sorted(set(members)) and event['source_ids']==expected_ids
                    and all(s in sources for s in expected_ids), 'Frozen origin membership differs')
            origins=[sources[s] for s in expected_ids]
            require(event['selected_known_at']==event['known_at'] and
                    event['origin_start']==members[0] and event['origin_end']==members[-1]
                    and all(b['known_at']<=event['bar_start'] for b in origins), 'Future/backdated frozen origin')
            require(event['zone_low']==min(b['low'] for b in origins)
                    and event['zone_high']==max(b['high'] for b in origins), 'Frozen zone geometry differs from members')
        seen.add(event['id']); previous = event['known_at']
    validate_target_lineage(events, sources, source_timeframes)
    month = [e for e in events if start < e['known_at'] <= end]
    require(len(month) == result['september_events'] and dict(Counter(e['kind'] for e in month)) == result['event_counts'],
            'Saved study event counts differ')
    return dict(root=str(root), completion_sha256=digest(completion_bytes), manifest=manifest, spec=spec,
                acquisition=acquisition, result=result, native=native, bars=dict(bars), sources=sources, source_timeframes=source_timeframes,
                coverage=coverage, events=events, warm=warm, start=start, end=end,
                captured_hashes=completion['captured_input_hashes'], output_hashes=completion['output_hashes'])


def select_cases(events, start, end):
    """Outcome-blind context selection; decision navigation is separately declared."""
    revocations = defaultdict(list)
    for e in events:
        if e['kind'] == 'LINK_REVOKED':
            revocations[e['link_id']].append(e['known_at'])
    links = [(i, e) for i, e in enumerate(events) if e['kind'] == 'HTF_LINK'
             and e['known_at'] <= end and not any(t <= start for t in revocations[e['link_id']])]
    links.sort(key=lambda x: (max(start, x[1]['known_at']), x[0]))
    chosen = []
    for rank, (_, link) in enumerate(links[:10], 1):
        relevant = [e for e in events if e.get('link_id') == link['link_id'] and start < e['known_at'] <= end]
        children = [e for e in relevant if e['kind'] == 'CHILD_CANDIDATE']
        taps = [e for e in relevant if e['kind'] == 'HTF_TAP']
        anchor = children[0] if children else taps[0] if taps else link
        chosen.append(dict(rank=rank, link_id=link['link_id'], link_event_id=link['id'],
                           context_known_at=link['known_at'], decision_cutoff=max(start, anchor['known_at']),
                           cutoff_basis='first child candidate' if children else 'first tap; no child candidate at this cutoff'
                           if taps else 'context recognition/study start; no tap at this cutoff',
                           direction=link['direction'], h4_authority_id=link['h4_authority_id'],
                           h1_authority_id=link['h1_authority_id']))
    return chosen


def case_events(events, case):
    owners = {case['h4_authority_id'], case['h1_authority_id']}
    zones = {e['zone_id'] for e in events if e['kind'] == 'ZONE_CANDIDATE' and e['authority_id'] in owners}
    taps = {e['tap_id'] for e in events if e['kind'] == 'HTF_TAP' and e['link_id'] == case['link_id']}
    children = {e['child_id'] for e in events if e['kind'] == 'CHILD_CANDIDATE' and e['link_id'] == case['link_id']}
    relevant=[]
    for event in events:
        # A shared 4H origin does not merge distinct 1H-linked contexts. Explicit
        # routing wins over common ancestry, including rejected child branches.
        if 'link_id' in event:
            include=event['link_id']==case['link_id']
        elif 'child_id' in event:
            include=event['child_id'] in children
        elif 'tap_id' in event:
            include=event['tap_id'] in taps
        else:
            include=event.get('authority_id') in owners or event.get('zone_id') in zones
        if include:
            relevant.append(event)
    return relevant


def known_pivots(events, cutoff):
    found = {}
    def add(p):
        if isinstance(p, dict) and 'price' in p and 'time' in p and p.get('known_at', cutoff + 1) <= cutoff:
            found[p['id']] = p
            add(p.get('comparator'))
    for e in events:
        if e['known_at'] > cutoff:
            continue
        for key in ('reference', 'pullback', 'pivot'):
            add(e.get(key))
        for chain in e.get('comparison_chains', {}).values():
            for p in chain:
                add(p)
        for p in e.get('pre_tap_pivots', []):
            add(p)
    return sorted(found.values(), key=lambda p: (p['time'], p['known_at'], p['id']))


def decision_view(run, case):
    cutoff = case['decision_cutoff']
    events = [e for e in case_events(run['events'], case) if e['known_at'] <= cutoff]
    pivots = known_pivots([e for e in events if e.get('timeframe') == 1 or e['kind'] == 'HTF_TAP'], cutoff)
    starts = [case['context_known_at'], cutoff - 7200]
    for e in events:
        if e.get('timeframe') == 1 or e['kind'] == 'HTF_TAP':
            starts.extend(e.get('member_times', []))
            if 'bar_start' in e:
                starts.append(e['bar_start'])
    starts.extend(p['time'] for p in pivots)
    # A long wait must not expand the detailed M1 view merely because context was
    # recognized weeks before its first tap. Every actual comparator is retained.
    relevant_starts = [cutoff - 7200] + [p['time'] for p in pivots]
    relevant_starts += [e['bar_start'] for e in events if e['kind'] == 'HTF_TAP' and 'bar_start' in e]
    relevant_starts += [t for e in events if e.get('timeframe') == 1 for t in e.get('member_times', [])]
    local_start = max(run['warm'], min(relevant_starts) - 1800)
    return dict(cutoff=cutoff, events=events, pivots=pivots, local_start=local_start,
                bars={tf: [b for b in run['bars'].get(tf, []) if b['known_at'] <= cutoff]
                      for tf in (240, 60, 1)},
                native=[b for b in run['native'].values() if local_start <= b['time'] and b['known_at'] <= cutoff])


def bos_index(run):
    """Every study BOS, not just the ten examples or successful origin branches."""
    events = run['events']; rows = []
    for e in events:
        if e['kind'] not in ('MAJOR_BOS', 'M1_TRANSITION_BOS') or not run['start'] < e['known_at'] <= run['end']:
            continue
        origins = [x for x in events if x.get('bos_id') == e['bos_id'] and x['kind'] in ('ZONE_CANDIDATE', 'CHILD_CANDIDATE')]
        zone_ids = {x['zone_id'] for x in origins}
        taps = [x for x in events if x['kind'] == 'HTF_TAP' and x.get('zone_id') in zone_ids]
        children = [x for x in events if x['kind'] == 'CHILD_CANDIDATE' and
                    (x.get('parent_zone_id') in zone_ids or x.get('zone_id') in zone_ids)]
        child_ids = {x['child_id'] for x in children}
        lifecycle = [x for x in events if x.get('zone_id') in zone_ids or x.get('child_id') in child_ids
                     or (e.get('authority_id') is not None and x.get('authority_id')==e['authority_id']
                         and x['kind']=='AUTHORITY_REVOKED')]
        reject = [x for x in events if x.get('bos_id') == e['bos_id'] and x['kind'] in ('FORMATION_REJECTED', 'CHILD_FORMATION_REJECTED')]
        row = dict(event_id=e['id'], kind=e['kind'], timeframe=e['timeframe'], direction=e['direction'],
                   known_at=e['known_at'], clocks=clocks(e['known_at']), source_id=e.get('source_id'),
                   bar_start=e.get('bar_start'), reference=e.get('reference'), pullback=e.get('pullback'),
                   authority_id=e.get('authority_id'), link_id=e.get('link_id'),
                   parent_tap_id=e.get('tap_id'),
                   origin_ids=sorted(zone_ids), formation_rejections=[x['id'] for x in reject],
                   tap_ids=[x['id'] for x in taps], child_ids=sorted(child_ids),
                   return_ids=[x['id'] for x in lifecycle if x['kind'] == 'RETURN_OBSERVED'],
                   respect_ids=[x['id'] for x in lifecycle if x['kind'] == 'RESPECT_OBSERVED'],
                   failure_ids=[x['id'] for x in lifecycle if 'BREACHED' in x['kind'] or 'REVOKED' in x['kind']],
                   status='NO_ORIGIN_RESOLVED' if not origins else 'ORIGIN_RECORDED')
        rows.append(row)
    return rows


def merged_spans(coverage, tf, start, end):
    groups = []
    for c in coverage:
        # A final higher-bucket classification is unavailable before bucket
        # close. Painting its prefix would reveal missing/future minutes.
        if (c['timeframe'] != tf or c['status'] == 'COMPLETE' or c['known_at'] <= start
                or c['time'] >= end or c['known_at'] > end):
            continue
        left, right = max(c['time'], start), min(c['known_at'], end)
        if groups and groups[-1][2] == c['status'] and groups[-1][1] == left:
            groups[-1][1] = right
        else:
            groups.append([left, right, c['status']])
    return groups


def mpl():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle
    return plt, dates, LineCollection, Rectangle


def plot_case(run, case, output, follow=False, detail_end=None):
    plt, dates, LineCollection, Rectangle = mpl()
    view = decision_view(run, case)
    cutoff = detail_end if detail_end is not None else run['end'] if follow else view['cutoff']
    related = [e for e in case_events(run['events'], case) if e['known_at'] <= cutoff]
    # A complete native timeline to frozen end is always present in follow-through.
    native_start = view['local_start']
    fig, axes = plt.subplots(4, 1, figsize=(19, 20), gridspec_kw={'height_ratios': [1, 1, 1, 1.25]})
    fig.subplots_adjust(left=.065, right=.745, top=.885, bottom=.105, hspace=.65)
    stamp = ('HISTORICAL DETAIL · first recorded return + 60 minutes; full-to-end view provided separately'
             if detail_end is not None else 'HISTORICAL DEVELOPMENT · later candles revealed through frozen end'
             if follow else 'SAVED DECISION VIEW · no candle or event after this cutoff')
    synthetic = ' · SYNTHETIC TEST DATA' if run['acquisition'].get('synthetic_fixture') else ''
    fig.suptitle(f"Case {case['rank']:02d} · {case['direction'].upper()} linked context{synthetic}\n{stamp}",
                 x=.055, y=.982, ha='left', fontsize=17, fontweight='bold')
    fig.text(.055, .929, f"Cutoff: {display_time(cutoff)}\nOriginal decision: {display_time(view['cutoff'])} · {case['cutoff_basis']}", fontsize=10)
    fig.text(.055, .905, f"NAS100 CFD · Dukascopy USATECHIDXUSD BID · research interpretation · no fills / teacher verification / Pepperstone parity", fontsize=10, color='#6b3e18')
    xnum = lambda t: dates.date2num(datetime.fromtimestamp(t, timezone.utc))
    decision_transitions=[e for e in related if e['kind']=='M1_TRANSITION_BOS' and e['known_at']<=view['cutoff']]
    # All comparators stay in the preceding full-context panel. The final panel
    # enlarges the post-tap structure and frozen child members; it is not a substitute.
    detail_times=[p['time'] for e in decision_transitions for chain in e.get('comparison_chains',{}).values() for p in chain[-2:]]
    detail_times += [t for e in view['events'] if e['kind']=='CHILD_CANDIDATE' for t in e['member_times']]
    detail_start=max(run['warm'],min([view['cutoff']-3600]+[t-900 for t in detail_times]))
    detail_cutoff=cutoff
    if follow:
        first_return=next((e for e in related if e['kind']=='RETURN_OBSERVED' and e['known_at']>view['cutoff']),None)
        if first_return:
            detail_start=max(detail_start,first_return['known_at']-3600)
            detail_cutoff=min(run['end'],first_return['known_at']+3600)
        else:
            detail_cutoff=min(run['end'],view['cutoff']+3600)
    for panel, (ax, tf) in enumerate(zip(axes, (240, 60, 1, 1))):
        start = native_start if tf == 1 else run['warm']
        panel_cutoff=cutoff
        if panel==3:
            start=detail_start;panel_cutoff=detail_cutoff
        bars = ([b for b in run['native'].values() if start <= b['time'] and b['known_at'] <= cutoff]
                if tf == 1 else [b for b in run['bars'].get(tf, []) if b['known_at'] <= cutoff])
        bars=[b for b in bars if b['known_at']<=panel_cutoff]
        x = [xnum(b['time']) for b in bars]
        dense = len(bars) > 1600
        if bars:
            segs = [[(v, b['low']), (v, b['high'])] for v, b in zip(x, bars)]
            colors = ['#207d67' if b['close'] >= b['open'] else '#b84c54' for b in bars]
            ax.add_collection(LineCollection(segs, colors=colors, linewidths=.65 if dense else .8, alpha=.55 if dense else .9))
            flat=[[(v-tf/1440*.3,b['close']),(v+tf/1440*.3,b['close'])]
                  for v,b in zip(x,bars) if b['high']==b['low']]
            if flat:
                ax.add_collection(LineCollection(flat,colors='#697d85',linewidths=.65,alpha=.8))
            if not dense:
                width = tf * 60 / 86400 * .68
                for v, b, col in zip(x, bars, colors):
                    body = max(abs(b['close'] - b['open']), (b['high'] - b['low']) * .012, 1e-9)
                    ax.add_patch(Rectangle((v - width / 2, min(b['open'], b['close'])), width, body,
                                           facecolor=col, edgecolor=col, linewidth=.35, alpha=.85))
            ax.autoscale_view()
        else:
            ax.text(.5, .45, 'No complete candles available in this interval', transform=ax.transAxes, ha='center')
        for left, right, status in merged_spans(run['coverage'], tf, start, panel_cutoff):
            ax.axvspan(xnum(left), xnum(right), color='#929ba4' if status == 'CLOSED' else '#d19035',
                       alpha=.13 if status == 'CLOSED' else .20, linewidth=0)
        zones = [e for e in related if e['kind'] == ('CHILD_CANDIDATE' if tf == 1 else 'ZONE_CANDIDATE')
                 and (tf == 1 or e['timeframe'] == tf)]
        # Parent routes can share a physical origin but retire at different
        # clocks. Keep route lifetimes; overlap is not another independent trade.
        physical = set()
        for z in zones:
            key = z['zone_id']
            if key in physical:
                continue
            physical.add(key)
            if z['known_at'] > panel_cutoff:
                continue
            c = '#166b9d' if z['direction'] == 'long' else '#ad6234'
            retirements=[e['known_at'] for e in related if e['known_at']<=panel_cutoff and
                ((e.get('zone_id')==z['zone_id'] and e['kind'] in ('PARENT_ZONE_BREACHED','CHILD_ZONE_BREACHED','CHILD_PERMISSION_REVOKED','CHILD_COVERAGE_UNKNOWN'))
                 or (z.get('authority_id') is not None and e.get('authority_id')==z['authority_id'] and e['kind']=='AUTHORITY_REVOKED'))]
            active_end=min([panel_cutoff]+retirements)
            # Solid from recognition, dashed origin footprint: geometry is not backdated permission.
            active_start=max(start,z['known_at'])
            if active_end>=active_start:
                ax.add_patch(Rectangle((xnum(active_start), z['zone_low']), max(xnum(active_end)-xnum(active_start), 1/86400),
                                       z['zone_high']-z['zone_low'], facecolor=c, edgecolor=c, alpha=.10))
            if active_end<panel_cutoff:
                for edge in (z['zone_low'],z['zone_high']):
                    ax.plot([xnum(max(start,active_end)),xnum(panel_cutoff)],[edge,edge],color='#9b7278',ls=':',lw=.75)
            ax.add_patch(Rectangle((xnum(z['origin_start']), z['zone_low']),
                                   max((z['origin_end']-z['origin_start']+tf*60)/86400, tf/1440),
                                   z['zone_high']-z['zone_low'], facecolor='none', edgecolor=c, linestyle='--', linewidth=1))
        focus = [e for e in related if e.get('timeframe') == tf and e['kind'] in ('MAJOR_BOS', 'M1_TRANSITION_BOS')
                 and e['known_at']<=panel_cutoff]
        if panel==2:
            p_events=[e for e in related if e['kind']=='HTF_TAP' or e['kind']=='M1_TRANSITION_BOS']
        elif panel==3:
            p_events=decision_transitions
        else:
            p_events=focus
        pivots = [p for p in known_pivots(p_events, panel_cutoff) if start<=p['time']<=panel_cutoff][-8:]
        clock_key=[]
        def short_clock(t):
            c=clocks(t)
            return c['CT'][5:16].replace('T',' ')+' CT / '+c['ET'][5:16].replace('T',' ')+' ET'
        for i, p in enumerate(pivots,1):
            if not start <= p['time'] <= panel_cutoff:
                continue
            # Short on-chart key; both exact origin and known clocks are visible
            # in the adjoining clock key, rather than overlapping candle labels.
            ax.scatter([xnum(p['time'])], [p['price']], s=18, color='#5547a6', zorder=5)
            ax.annotate(f"P{i} {p['label']}", (xnum(p['time']), p['price']),
                        xytext=(-3,12 if p['kind']=='high' else -17),textcoords='offset points',
                        ha='right',fontsize=7,color='#5547a6')
            ax.scatter([xnum(p['known_at'])], [p['price']], s=12, marker='|', color='#5547a6', zorder=5)
            clock_key.append(f"P{i} {p['label']} {p['price']:g} · origin {short_clock(p['time'])}\nKnown {short_clock(p['known_at'])}")
        owner_note=''
        if tf in (240,60):
            owner=case['h4_authority_id'] if tf==240 else case['h1_authority_id']
            revoked=next((e for e in related if e['kind']=='AUTHORITY_REVOKED' and e.get('authority_id')==owner and e['known_at']<=panel_cutoff),None)
            owner_note=('Original owner REVOKED\n'+short_clock(revoked['known_at'])+'\n\n' if revoked else 'Original owner active at panel cutoff\n\n')
        if clock_key or owner_note:
            pos=ax.get_position()
            fig.text(.766,pos.y1,owner_note+'Pivot clocks · origin ≠ recognition\n\n'+'\n'.join(clock_key),
                     va='top',fontsize=6.6,linespacing=1.35,color='#5547a6')
        observed_bos=set()
        for i, e in enumerate(focus):
            ref = e.get('reference', {})
            key=(e['known_at'],ref.get('price'))
            if 'price' not in ref or key in observed_bos:
                continue
            observed_bos.add(key)
            ax.scatter([xnum(e['known_at'])], [ref['price']], marker='D', s=29, color='#161c24', zorder=6)
            if len(focus) <= 8:
                ax.annotate('BOS known', (xnum(e['known_at']), ref['price']), xytext=(-7, 9),
                            textcoords='offset points',ha='right',fontsize=7,color='#161c24')
        if tf == 1:
            symbols = {'HTF_TAP': ('v','#2588ae'), 'RETURN_OBSERVED':('o','#954bc1'),
                       'RESPECT_OBSERVED':('*','#1b8549'), 'CHILD_ZONE_BREACHED':('x','#bd2434'),
                       'PARENT_ZONE_BREACHED':('X','#8b1020')}
            for e in related:
                if e['kind'] not in symbols or e['known_at'] > panel_cutoff:
                    continue
                b = marker_candle(run, e, panel_cutoff)
                if b and b['time'] >= start:
                    marker, color = symbols[e['kind']]
                    ax.scatter([xnum(b['time'])], [b['close']], marker=marker, c=color, s=38, zorder=7)
        ax.axvline(xnum(view['cutoff']), color='#1c3f70', linestyle=':', linewidth=1.3)
        ax.set_xlim(xnum(start), xnum(max(panel_cutoff, start + 60)))
        ax.margins(y=.28)
        locator = dates.AutoDateLocator(minticks=4, maxticks=7)
        ax.xaxis.set_major_locator(locator)
        from matplotlib.ticker import FuncFormatter
        def tick(v, pos):
            dt = dates.num2date(v)
            ct, et = dt.astimezone(ZoneInfo('America/Chicago')), dt.astimezone(ZoneInfo('America/New_York'))
            return f"{ct:%b %d %H:%M} CT\n{et:%b %d %H:%M} ET"
        ax.xaxis.set_major_formatter(FuncFormatter(tick))
        ax.tick_params(axis='both', labelsize=8)
        ax.grid(alpha=.15)
        ax.set_ylabel('Index points · BID', fontsize=8)
        if tf == 1:
            description = 'native minute range strokes (all available minutes)' if dense else 'native minute candles'
            if panel==3:
                period='local decision detail' if not follow and detail_end is None else 'fixed chronological local detail (full timeline above)'
            else:
                period = 'first-return context' if detail_end is not None else 'FULL development to frozen end' if follow else 'full retained pre-tap comparator context'
            title = f"1M · {description} · {period}"
        else:
            title = f"{TF_NAME[tf]} · linked {case['direction'].upper()} owner · FULL warmup context · fixed UTC phase 0 · completed candles"
        ax.set_title(title, loc='left', fontsize=10, pad=9)
    fig.text(.055, .045,
             'Dashed box = origin footprint; shaded extension begins when zone became known. Purple dot = pivot origin; purple tick = recognition.\n'
             'Diamond = BOS known; blue triangle = parent tap; purple circle = recorded return; green star = assumed respect; red cross = strict breach.\n'
            'Dotted box edges after retirement retain historical geometry, not eligibility. Gray = scheduled closure; amber = incomplete / unknown / contradictory coverage.\n'
             'Up to eight pivot labels per panel; full evidence, branch members, clocks, IDs and routes remain in the ledger. No recorded return does not prove no later physical overlap.',
             fontsize=8, linespacing=1.5, color='#3d4854')
    fig.savefig(output, dpi=135, facecolor='white')
    plt.close(fig)


def html_table(headers, rows):
    esc = lambda x: html.escape(str(x))
    return '<table><thead><tr>' + ''.join('<th>'+esc(x)+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join('<td>'+esc(x)+'</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table>'


def event_summary(e):
    bits = []
    if 'zone_low' in e:
        bits.append(f"{e['zone_low']:g}–{e['zone_high']:g}")
    for k in ('branch_names', 'reason', 'review_status', 'failed_same_bar', 'target_known', 'target_already_reached'):
        if k in e:
            bits.append(f'{k}: {e[k]}')
    if 'member_times' in e:
        bits.append('members: '+', '.join(display_time(t) for t in e['member_times']))
    return ' | '.join(bits)


def make_case_html(run, case, events, filename, return_detail=False):
    validate_target_lineage(run['events'], run['sources'], run['source_timeframes'])
    # A caller cannot substitute an unvalidated child payload into a trusted run.
    originals = {e['id']: e for e in run['events']}
    require(all(originals.get(e['id']) == e for e in events), 'HTML events differ from validated lineage')
    cutoff = case['decision_cutoff']
    pivots = known_pivots([e for e in events if e.get('timeframe') == 1 or e['kind'] == 'HTF_TAP'], cutoff)
    zones = [e for e in events if e['kind'] in ('ZONE_CANDIDATE', 'CHILD_CANDIDATE')]
    zone_table = html_table(['Role / route', 'Frozen box', 'Branch hypotheses', 'Origin candles CT / ET', 'Selected known CT / ET', 'Identity'], [
        [f"{TF_NAME.get(e['timeframe'])} {e['direction']} · parent {e.get('parent_zone_id', e.get('authority_id'))}",
         f"{e['zone_low']:g}–{e['zone_high']:g}", ', '.join(e['branch_names']),
         '\n'.join(display_time(t) for t in e['member_times']), display_time(e['known_at']), e['zone_id']]
        for e in zones])
    pivot_table = html_table(['Label / level', 'Origin CT / ET', 'Known CT / ET', 'Comparator', 'ID / native source'], [
        [f"{p['label']} · {p['price']:g}", display_time(p['time']), display_time(p['known_at']),
         p.get('comparator', {}).get('id') if p.get('comparator') else 'none', f"{p['id']}\n{p.get('source_id')}"] for p in pivots])
    target_table = html_table(['Child proposal','Original target at saved selection','Known clock CT / ET','Already reached','Opposing zones known then'],[
        [e['child_id'],e['target']['price'] if e.get('target_known') and e.get('target') else 'UNKNOWN — no target invented',
         display_time(e['target']['known_at']) if e.get('target_known') and e.get('target') else 'not known',
         e.get('target_already_reached','not recorded'),
         '\n'.join(f"{z['timeframe']}M {z['zone_low']:g}–{z['zone_high']:g} · {z['zone_id']}" for z in e.get('opposing_known_zones',[])) or 'none recorded']
        for e in zones if e['kind']=='CHILD_CANDIDATE' and e['known_at']<=cutoff])
    timeline = html_table(['Known CT / ET', 'Stage / owner', 'Event ID', 'Route / parent', 'Recorded facts'], [
        [display_time(e['known_at']), f"{TF_NAME.get(e.get('timeframe'))} · {e['kind']}", e['id'],
         '\n'.join(f'{k}: {e[k]}' for k in ('link_id','zone_id','parent_zone_id','child_id','tap_id','authority_id') if k in e),
         event_summary(e)] for e in events])
    prefix = f"case-{case['rank']:02d}"
    detail = (f'<h3>First recorded return, then sixty more minutes</h3><p>This fixed chronological detail is supplemental. The full later sequence remains above, including any subsequent failures.</p><a href="{prefix}-return-detail.png"><img loading="lazy" src="{prefix}-return-detail.png" alt="First recorded return plus sixty minutes of later development"></a>' if return_detail else '')
    title = f"Case {case['rank']:02d} · {case['direction'].upper()}"
    body = f'''<a href="index.html">← Month index</a><h1>{title}</h1>
<p class="warning">Research interpretation, teacher fidelity unverified. Source: NAS100 CFD · Dukascopy USATECHIDXUSD BID. No order, fill, profit or Pepperstone parity claim.</p>
<p>This context was selected by chronology alone. Decision cutoff: <b>{html.escape(display_time(cutoff))}</b> ({html.escape(case['cutoff_basis'])}). Selection is retrospective navigation through the sealed event stream; later observations do not change the saved boxes.</p>
<p><b>4H owner:</b> {html.escape(case['h4_authority_id'])}<br><b>1H owner:</b> {html.escape(case['h1_authority_id'])}<br><b>Linked direction:</b> {case['direction']} · independent same-direction context; 1H is not assumed geometrically inside 4H.<br><b>Link:</b> {html.escape(case['link_id'])}</p>
<p><a href="{prefix}.json">Full event lineage and exact source identities</a></p>
<h2>1. What was available at the decision cutoff</h2>
<p>Complete available 4H and 1H context is retained. A pivot dot is its historical origin; its later known clock is shown separately. A box begins to authorize context only at recognition. An intraminute parent permission is judged at that minute's start by the engine; the chart shows closed-bar observations.</p>
<a href="{prefix}-decision.png"><img src="{prefix}-decision.png" alt="Saved before-return decision view with full higher-timeframe context"></a>
<details><summary>1M swing comparison evidence available by this cutoff</summary>{pivot_table}</details>
<details><summary>Target information actually known at selection</summary>{target_table}</details>
<h2>2. Later historical development through the frozen end</h2>
<p>Everything below is later information. A recorded respect is an assumed separate-candle response, not a fill or winning trade. A child failure does not reverse its 4H or 1H owner. If eligibility was retired, absence of a return event does not prove price never revisited.</p>
<a href="{prefix}-followthrough.png"><img loading="lazy" src="{prefix}-followthrough.png" alt="Later development through frozen run end, including adverse moves and gaps"></a>
{detail}
<h2>Frozen origins and competing hypotheses</h2>{zone_table}
<h2>Exact chronological lineage, including rejection and failure</h2>{timeline}'''
    Path(filename).write_text(page(title, body), encoding='utf-8')


def page(title, body):
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+html.escape(title)+'''</title>
<style>body{font-family:system-ui,sans-serif;color:#1d2938;background:#f4f6f8;margin:0 auto;padding:32px;max-width:1500px;line-height:1.5}h1{font-size:32px}h2{margin-top:36px}a{color:#165eaa}img{display:block;width:100%;height:auto;background:white;border:1px solid #ccd4dd;margin:18px 0}.warning{border-left:5px solid #ae791e;background:#fff4dc;padding:14px}table{border-collapse:collapse;display:block;overflow-x:auto;width:100%;background:white;margin:15px 0;font-size:12px}th,td{text-align:left;vertical-align:top;border:1px solid #dbe1e8;padding:8px;white-space:pre-line;overflow-wrap:anywhere;min-width:110px}th{background:#e8edf4}details{background:white;padding:15px;margin:16px 0}summary{cursor:pointer;font-weight:650}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:15px}.card{background:white;padding:18px;border:1px solid #d4dbe4}input{padding:12px;width:min(95%,700px);font:inherit}code{overflow-wrap:anywhere}small{color:#516173}@media(max-width:650px){body{padding:14px}h1{font-size:25px}}@media print{details{display:block}body{padding:0;background:white}img{break-inside:avoid}}</style><body>'''+body+'</body></html>'


def contact_sheet(run, cases, output):
    plt, _, _, _ = mpl()
    n = max(1, len(cases))
    fig, axes = plt.subplots(n, 2, figsize=(17, max(3.5, 4.5*n)), squeeze=False)
    fig.subplots_adjust(top=.97 if n>1 else .8, bottom=.01, hspace=.17, wspace=.03)
    fig.suptitle('Chronological linked contexts · left: saved decision / right: later development to frozen end\nResearch interpretations · NAS100 CFD Dukascopy BID · no fills or teacher-fidelity claim', fontsize=13)
    if not cases:
        for ax in axes.flat:
            ax.axis('off')
        axes[0,0].text(.03,.7,'NO LINKED CONTEXT ELIGIBLE FOR EXAMPLE SELECTION\nAll BOS and coverage remain in the month index.', fontsize=12)
    for row, case in zip(axes, cases):
        for ax, suffix in zip(row, ('decision','followthrough')):
            path = Path(output).parent / f"case-{case['rank']:02d}-{suffix}.png"
            ax.imshow(plt.imread(path)); ax.axis('off')
            ax.set_title(f"{case['rank']:02d} · {case['direction']} · {suffix}", fontsize=9)
    fig.savefig(output, dpi=100, facecolor='white'); plt.close(fig)


def write_report(run, output, protocol=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    protocol_path = Path(__file__).with_name('SELECTION_PROTOCOL.json')
    protocol_bytes = protocol_path.read_bytes() if protocol is None else canon(protocol).encode()
    protocol = json.loads(protocol_bytes)
    require(protocol.get('id') == VERSION and protocol.get('execution_allowed') is False, 'Wrong report protocol')
    (output/'SELECTION_PROTOCOL.json').write_bytes(protocol_bytes)
    cases = select_cases(run['events'], run['start'], run['end'])
    bos = bos_index(run)
    (output/'all-bos.json').write_text(json.dumps(bos, indent=2)+'\n')
    metadata = dict(report_version=VERSION, built_at=datetime.now(timezone.utc).isoformat(),
                    source_run=run['root'], source_completion_sha256=run['completion_sha256'],
                    input_hashes=run['captured_hashes'], sealed_output_hashes=run['output_hashes'],
                    report_code_sha256=digest(Path(__file__).read_bytes()), selection_protocol_sha256=digest(protocol_bytes),
                    scope=run['spec']['scope'], run_status=run['result']['status'],
                    complete_month_claim=run['result'].get('complete_month_claim', False),
                    integrity='Captured bytes, exact native membership/OHLC, event payload/count/head, input chain and reported counts reconciled. This is not independent detector validation.',
                    execution_allowed=False, teacher_verified=False, performance=None, cases=cases,
                    all_bos_count=len(bos), coverage_by_timeframe={}, synthetic_fixture=run['acquisition'].get('synthetic_fixture',False))
    for tf in (240,60,1):
        metadata['coverage_by_timeframe'][str(tf)] = dict(Counter(c['status'] for c in run['coverage']
            if c['timeframe']==tf and run['start'] <= c['time'] < run['end']))
    (output/'REPORT.json').write_text(json.dumps(metadata,indent=2)+'\n')
    cards = []
    for case in cases:
        prefix = f"case-{case['rank']:02d}"
        related = case_events(run['events'],case)
        view = decision_view(run,case)
        lineage = dict(case=case, decision_cutoff_clocks=clocks(case['decision_cutoff']),
                       decision_event_ids=[e['id'] for e in view['events']],
                       decision_native_bar_times=[b['time'] for b in view['native']],
                       decision_htf_bar_times={str(tf):[b['time'] for b in view['bars'][tf]] for tf in (240,60)},
                       decision_pivots=view['pivots'], events=related,
                       source_completion_sha256=run['completion_sha256'], execution_allowed=False)
        (output/(prefix+'.json')).write_text(json.dumps(lineage,indent=2)+'\n')
        plot_case(run,case,output/(prefix+'-decision.png'))
        plot_case(run,case,output/(prefix+'-followthrough.png'),True)
        returns=[e for e in related if e['kind']=='RETURN_OBSERVED' and e['known_at']>case['decision_cutoff']]
        if returns:
            plot_case(run,case,output/(prefix+'-return-detail.png'),False,
                      detail_end=min(run['end'],returns[0]['known_at']+3600))
        make_case_html(run,case,related,output/(prefix+'.html'),bool(returns))
        counts = Counter(e['kind'] for e in related)
        cards.append(f'<article class="card"><h3><a href="{prefix}.html">{case["rank"]:02d} · {case["direction"].upper()} linked context</a></h3><p>{html.escape(display_time(case["decision_cutoff"]))}</p><p>{html.escape(case["cutoff_basis"])}</p><small>Recorded children: {counts["CHILD_CANDIDATE"]}; returns: {counts["RETURN_OBSERVED"]}; respect observations: {counts["RESPECT_OBSERVED"]}. Branches/routes are not independent trades.</small></article>')
    contact_sheet(run,cases,output/'contact-sheet.png')
    table = html_table(['Known CT / ET','BOS / direction','Reference (origin → known)','Origins','Taps / children','Later observations','Exact event ID'],[
        [display_time(b['known_at']),f"{TF_NAME[b['timeframe']]} {b['direction']} · {b['kind']}",
         f"{b['reference']['price']:g}\n{display_time(b['reference']['time'])}\n→ {display_time(b['reference']['known_at'])}",
         f"{len(b['origin_ids'])} origins · {len(b['formation_rejections'])} rejected branches\n{b['status']}",
         f"{len(b['tap_ids'])} taps / {len(b['child_ids'])} child routes",
         f"{len(b['return_ids'])} returns / {len(b['respect_ids'])} respect / {len(b['failure_ids'])} breach or revocation events",b['event_id']]
        for b in bos])
    coverage_table=html_table(['Timeframe','Study coverage buckets'],[[TF_NAME[int(tf)],str(counts)] for tf,counts in metadata['coverage_by_timeframe'].items()])
    synthetic = ' · SYNTHETIC TEST DATA' if metadata['synthetic_fixture'] else ''
    body=f'''<h1>September context and sequence review{synthetic}</h1>
<p class="warning">Research interpretations only. NAS100 CFD · Dukascopy USATECHIDXUSD BID. Teacher fidelity unverified; no fills, orders or performance claim. This feed is not established as equivalent to Pepperstone.</p>
<p><b>Frozen study:</b> {html.escape(display_time(run['start']))} → {html.escape(display_time(run['end']))} (end exclusive). The month is defined in UTC, not Central-calendar dates.<br><b>Available warmup begins:</b> {html.escape(display_time(run['warm']))}. Earlier major levels are outside this source.<br><b>Run status:</b> {html.escape(run['result']['status'])}; complete-month eligible coverage claim: {str(metadata['complete_month_claim']).lower()}.</p>
<p>{html.escape(run['spec']['scope'].get('prior_exposure',''))}</p>
<p><a href="REPORT.json">Source/code hashes and scope</a> · <a href="SELECTION_PROTOCOL.json">Frozen example selection</a> · <a href="all-bos.json">Every BOS with exact identities</a> · <a href="contact-sheet.png">Full contact sheet</a></p>
<h2>Coverage is part of the evidence</h2>{coverage_table}
<p>Gray chart bands mark scheduled closures; amber marks incomplete, unknown-calendar or contradictory intervals. Incomplete higher bars are absent, not filled. The provider's historical summer schedule is an explicit assumption; the saved run identifies unresolved dates. Source acquisition completeness does not establish complete eligible market coverage.</p>
<h2>Earliest ten linked contexts</h2>
<p>Selection uses link chronology, without looking for favorable returns or requiring an entry review. The first child selection is the saved decision cutoff; with no child, the first tap; with no tap, context recognition/study start. All simultaneous formation branches remain. These pages navigate a sealed historical replay; they are not a claim that this report was generated prospectively.</p>
<div class="cards">{''.join(cards) if cards else '<p><b>No linked context eligible for example selection.</b> The complete BOS index and coverage are still reported below.</p>'}</div>
<h2>Every BOS in the frozen study</h2><p>{len(bos)} structural BOS records; this includes unresolved origins and cases that never tapped, returned or reached an eligible review. Counts describe events, never trades. Full identities and branches are in the JSON index.</p>
<input id="search" aria-label="Filter BOS rows" placeholder="Filter by 4H, 1H, 1M, direction, date or event ID">
<div id="bos">{table}</div>
<script>document.getElementById('search').addEventListener('input',function(){{let q=this.value.toLowerCase();document.querySelectorAll('#bos tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));}});</script>'''
    (output/'index.html').write_text(page('September context and sequence review',body),encoding='utf-8')
    hashes={p.name:digest(p.read_bytes()) for p in output.iterdir() if p.is_file()}
    (output/'REPORT_COMPLETION.json').write_text(json.dumps(dict(status='REPORT_WRITTEN',
        source_completion_sha256=run['completion_sha256'],output_hashes=hashes,execution_allowed=False),indent=2)+'\n')
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,help='Completed sealed runner output, read only')
    parser.add_argument('--output',required=True,help='New exclusive report directory')
    args=parser.parse_args()
    run=load_run(args.run)
    result=write_report(run,args.output)
    print(json.dumps(dict(status='REPORT_WRITTEN',examples=len(result['cases']),all_bos=result['all_bos_count'],
                         output=str(Path(args.output).resolve()),execution_allowed=False)))


if __name__ == '__main__':
    main()
