"""Explicit sourced correction mode; legacy engine and frozen SPEC are unchanged.

Supplied prior HTF context is NOT autonomous parent discovery or verified parent
validity. Local reference/origin are derived from observed candles, never supplied
as answers. Full-origin-candle geometry and separate-close response are explicit
research conventions. Candidate events are neither ENTRY_REVIEW nor fills.
"""
from copy import deepcopy
from math import isfinite
from engine import ident, digest, cross, overlap

MODE = 'SOURCED_CORRECTION_OBSERVER_V1'

def resolve_outside_reversal(bar, members, previous_high, previous_low, leg, cutoff):
    """Resolve only a complete outside reversal whose final extreme preceded crossing.

    leg is the incoming leg (+1 up, -1 down). Every member is a native 1M
    OHLC dict with time and known_at. No member ordering within a minute is
    inferred. The returned extreme is independent of any colored footprint.
    """
    rejected = {"resolved": False, "extreme_time": None, "crossing_time": None}
    try:
        start, end = bar['time'], bar['known_at']
        # A cutoff is an integer epoch, never a float, boolean or NaN.
        if (type(cutoff) is not int or type(leg) is not int or leg not in (-1, 1)
                or type(start) is not int or type(end) is not int or start < 0
                or start % 300 or end != start + 300 or cutoff < end):
            return rejected
        if len(members) != 5 or [b['time'] for b in members] != list(range(start, end, 60)):
            return rejected
        for b in [bar, *members]:
            if (type(b['time']) is not int or type(b['known_at']) is not int
                    or b['time'] % 60 or b['known_at'] % 60):
                return rejected
            if not all(type(b[k]) in (int, float) and isfinite(b[k]) for k in ('open', 'high', 'low', 'close')):
                return rejected
            if not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
                return rejected
        if any(b['known_at'] != b['time'] + 60 or b['known_at'] > cutoff for b in members):
            return rejected
        aggregate = {'open': members[0]['open'], 'high': max(b['high'] for b in members),
                     'low': min(b['low'] for b in members), 'close': members[-1]['close']}
        if any(bar[k] != v for k, v in aggregate.items()):
            return rejected
        if not all(type(x) in (int, float) and isfinite(x) for x in (previous_high, previous_low)) or previous_high < previous_low:
            return rejected
        down = leg == -1
        if not (bar['low'] < previous_low and bar['close'] > previous_high if down
                else bar['high'] > previous_high and bar['close'] < previous_low):
            return rejected
        key, boundary = ('low', previous_high) if down else ('high', previous_low)
        extreme_time = max(b['time'] for b in members if b[key] == bar[key])
        crossing_time = next(b['time'] for b in members
                             if (b['high'] > boundary if down else b['low'] < boundary))
        if extreme_time >= crossing_time:
            return rejected
        return {'resolved': True, 'extreme_time': extreme_time, 'crossing_time': crossing_time,
                'extreme_price': bar[key], 'known_at': end}
    except (KeyError, TypeError, ValueError, StopIteration):
        return rejected


def correction_turn(pivots, direction, bar_start, after, known_at=None):
    """Persistent corrective LL/HH origin and its reference, never a later HL/LH.

    The origin may become confirmed at this break CLOSE; the reference must
    already exist at bar start. Returned objects are isolated frozen copies.
    """
    if direction not in ('long', 'short'):
        raise ValueError('Explicit long/short direction required')
    cutoff = bar_start if known_at is None else known_at
    origin_kind = 'low' if direction == 'long' else 'high'
    reference_kind = 'high' if direction == 'long' else 'low'
    origins = [p for p in pivots if p['kind'] == origin_kind]
    references = [p for p in pivots if p['kind'] == reference_kind]
    if len(origins) < 2 or not references:
        return None
    wanted = 'LL' if direction == 'long' else 'HH'
    # Bind a genuine new corrective extreme to ITS reference. Later
    # internal HL/LH confirms the recovery, but cannot erase the responsible
    # correction extreme or substitute an easier internal reaction level.
    pairs = [(origin, comparator) for comparator, origin in zip(origins, origins[1:])
             if origin['time'] >= after and origin['label'] == wanted
             and origin.get('comparator', {}).get('id') == comparator['id']
             and (origin['price'] < comparator['price'] if direction == 'long'
                  else origin['price'] > comparator['price'])]
    if not pairs:
        return None
    origin, comparator = pairs[0]
    for candidate, prior in pairs[1:]:
        if candidate['price'] < origin['price'] if direction == 'long' else candidate['price'] > origin['price']:
            origin, comparator = candidate, prior
    preceding = [p for p in references if p['time'] < origin['time']]
    if not preceding:
        return None
    reference = preceding[-1]
    if not (comparator['time'] < reference['time'] < origin['time'] < bar_start):
        return None
    if origin['time'] < after or reference['known_at'] > bar_start or origin['known_at'] > cutoff:
        return None
    if comparator['known_at'] > origin['known_at']:
        return None
    return deepcopy(reference), deepcopy(origin), dict(correction_comparator=deepcopy(comparator),
        correction_origin=deepcopy(origin), reaction_reference=deepcopy(reference),
        break_rule='COMPLETED_CLOSE_CROSS_ASSUMPTION', origin_confirmation_by=cutoff)


