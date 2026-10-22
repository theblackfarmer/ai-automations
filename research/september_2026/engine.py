"""Streaming September research interpretation. No fills, orders, PnL or market I/O.

API: Engine(spec).feed(tf_minutes, completed_bar), .feed_batch([(tf, bar)]),
.gap(known_at, reason, timeframes=(240,60,1)), .snapshot(), .events.
Bars require time, known_at=time+tf*60, complete=True, source_id and OHLC.
Inputs advance in (known_at, 240-before-60-before-1) order. A timestamp gap is
unclassified unless gap_before='scheduled' AND schedule_evidence is supplied.
The caller supplies native phase-0 bars, actual coverage and scheduled-gap proof.
Never use detector labels from future annotations. Extra input fields are ignored.

Swing logic is an explicit adaptation of causal_swings_r5.py. Native clock/phase,
scheduled bridges, copy isolation and journal IDs are handled here. The pinned
original SHA identifies the source, not a claim of exact byte-for-byte reuse.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math

SWING_SOURCE_SHA256 = '1d575dbac6fe70ed42e694293ea14957acfc83a404cf354e95a2555d093a52ce'
AUTHORITY = 'ASSUMED_RESEARCH_ENCODING'
STEPS = {240: 14400, 60: 3600, 1: 60}
RANK = {240: 0, 60: 1, 1: 2}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def ident(kind, *parts):
    return kind.lower() + ':' + digest(parts)[:24]


def cross(previous, close, reference, direction):
    return previous <= reference < close if direction == 'long' else previous >= reference > close


def overlap(bar, low, high):
    return bar['high'] >= low and bar['low'] <= high


def far_breach(bar, zone):
    return bar['low'] < zone['zone_low'] if zone['direction'] == 'long' else bar['high'] > zone['zone_high']


def far_contact(bar, zone):
    return bar['low'] == zone['zone_low'] if zone['direction'] == 'long' else bar['high'] == zone['zone_high']


def departed(bar, zone):
    return bar['close'] > zone['zone_high'] if zone['direction'] == 'long' else bar['close'] < zone['zone_low']


def validate_bar(tf, bar):
    if tf not in STEPS or type(tf) is not int:
        raise ValueError('Only 240, 60 and 1 minute bars')
    if not isinstance(bar, dict):
        raise ValueError('Bar must be a dict')
    b = {key: deepcopy(bar.get(key)) for key in ('time', 'known_at', 'open', 'high', 'low', 'close', 'source_id', 'complete')}
    if type(b['time']) is not int or b['time'] < 0 or b['time'] % STEPS[tf]:
        raise ValueError('Invalid UTC-phase-zero bar start')
    if type(b['known_at']) is not int or b['known_at'] != b['time'] + STEPS[tf] or b['complete'] is not True:
        raise ValueError('Bar must actually be complete at its native close')
    if not isinstance(b['source_id'], str) or not b['source_id']:
        raise ValueError('Native source_id required')
    for key in ('open', 'high', 'low', 'close'):
        if type(b[key]) not in (int, float) or not math.isfinite(b[key]) or b[key] <= 0:
            raise ValueError('Invalid OHLC')
    if not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
        raise ValueError('Invalid OHLC range')
    gap = bar.get('gap_before', 'unclassified')
    if gap not in ('none', 'unclassified', 'scheduled'):
        raise ValueError('Unknown gap classification')
    if gap == 'scheduled' and not bar.get('schedule_evidence'):
        raise ValueError('Scheduled bridge requires explicit evidence')
    b['gap_before'] = gap
    b['schedule_evidence'] = deepcopy(bar.get('schedule_evidence'))
    return b


class SwingTracker:
    """R5 assumption; known-at never backdated. No assertion of major authority."""
    def __init__(self, step):
        self.step = step
        self.segment = 0
        self.bars = []
        self.pivots = []
        self.run_direction = None
        self.extreme = None

    def reset(self):
        self.segment += 1
        self.bars = []
        self.pivots = []
        self.run_direction = None
        self.extreme = None

    def push(self, bar):
        b = deepcopy(bar)
        prev = self.bars[-1] if self.bars else None
        events = []
        if prev is None:
            self.bars.append(b)
            return events
        move = 'long' if b['close'] > prev['high'] else 'short' if b['close'] < prev['low'] else None
        if self.run_direction is None:
            if move:
                self.run_direction = move
                field = 'high' if move == 'long' else 'low'
                self.extreme = (max if move == 'long' else min)(self.bars + [b], key=lambda x: x[field])
        elif move and move != self.run_direction:
            field = 'high' if self.run_direction == 'long' else 'low'
            extension = b[field] > self.extreme[field] if field == 'high' else b[field] < self.extreme[field]
            if extension:
                events.append({'kind': 'AMBIGUOUS_TURN', 'known_at': b['known_at'], 'bar_start': b['time']})
                self.reset()
                self.bars.append(b)
                return events
            peers = [p for p in self.pivots if p['kind'] == field]
            value = self.extreme[field]
            label = 'H' if field == 'high' else 'L'
            comparator = deepcopy(peers[-1]) if peers else None
            if comparator:
                old = comparator['price']
                label = ('HH' if value > old else 'LH' if value < old else 'EQH') if field == 'high' else ('HL' if value > old else 'LL' if value < old else 'EQL')
                # Comparators contain identity/clocks, not recursive comparator chains.
                comparator.pop('comparator', None)
            p = {'id': ident('pivot', self.step, self.segment, field, self.extreme['time']),
                 'kind': field, 'time': self.extreme['time'], 'known_at': b['known_at'],
                 'price': value, 'label': label, 'comparator': comparator,
                 'source_id': self.extreme['source_id']}
            self.pivots.append(p)
            events.append({'kind': 'PIVOT_CONFIRMED', 'known_at': b['known_at'], 'pivot': deepcopy(p)})
            self.run_direction = move
            self.extreme = b
        elif self.run_direction is not None:
            field = 'high' if self.run_direction == 'long' else 'low'
            if b[field] == self.extreme[field]:
                events.append({'kind': 'EQUAL_EXTREMUM', 'known_at': b['known_at'], 'price': b[field], 'extremum': field})
            elif (b[field] > self.extreme[field] if field == 'high' else b[field] < self.extreme[field]):
                self.extreme = b
        self.bars.append(b)
        return events


def progressing(pivots, direction, pairs=1, after=None):
    wanted = {'high': 'HH' if direction == 'long' else 'LH', 'low': 'HL' if direction == 'long' else 'LL'}
    selected = {}
    for kind in ('high', 'low'):
        peers = [p for p in pivots if p['kind'] == kind]
        if len(peers) < pairs + 1:
            return None
        recent = peers[-pairs:]
        if any(p['label'] != wanted[kind] or (after is not None and p['time'] <= after) for p in recent):
            return None
        # Recheck actual strict comparisons; do not trust an injected label.
        chain = peers[-pairs-1:]
        for previous, point in zip(chain, chain[1:]):
            if not (point['price'] > previous['price'] if direction == 'long' else point['price'] < previous['price']):
                return None
            if point.get('comparator', {}).get('id') != previous['id']:
                return None
        selected[kind] = deepcopy(chain)
    return selected


def association(pivots, direction, bar_start, reference=None, scope_external=False):
    kind = 'high' if direction == 'long' else 'low'
    opposite = 'low' if direction == 'long' else 'high'
    # Choose the actual latest pivots FIRST. Filtering by knowledge time first
    # silently falls back to an older/easier reference or stale pullback when the
    # latest confirmation arrives at this bar's opening or close. Wait instead.
    refs = [p for p in pivots if p['kind'] == kind]
    pulls = [p for p in pivots if p['kind'] == opposite]
    if not refs or not pulls:
        return None
    ref = refs[-1] if reference is None else reference
    pull = pulls[-1]
    if ref['known_at'] >= bar_start or pull['known_at'] >= bar_start or ref['time'] >= pull['time']:
        return None
    # Local entries/bootstrap use the immediately preceding directional pivot.
    # An established HTF authority instead names its external extreme: an internal
    # lower high/higher low cannot replace that harder major continuation level.
    before = [p for p in refs if p['time'] < pull['time']]
    if not scope_external and (ref['id'] != refs[-1]['id'] or not before or before[-1]['id'] != ref['id']):
        return None
    return deepcopy(ref), deepcopy(pull)


def formation_candidates(bars, pivot, reference, direction, bos_bar):
    """Return accepted/rejected frozen hypotheses; never inspect post-BOS bars.

    A: exact historical opposite-color run. B: backward overlap with the next
    accepted candle, opposite-colored OR inside that candle; forward overlap
    with the FIXED pivot seed envelope. First directional close outside that
    seed is departure and is excluded. These are declared research hypotheses.
    """
    prefix = [deepcopy(b) for b in bars if b['known_at'] <= bos_bar['known_at']]
    index = next((i for i, b in enumerate(prefix) if b['time'] == pivot['time']), None)
    if index is None or pivot['time'] >= bos_bar['time']:
        return [], [{'branch_names': ['A_OPPOSITE_RUN', 'B_WHOLE_BASE'], 'reason': 'PIVOT_SOURCE_MISSING_OR_NOT_BEFORE_BOS'}]
    opposite = lambda b: b['close'] < b['open'] if direction == 'long' else b['close'] > b['open']
    choices = []
    end = index if opposite(prefix[index]) else index - 1
    if end >= 0 and opposite(prefix[end]):
        begin = end
        while begin > 0 and prefix[begin]['time'] == prefix[begin-1]['known_at'] and opposite(prefix[begin-1]):
            begin -= 1
        choices.append(('A_OPPOSITE_RUN', prefix[begin:end+1]))
    else:
        choices.append(('A_OPPOSITE_RUN', []))
    seed = prefix[index]
    members = [seed] if seed['time'] > reference['time'] else []
    if members:
        cursor = index - 1
        later = seed
        while cursor >= 0:
            b = prefix[cursor]
            inside = b['low'] >= later['low'] and b['high'] <= later['high']
            if b['time'] <= reference['time'] or b['known_at'] != later['time'] or not overlap(b, later['low'], later['high']) or not (opposite(b) or inside):
                break
            members.insert(0, b)
            later = b
            cursor -= 1
        previous = seed
        for b in prefix[index+1:]:
            if b['time'] >= bos_bar['time'] or b['time'] != previous['known_at']:
                break
            exits = b['close'] > seed['high'] if direction == 'long' else b['close'] < seed['low']
            if exits or not overlap(b, seed['low'], seed['high']):
                break
            members.append(b)
            previous = b
    choices.append(('B_WHOLE_BASE', members))
    accepted, rejected, dedup = [], [], {}
    for name, members in choices:
        if not members:
            rejected.append({'branch_names': [name], 'reason': 'NO_ELIGIBLE_MEMBERS'})
            continue
        key = tuple(b['time'] for b in members)
        z = dict(direction=direction, branch_names=[name], member_times=list(key),
                 source_ids=[b['source_id'] for b in members], origin_start=key[0], origin_end=key[-1],
                 zone_low=min(b['low'] for b in members), zone_high=max(b['high'] for b in members),
                 selected_known_at=bos_bar['known_at'],
                 physical_origin_id=ident('physical_origin', members[0]['known_at']-members[0]['time'], direction, list(key)))
        # Both branches must be responsible to the named post-reference pullback.
        reason = None
        if z['origin_start'] <= reference['time']:
            reason = 'ORIGIN_NOT_STRICTLY_AFTER_BROKEN_REFERENCE'
        elif any(far_breach(b, z) for b in prefix if b['time'] > z['origin_end'] and b['time'] <= bos_bar['time']):
            reason = 'FAR_EDGE_BREACH_BEFORE_RECOGNITION'
        if reason:
            rejected.append(dict(z, reason=reason))
        elif key in dedup:
            dedup[key]['branch_names'].append(name)
        else:
            dedup[key] = z
            accepted.append(z)
    return accepted, rejected


class Engine:
    """All public results are deep copies. The internal journal is append-only."""
    def __init__(self, spec):
        if not isinstance(spec, dict) or spec.get('id') != 'SEPTEMBER_4H_1H_1M_V1' or spec.get('execution_allowed') is not False:
            raise ValueError('Explicit frozen September research spec required')
        self.spec = deepcopy(spec)
        self.spec_hash = digest(spec)
        self._events = []
        self._ids = set()
        self._last_order = None
        self._last = {}
        self._coverage_through = {tf: 0 for tf in STEPS}
        self._track = {tf: SwingTracker(step) for tf, step in STEPS.items()}
        self._authorities = {}
        self._active = {240: None, 60: None}
        self._bos = {}
        self._zones = {}
        self._links = {}
        self._taps = {}
        self._children = {}
        self._local_used = set()

    @property
    def events(self):
        return deepcopy(self._events)

    def _emit(self, kind, known_at, tf, **data):
        payload = dict(kind=kind, known_at=known_at, timeframe=tf, direction=None,
                       source_id=None, authority=AUTHORITY, execution_allowed=False,
                       teacher_verified=False, spec_hash=self.spec_hash)
        payload.update(deepcopy(data))
        if tf == 1 and 'bar_start' in payload:
            payload['htf_context_as_of'] = payload['bar_start']
        payload['id'] = ident(kind, payload)
        if payload['id'] not in self._ids:
            self._events.append(payload)
            self._ids.add(payload['id'])
        return deepcopy(payload)

    def snapshot(self):
        return deepcopy(dict(spec_hash=self.spec_hash, last_order=self._last_order,
                             authorities=self._authorities, active_authorities=self._active,
                             bos=self._bos, zones=self._zones, links=self._links, taps=self._taps,
                             children=self._children, coverage_through=self._coverage_through,
                             event_count=len(self._events), event_head=digest(self._events),
                             execution_allowed=False, teacher_verified=False))

    def _revoke(self, tf, known, reason, source_id=None):
        aid = self._active.get(tf)
        if not aid:
            return
        a = self._authorities[aid]
        a['active'] = False
        a['revoked_at'] = known
        a['revocation_reason'] = reason
        self._active[tf] = None
        self._emit('AUTHORITY_REVOKED', known, tf, authority_id=aid, direction=a['direction'], reason=reason, source_id=source_id)
        for link in self._links.values():
            if link['active'] and aid in (link['h4_authority_id'], link['h1_authority_id']):
                link['active'] = False
                link['revoked_at'] = known
                self._emit('LINK_REVOKED', known, tf, link_id=link['id'], reason=reason, direction=link['direction'])
        for z in self._zones.values():
            if z['authority_id'] == aid and z['active']:
                z['active'] = False
                z['retired_at'] = known
                z['retirement_reason'] = reason
        for c in self._children.values():
            link = self._links[c['link_id']]
            if not link['active'] and c['active']:
                c['active'] = False
                c['status'] = 'PARENT_PERMISSION_REVOKED'
                c['permission_revoked_at'] = known
                c['permission_revocation_reason'] = reason
                self._emit('CHILD_PERMISSION_REVOKED', known, 1, child_id=c['id'], zone_id=c['id'], reason=reason, direction=c['direction'])

    def gap(self, known_at, reason, timeframes=(240, 60, 1), scheduled=False, schedule_evidence=None):
        if type(known_at) is not int or known_at < 0 or not isinstance(reason, str) or not reason:
            raise ValueError('Explicit gap clock/reason required')
        if self._last_order and known_at < self._last_order[0]:
            raise ValueError('Cannot backdate a gap')
        # A caller may supply a generator. Materialize ONCE before validating,
        # emitting, or mutating state; validation must not consume the action.
        try:
            timeframes = tuple(timeframes)
        except TypeError as exc:
            raise ValueError('Gap timeframes must be a nonempty iterable') from exc
        if not timeframes or any(type(tf) is not int or tf not in STEPS for tf in timeframes) or len(set(timeframes)) != len(timeframes):
            raise ValueError('Invalid gap timeframe')
        if type(scheduled) is not bool:
            raise ValueError('scheduled must be an explicit boolean')
        if scheduled and not schedule_evidence:
            raise ValueError('Scheduled bridge requires source evidence')
        start = len(self._events)
        self._emit('SCHEDULED_COVERAGE_BRIDGE' if scheduled else 'UNCLASSIFIED_COVERAGE_GAP', known_at, None,
                   reason=reason, timeframes=list(timeframes), schedule_evidence=deepcopy(schedule_evidence))
        for tf in timeframes:
            if scheduled:
                self._coverage_through[tf] = max(self._coverage_through[tf], known_at)
            else:
                self._track[tf].reset()
                self._revoke(tf, known_at, 'UNCLASSIFIED_COVERAGE_GAP')
                if tf == 1:
                    for tap in self._taps.values():
                        tap['active'] = False
                    for c in self._children.values():
                        if c['active']:
                            c['active'] = False
                            c['status'] = 'COVERAGE_UNKNOWN'
                            self._emit('CHILD_COVERAGE_UNKNOWN', known_at, 1, child_id=c['id'], zone_id=c['id'], direction=c['direction'])
        # Reached target flags are deliberately never reset by coverage changes.
        self._last_order = max(self._last_order or (known_at, -1), (known_at, -1))
        return deepcopy(self._events[start:])

    def feed_batch(self, records):
        prepared = [(tf, validate_bar(tf, b)) for tf, b in records]
        prepared.sort(key=lambda x: (x[1]['known_at'], RANK[x[0]]))
        order = self._last_order
        times = {tf: b['time'] for tf, b in self._last.items()}
        for tf, b in prepared:
            key = (b['known_at'], RANK[tf])
            if order is not None and key <= order:
                raise ValueError('Duplicate, backward or same-cutoff reversed order')
            if tf in times and b['time'] <= times[tf]:
                raise ValueError('Duplicate native bar')
            order, times[tf] = key, b['time']
        start = len(self._events)
        for tf, b in prepared:
            self.feed(tf, b)
        return deepcopy(self._events[start:])

    def feed(self, timeframe_minutes, bar):
        tf = timeframe_minutes
        b = validate_bar(tf, bar)
        key = (b['known_at'], RANK[tf])
        if self._last_order is not None and key <= self._last_order:
            raise ValueError('Bars must advance known-time; common cutoff order is 4H, 1H, 1M')
        previous = self._last.get(tf)
        if previous and b['time'] <= previous['time']:
            raise ValueError('Duplicate native candle')
        start = len(self._events)
        if previous and b['time'] != previous['known_at']:
            scheduled = b['gap_before'] == 'scheduled'
            self.gap(b['known_at'], 'NATIVE_TIMESTAMP_DISCONTINUITY',
                     timeframes=(tf,) if scheduled else (240, 60, 1),
                     scheduled=scheduled, schedule_evidence=b['schedule_evidence'])
        self._last_order = key
        self._last[tf] = deepcopy(b)
        self._coverage_through[tf] = max(self._coverage_through[tf], b['known_at'])
        se = self._track[tf].push(b)
        for event in se:
            data = {k: v for k, v in event.items() if k not in ('kind', 'known_at')}
            self._emit(event['kind'], b['known_at'], tf, source_id=b['source_id'], **data)
        if any(e['kind'] == 'AMBIGUOUS_TURN' for e in se):
            # Ambiguity resets local recognition, never asserts a market reversal.
            if tf == 1:
                for tap in self._taps.values():
                    tap['transition_segment'] = self._track[1].segment
            self._emit('LOCAL_STRUCTURE_RESET', b['known_at'], tf, reason='AMBIGUOUS_INTRABAR_TURN', source_id=b['source_id'])
        if tf in (240, 60):
            self._higher(tf, b, previous, se)
            self._link(b['known_at'])
        else:
            self._minute(b, previous)
        return deepcopy(self._events[start:])

    def _higher(self, tf, b, previous, swing_events):
        a = self._authorities.get(self._active[tf])
        if a and (b['close'] < a['protected']['price'] if a['direction'] == 'long' else b['close'] > a['protected']['price']):
            self._revoke(tf, b['known_at'], 'OWNER_CLOSE_THROUGH_PROTECTED_PIVOT', b['source_id'])
            a = None
        for event in swing_events:
            if event['kind'] != 'PIVOT_CONFIRMED':
                continue
            p = event['pivot']
            for bos in self._bos.values():
                endpoint_kind = 'high' if bos['direction'] == 'long' else 'low'
                if bos['timeframe'] == tf and bos['target'] is None and p['kind'] == endpoint_kind and p['known_at'] > bos['known_at'] and p['time'] >= bos['bar_start']:
                    bos['target'] = dict(price=p['price'], pivot=deepcopy(p), known_at=p['known_at'], reached=False, reached_at=None)
                    self._emit('ORIGINAL_TARGET_KNOWN', b['known_at'], tf, bos_id=bos['id'], authority_id=bos['authority_id'], direction=bos['direction'], target=bos['target'], source_id=b['source_id'])
            if a and p['kind'] == ('high' if a['direction'] == 'long' else 'low'):
                if p['price'] > a['external']['price'] if a['direction'] == 'long' else p['price'] < a['external']['price']:
                    a['external'] = deepcopy(p)
        if previous is None or not self._track[tf].bars or len(self._track[tf].bars) < 2:
            return
        pivots = self._track[tf].pivots
        directions = [a['direction']] if a else ['long', 'short']
        for direction in directions:
            if a:
                if a['external']['id'] in a['broken_reference_ids']:
                    continue
                pair = association(pivots, direction, b['time'], a['external'], scope_external=True)
            else:
                evidence = progressing(pivots, direction, 1)
                if not evidence or any(p['known_at'] >= b['time'] for chain in evidence.values() for p in chain):
                    continue
                pair = association(pivots, direction, b['time'])
            if pair is None:
                continue
            reference, pullback = pair
            if not cross(previous['close'], b['close'], reference['price'], direction):
                continue
            bos_id = ident('bos', tf, b['time'], direction, reference['id'], pullback['id'])
            if bos_id in self._bos:
                continue
            if a is None:
                aid = ident('authority', tf, bos_id)
                a = dict(id=aid, timeframe=tf, direction=direction, known_at=b['known_at'], active=True,
                         protected=deepcopy(pullback), external=deepcopy(reference), initial_bos_id=bos_id, broken_reference_ids=[])
                self._authorities[aid] = a
                self._active[tf] = aid
                bos_type = 'BOOTSTRAP_OR_REESTABLISHMENT'
            else:
                bos_type = 'EXTERNAL_CONTINUATION'
                a['protected'] = deepcopy(pullback)
            a['broken_reference_ids'].append(reference['id'])
            a['last_bos_id'] = bos_id
            a['last_bos_known_at'] = b['known_at']
            record = dict(id=bos_id, timeframe=tf, direction=direction, known_at=b['known_at'], bar_start=b['time'],
                          authority_id=a['id'], reference=reference, pullback=pullback, target=None)
            self._bos[bos_id] = record
            self._emit('MAJOR_BOS', b['known_at'], tf, bos_id=bos_id, authority_id=a['id'], direction=direction,
                       bos_type=bos_type, reference=reference, pullback=pullback, bar_start=b['time'], source_id=b['source_id'])
            zones, rejected = formation_candidates(self._track[tf].bars, pullback, reference, direction, b)
            for reject in rejected:
                self._emit('FORMATION_REJECTED', b['known_at'], tf, bos_id=bos_id, authority_id=a['id'], **reject)
            for geometry in zones:
                zid = ident('zone', tf, bos_id, geometry['member_times'])
                z = dict(geometry, id=zid, zone_id=zid, timeframe=tf, authority_id=a['id'], bos_id=bos_id,
                         active=True, tapped_links=[], departed_at=b['known_at'] if departed(b, geometry) else None)
                self._zones[zid] = z
                self._emit('ZONE_CANDIDATE', b['known_at'], tf, authority_id=a['id'], bos_id=bos_id, zone_id=zid,
                           **geometry, source_id=b['source_id'], bar_start=b['time'])
            break

    def _link(self, known):
        h4 = self._authorities.get(self._active[240])
        h1 = self._authorities.get(self._active[60])
        if not h4 or not h1 or h4['direction'] != h1['direction']:
            return
        lid = ident('link', h4['id'], h1['id'])
        if lid not in self._links:
            link = dict(id=lid, h4_authority_id=h4['id'], h1_authority_id=h1['id'], direction=h4['direction'],
                        known_at=known, active=True, relationship='SAME_DIRECTION_CHILD_CONTEXT_NOT_GEOMETRIC_REFINEMENT')
            self._links[lid] = link
            self._emit('HTF_LINK', known, None, link_id=lid, **{k:v for k,v in link.items() if k not in ('id','known_at')})

    @staticmethod
    def _authority_at(authority, bar_start):
        if authority['known_at'] > bar_start:
            return False
        return authority['active'] or (
            authority.get('revocation_reason') == 'OWNER_CLOSE_THROUGH_PROTECTED_PIVOT'
            and authority.get('revoked_at', -1) > bar_start)

    @staticmethod
    def _zone_at(zone, bar_start):
        return zone['active'] or (
            zone.get('retirement_reason') == 'OWNER_CLOSE_THROUGH_PROTECTED_PIVOT'
            and zone.get('retired_at', -1) > bar_start)

    @staticmethod
    def _child_at(child, bar_start):
        return child['active'] or (
            child.get('permission_revocation_reason') == 'OWNER_CLOSE_THROUGH_PROTECTED_PIVOT'
            and child.get('permission_revoked_at', -1) > bar_start)

    def _eligible(self, link, bar):
        if link['known_at'] > bar['time'] or (not link['active'] and link.get('revoked_at', -1) <= bar['time']):
            return False
        for tf in (240,60):
            aid = link['h4_authority_id'] if tf == 240 else link['h1_authority_id']
            if not self._authority_at(self._authorities[aid], bar['time']):
                return False
            expected = (bar['time'] // STEPS[tf]) * STEPS[tf]
            if self._coverage_through[tf] < expected:
                return False
        return True

    def _target_flags(self, zone, bar_start):
        """HTF facts must have been available when the deciding M1 bar began.

        H1/H4 are processed first at a shared closing cutoff. Their new endpoint
        remains in the global journal, but cannot authorize that just-closed M1.
        A retouch of an ALREADY-known target during the current M1 still counts.
        """
        target = self._bos[zone['bos_id']]['target']
        if target is not None and target['known_at'] > bar_start:
            target = None
        return dict(target_known=target is not None, target=deepcopy(target), target_already_reached=bool(target and target['reached']))

    def _minute(self, b, previous):
        # Known target recognition precedes only later bars, not its own endpoint.
        for bos in self._bos.values():
            target = bos['target']
            if target and not target['reached'] and b['time'] >= target['known_at']:
                touched = b['high'] >= target['price'] if bos['direction'] == 'long' else b['low'] <= target['price']
                if touched:
                    target['reached'] = True
                    target['reached_at'] = b['known_at']
                    self._emit('ORIGINAL_TARGET_REACHED', b['known_at'], 1, bos_id=bos['id'], authority_id=bos['authority_id'], direction=bos['direction'], target=target, source_id=b['source_id'], bar_start=b['time'])
        # Far-edge zone wick events are distinct from owner-timeframe authority.
        # A parent's failure on this minute must not erase the child's own
        # observed failure. This local set grants no continuing permission.
        children_retired_this_minute = set()
        for z in self._zones.values():
            if not self._zone_at(z,b['time']) or b['time'] < z['selected_known_at']:
                continue
            if far_breach(b,z):
                z['active'] = False
                z['retired_at'] = b['known_at']
                z['retirement_reason'] = 'PARENT_ZONE_FAR_EDGE_WICK_BREACH'
                self._emit('PARENT_ZONE_BREACHED', b['known_at'], 1, zone_id=z['id'], authority_id=z['authority_id'], direction=z['direction'], owner_timeframe=z['timeframe'], source_id=b['source_id'], bar_start=b['time'])
                for tap in self._taps.values():
                    if tap['zone_id']==z['id']:tap['active']=False
                for c in self._children.values():
                    if c['active'] and c['parent_zone_id']==z['id']:
                        children_retired_this_minute.add(c['id'])
                        c['active']=False;c['status']='PARENT_ZONE_RETIRED'
                        self._emit('CHILD_PERMISSION_REVOKED',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],reason='PARENT_ZONE_FAR_EDGE_WICK_BREACH')
            elif far_contact(b,z):
                self._emit('PARENT_ZONE_EDGE_CONTACT',b['known_at'],1,zone_id=z['id'],direction=z['direction'],owner_timeframe=z['timeframe'],source_id=b['source_id'],bar_start=b['time'])
            if z['active'] and z['departed_at'] is None and departed(b,z):
                z['departed_at']=b['known_at']
        # Older children are observed before new candidates are created.
        for c in self._children.values():
            # Preserve only the far-breach path for a child just retired by its
            # parent. The selection clock and inherited HTF permission checks
            # still apply; respect/review and later-minute reactivation do not.
            parent_and_child_failed = c['id'] in children_retired_this_minute and far_breach(b,c)
            if (not self._child_at(c,b['time']) and not parent_and_child_failed) or b['time'] < c['selected_known_at']:
                continue
            if not self._eligible(self._links[c['link_id']],b):
                self._emit('CHILD_CONTEXT_UNAVAILABLE',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],source_id=b['source_id'],bar_start=b['time'])
                continue
            if far_breach(b,c):
                # OHLC establishes overlap and breach, but not their intrabar order.
                # Keep the actual first return even when that candle also fails.
                if c['return_known_at'] is None and c['departed_at'] is not None and b['time'] >= c['departed_at'] and overlap(b,c['zone_low'],c['zone_high']):
                    c['return_known_at']=b['known_at']
                    self._emit('RETURN_OBSERVED',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],
                               zone_low=c['zone_low'],zone_high=c['zone_high'],failed_same_bar=True,intrabar_order_unknown=True,
                               source_id=b['source_id'],bar_start=b['time'])
                c['active']=False;c['status']='CHILD_FAR_EDGE_BREACH';c['failed_at']=b['known_at']
                self._emit('CHILD_ZONE_BREACHED',b['known_at'],1,child_id=c['id'],zone_id=c['id'],parent_zone_id=c['parent_zone_id'],direction=c['direction'],source_id=b['source_id'],bar_start=b['time'])
                continue
            if far_contact(b,c):
                self._emit('CHILD_ZONE_EDGE_CONTACT',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],source_id=b['source_id'],bar_start=b['time'])
            if c['departed_at'] is None:
                if departed(b,c):
                    c['departed_at']=b['known_at']
                    if c['active']:c['status']='AWAIT_RETURN'
                    self._emit('CHILD_DEPARTURE_OBSERVED',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],source_id=b['source_id'],bar_start=b['time'])
                continue
            if c['return_known_at'] is None and b['time'] >= c['departed_at'] and overlap(b,c['zone_low'],c['zone_high']):
                c['return_known_at']=b['known_at']
                if c['active']:c['status']='RETURN_OBSERVED'
                self._emit('RETURN_OBSERVED',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],zone_low=c['zone_low'],zone_high=c['zone_high'],source_id=b['source_id'],bar_start=b['time'])
                continue
            if c['return_known_at'] is not None and c['respect_known_at'] is None and b['time']>=c['return_known_at'] and departed(b,c):
                c['respect_known_at']=b['known_at']
                if c['active']:c['status']='RESPECT_OBSERVED'
                flags=self._target_flags(self._zones[c['parent_zone_id']],b['time'])
                self._emit('RESPECT_OBSERVED',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],respect_rule='SEPARATE_CLOSE_BEYOND_NEAR_EDGE_NO_FAR_BREACH',zone_low=c['zone_low'],zone_high=c['zone_high'],source_id=b['source_id'],bar_start=b['time'],**flags)
                status='BLOCKED_TARGET_UNKNOWN' if not flags['target_known'] else 'BLOCKED_TARGET_ALREADY_REACHED' if flags['target_already_reached'] else 'RESEARCH_REVIEW_ONLY'
                c['review_status']=status
                self._emit('ENTRY_REVIEW' if status=='RESEARCH_REVIEW_ONLY' else 'ENTRY_REVIEW_BLOCKED',b['known_at'],1,child_id=c['id'],zone_id=c['id'],direction=c['direction'],review_status=status,guard_required=True,teacher_fidelity_required=True,source_id=b['source_id'],bar_start=b['time'],**flags)
        for link in self._links.values():
            if not self._eligible(link,b):
                continue
            for z in self._zones.values():
                if not self._zone_at(z,b['time']) or z['authority_id'] not in (link['h4_authority_id'],link['h1_authority_id']) or z['direction']!=link['direction'] or link['id'] in z['tapped_links']:
                    continue
                recognition=max(z['selected_known_at'],link['known_at'])
                if b['time'] < recognition or z['departed_at'] is None or b['time']<z['departed_at'] or not overlap(b,z['zone_low'],z['zone_high']):
                    continue
                tid=ident('tap',z['id'],link['id'],b['time'])
                tap=dict(id=tid,zone_id=z['id'],link_id=link['id'],direction=z['direction'],bar_start=b['time'],known_at=b['known_at'],active=bool(z['active'] and link['active']),
                         transition_segment=self._track[1].segment,pre_tap_pivots=deepcopy([p for p in self._track[1].pivots if p['known_at']<=b['known_at']]))
                self._taps[tid]=tap;z['tapped_links'].append(link['id'])
                self._emit('HTF_TAP',b['known_at'],1,tap_id=tid,zone_id=z['id'],link_id=link['id'],direction=z['direction'],owner_timeframe=z['timeframe'],pre_tap_pivots=tap['pre_tap_pivots'],source_id=b['source_id'],bar_start=b['time'])
        if previous is None:
            return
        for tap in self._taps.values():
            z=self._zones[tap['zone_id']]
            if not tap['active'] or not self._zone_at(z,b['time']) or not self._eligible(self._links[tap['link_id']],b) or b['time']<=tap['bar_start']:
                continue
            pivots=self._track[1].pivots
            evidence=progressing(pivots,tap['direction'],pairs=2,after=tap['bar_start'])
            if not evidence:
                continue
            structure_known=max(p['known_at'] for chain in evidence.values() for p in chain[-2:])
            if structure_known>=b['time']:
                continue
            pair=association(pivots,tap['direction'],b['time'])
            if pair is None:
                continue
            reference,pullback=pair
            if not cross(previous['close'],b['close'],reference['price'],tap['direction']):
                continue
            key=(tap['id'],reference['id'],pullback['id'])
            if key in self._local_used:
                continue
            self._local_used.add(key)
            local_bos=ident('m1bos',*key,b['time'])
            self._emit('M1_TRANSITION_BOS',b['known_at'],1,bos_id=local_bos,tap_id=tap['id'],link_id=tap['link_id'],direction=tap['direction'],reference=reference,pullback=pullback,structure_known_at=structure_known,comparison_chains=evidence,source_id=b['source_id'],bar_start=b['time'])
            candidates,rejections=formation_candidates(self._track[1].bars,pullback,reference,tap['direction'],b)
            for rejection in rejections:
                self._emit('CHILD_FORMATION_REJECTED',b['known_at'],1,bos_id=local_bos,tap_id=tap['id'],parent_zone_id=z['id'],**rejection)
            for geometry in candidates:
                cid=ident('child',tap['id'],local_bos,geometry['member_times'])
                flags=self._target_flags(z,b['time'])
                opposing=[dict(zone_id=o['id'],timeframe=o['timeframe'],zone_low=o['zone_low'],zone_high=o['zone_high'],known_at=o['selected_known_at']) for o in self._zones.values() if self._zone_at(o,b['time']) and o['direction']!=tap['direction'] and o['selected_known_at']<=b['time']]
                c=dict(geometry,id=cid,zone_id=cid,child_id=cid,parent_zone_id=z['id'],tap_id=tap['id'],link_id=tap['link_id'],bos_id=local_bos,
                       active=True,departed_at=b['known_at'] if departed(b,geometry) else None,return_known_at=None,respect_known_at=None,
                       status='AWAIT_RETURN' if departed(b,geometry) else 'AWAIT_DEPARTURE',reference=reference,pullback=pullback,
                       decision_target=flags,opposing_known_zones=opposing)
                self._children[cid]=c
                self._emit('CHILD_CANDIDATE',b['known_at'],1,child_id=cid,zone_id=cid,parent_zone_id=z['id'],tap_id=tap['id'],link_id=tap['link_id'],bos_id=local_bos,
                           reference=reference,pullback=pullback,structure_known_at=structure_known,opposing_known_zones=opposing,source_id=b['source_id'],bar_start=b['time'],**geometry,**flags)
                # Selection describes the just-closed minute using its inherited
                # context. A simultaneously revoked owner cannot permit a later
                # return, even though it was available when this minute began.
                if not self._links[tap['link_id']]['active'] or not z['active']:
                    c['active']=False;c['status']='PARENT_PERMISSION_REVOKED'
                    c['permission_revoked_at']=b['known_at']
                    c['permission_revocation_reason']='OWNER_CLOSE_THROUGH_PROTECTED_PIVOT'
                    self._emit('CHILD_PERMISSION_REVOKED',b['known_at'],1,child_id=cid,zone_id=cid,direction=c['direction'],reason=c['permission_revocation_reason'])