class EvidenceSwingTracker:
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

    def push(self, bar, members):
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
            witness = resolve_outside_reversal(b, members, prev['high'], prev['low'],
                -1 if self.run_direction == 'short' else 1, b['known_at']) if extension and self.step == 300 else {'resolved': False}
            if witness['resolved']:
                self.extreme = b
                events.append(dict(kind='OUTSIDE_REVERSAL_ORDER_RESOLVED', **witness))
            if extension and not witness['resolved']:
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



def _minute(bar, source):
    b = deepcopy(bar)
    t = b.get('time')
    if (type(t) is not int or t < 0 or t % 60 or b.get('known_at') != t+60 or
            type(b.get('known_at')) is not int or b.get('complete') is not True or
            b.get('source_id') != f'{source}:1:{t}'):
        raise ValueError('Invalid closed native minute identity or clock')
    if any(type(b.get(k)) not in (int,float) or not isfinite(b[k]) or b[k] <= 0
           for k in ('open','high','low','close')):
        raise ValueError('Invalid native minute price')
    if not b['low'] <= min(b['open'],b['close']) <= max(b['open'],b['close']) <= b['high']:
        raise ValueError('Invalid native minute range')
    return b


class SourcedCorrectionObserver:
    def __init__(self, context, source_sha256, entry_minutes=5):
        if type(entry_minutes) is not int or entry_minutes not in (1,5):
            raise ValueError('Select one native entry timeframe: 1 or 5')
        if (not isinstance(source_sha256,str) or len(source_sha256)!=64 or
                any(c not in '0123456789abcdef' for c in source_sha256)):
            raise ValueError('Exact source SHA256 required')
        if not isinstance(context,dict) or set(context) != {'direction','four_hour','one_hour','parent','opposing_zone'}:
            raise ValueError('Supply prior HTF context only; no local answer anchors')
        if context['direction'] not in ('long','short'):
            raise ValueError('Explicit direction required')
        for name in ('four_hour','one_hour','parent','opposing_zone'):
            fact=context[name]
            if (not isinstance(fact,dict) or type(fact.get('known_at')) is not int or fact['known_at']<0 or
                    not isinstance(fact.get('source_id'),str) or not fact['source_id'].startswith(source_sha256+':')):
                raise ValueError('Prior HTF facts need source identity and actual known clock')
        parent=context['parent']
        if (parent.get('timeframe') not in (60,240) or not isinstance(parent.get('id'),str) or not parent['id'] or
                any(type(parent.get(k)) not in (int,float) or not isfinite(parent[k]) for k in ('zone_low','zone_high')) or
                not 0 < parent['zone_low'] < parent['zone_high']):
            raise ValueError('Explicit selected parent candidate required')
        self._context=deepcopy(context);self._source=source_sha256;self.step=entry_minutes*60
        self._context_digest=digest(self._context);self.tracker=EvidenceSwingTracker(self.step)
        self._events=[];self._buffer=[];self._last=None;self._previous_bar=None
        self._contact=None;self._used=set();self._candidates=[];self._valid=True

    @property
    def context(self):return deepcopy(self._context)

    @property
    def source(self):return self._source

    @property
    def context_digest(self):return self._context_digest

    @property
    def events(self):return deepcopy(self._events)

    def _emit(self,kind,known_at,**data):
        event=dict(kind=kind,known_at=known_at,mode=MODE,source_sha256=self.source,
            original_context_digest=self.context_digest,parent_id=self._context['parent']['id'],
            context_status='SUPPLIED_PRIOR_HTF_CONTEXT_NOT_AUTONOMOUS_DISCOVERY',
            parent_validity='NOT_INDEPENDENTLY_VERIFIED',teacher_fidelity_proven=False,
            execution_allowed=False,fill_verified=False,**deepcopy(data))
        event['id']=ident(kind,event);self._events.append(event)

    def invalidate_context(self,known_at,reason):
        if (type(known_at) is not int or known_at<0 or not isinstance(reason,str) or not reason or
                (self._last is not None and known_at<self._last['known_at'])):
            raise ValueError('Explicit causal context invalidation required')
        self._valid=False;self._buffer=[];self.tracker.reset()
        self._emit('SUPPLIED_CONTEXT_INVALIDATED',known_at,reason=reason)
        self._invalidated_at=known_at

    def feed_minute(self,bar):
        b=_minute(bar,self.source)
        if self._last is not None and b['time']<=self._last['time']:
            raise ValueError('Native minutes must strictly advance')
        if hasattr(self,'_invalidated_at') and b['known_at']<self._invalidated_at:
            raise ValueError('Cannot feed before context invalidation')
        if self._last is None and any(self._context[k]['known_at']>b['time']
                for k in ('four_hour','one_hour','parent','opposing_zone')):
            raise ValueError('Supplied HTF context is not known at first minute open')
        if self._last is not None and b['time']!=self._last['known_at']:
            self.invalidate_context(b['known_at'],'UNOBSERVED_NATIVE_MINUTE')
        self._last=b
        if not self._valid:return
        if not self._buffer and b['time']%self.step:
            return  # Never complete a partial leading native bucket.
        self._buffer.append(b)
        if len(self._buffer)!=self.step//60:return
        members=self._buffer;self._buffer=[]
        native=dict(time=members[0]['time'],known_at=members[-1]['known_at'],
            open=members[0]['open'],high=max(x['high'] for x in members),
            low=min(x['low'] for x in members),close=members[-1]['close'],complete=True,
            source_id=f'{self.source}:{self.step//60}:{members[0]["time"]}')
        self._feed_native(native,members)

    def _feed_native(self,b,members):
        swing=self.tracker.push(b,members)
        for event in swing:
            if event['kind']=='OUTSIDE_REVERSAL_ORDER_RESOLVED':
                self._emit(event['kind'],b['known_at'],evidence=event,bar_start=b['time'])
        parent=self._context['parent'];direction=self._context['direction']
        if self._contact is None and overlap(b,parent['zone_low'],parent['zone_high']):
            self._contact=b['time']
            self._emit('SUPPLIED_PARENT_CONTACT_OBSERVED',b['known_at'],bar_start=b['time'])
        for candidate in self._candidates:
            if not candidate['active'] or b['time']<candidate['known_at']:continue
            low,high=candidate['zone_low'],candidate['zone_high']
            breach=b['low']<low if direction=='long' else b['high']>high
            if candidate['return_known_at'] is None and overlap(b,low,high):
                candidate['return_known_at']=b['known_at']
                self._emit('SAME_ORIGIN_RETURN_OBSERVED',b['known_at'],candidate_id=candidate['id'],
                    bar_start=b['time'],zone_low=low,zone_high=high,failed_same_bar=breach)
            elif candidate['return_known_at'] is not None and b['time']>=candidate['return_known_at'] and not breach:
                respected=b['close']>high if direction=='long' else b['close']<low
                if respected and not candidate['response_observed']:
                    candidate['response_observed']=True
                    self._emit('SAME_ORIGIN_RESPONSE_OBSERVED',b['known_at'],candidate_id=candidate['id'],
                        bar_start=b['time'],zone_low=low,zone_high=high)
            if breach:
                candidate['active']=False
                self._emit('CHILD_ORIGIN_FAR_EDGE_CROSSED',b['known_at'],candidate_id=candidate['id'],
                    bar_start=b['time'],parent_reversal_inferred=False)
        if self._contact is not None and self._previous_bar is not None and b['time']>self._contact:
            selected=correction_turn(self.tracker.pivots,direction,b['time'],self._contact,b['known_at'])
            if selected:
                ref,origin,evidence=selected;key=(ref['id'],origin['id'])
                if key not in self._used and cross(self._previous_bar['close'],b['close'],ref['price'],direction):
                    raw=next((x for x in self.tracker.bars if x['time']==origin['time']),None)
                    if raw is not None:
                        candidate=dict(id=ident('sourced_candidate',self.context_digest,ref['id'],origin['id']),
                            known_at=b['known_at'],zone_low=raw['low'],zone_high=raw['high'],
                            return_known_at=None,response_observed=False,active=True)
                        self._used.add(key);self._candidates.append(candidate)
                        self._emit('CORRECTION_FLIP_CANDIDATE',b['known_at'],candidate_id=candidate['id'],
                            bar_start=b['time'],direction=direction,reference=ref,origin=origin,
                            zone_low=raw['low'],zone_high=raw['high'],evidence=evidence,
                            geometry_status='FULL_ORIGIN_CANDLE_RESEARCH_CONVENTION')
        self._previous_bar=b
