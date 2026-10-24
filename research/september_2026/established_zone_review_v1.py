"""Causal review of a supplied selected zone whose local break already happened.

Every tap remains visible; no new BOS is required. This is a review branch, not
an automatic formation selector or ENTRY_REVIEW/fill authorization. Exact range
comparison and completed-close break verification are research encodings.
"""
from copy import deepcopy
from math import isfinite
from engine import ident


class EstablishedZoneReview:
    def __init__(self, source_sha256, symbol, feed, step=300):
        if (not isinstance(source_sha256, str) or len(source_sha256) != 64 or
                any(c not in '0123456789abcdef' for c in source_sha256)):
            raise ValueError('Exact source hash required')
        if not all(isinstance(x, str) and x for x in (symbol, feed)):
            raise ValueError('Explicit symbol and feed required')
        if type(step) is not int or step not in (60, 300):
            raise ValueError('Observed native 1M or 5M required')
        self._source = source_sha256
        self._symbol, self._feed, self._step = symbol, feed, step
        self._bars = {}
        self._last = None
        self._events = []
        self._zones = {}
        self._identities = set()

    @property
    def events(self):
        return deepcopy(self._events)

    def events_since(self, cursor):
        """Copy only newly emitted observations for streaming consumers."""
        if type(cursor) is not int or not 0 <= cursor <= len(self._events):
            raise ValueError("Invalid event cursor")
        return deepcopy(self._events[cursor:])

    def _emit(self, kind, known_at, **details):
        e = dict(kind=kind, known_at=known_at, mode='ESTABLISHED_ZONE_REVIEW_V1',
                 source_sha256=self._source, symbol=self._symbol, feed=self._feed,
                 timeframe_seconds=self._step, execution_allowed=False,
                 fill_verified=False, parent_validity='NOT_INDEPENDENTLY_VERIFIED',
                 **deepcopy(details))
        e['id'] = ident(kind, e)
        self._events.append(e)

    def _validate(self, bar):
        b = deepcopy(bar)
        t = b.get('time')
        if (type(t) is not int or t < 0 or t % self._step or
                type(b.get('known_at')) is not int or b['known_at'] != t+self._step or
                b.get('complete') is not True or b.get('symbol') != self._symbol or
                b.get('feed') != self._feed or
                b.get('source_id') != f'{self._source}:{self._step//60}:{t}'):
            raise ValueError('Native source, clock, symbol or feed mismatch')
        if any(type(b.get(k)) not in (int, float) or not isfinite(b[k]) or b[k] <= 0
               for k in ('open', 'high', 'low', 'close')):
            raise ValueError('Invalid native OHLC')
        if not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
            raise ValueError('Invalid native range')
        return b

    def feed_bar(self, bar):
        b = self._validate(bar)
        if self._last and b['time'] <= self._last['time']:
            raise ValueError('Native bars must strictly advance')
        if self._last and b['time'] != self._last['known_at']:
            for zone in self._zones.values():
                if zone['active']:
                    zone['active'] = False
                    self._emit('ESTABLISHED_ZONE_REVIEW_BLOCKED', b['known_at'],
                               zone_id=zone['id'], reason='UNOBSERVED_NATIVE_BAR')
            self._bars = {}  # Old origins cannot authorize a new post-gap zone.
        self._bars[b['time']] = b
        self._last = b
        for zone in self._zones.values():
            if not zone['active'] or b['time'] < zone['known_at']:
                continue
            low, high = zone['zone_low'], zone['zone_high']
            touch = b['low'] <= high and b['high'] >= low
            breach = b['high'] > high if zone['direction'] == 'short' else b['low'] < low
            relation = 'BELOW' if b['close'] < low else 'ABOVE' if b['close'] > high else 'INSIDE'
            if breach:
                zone['prior_far_edge_crossing'] = True
                self._emit('ESTABLISHED_ZONE_BOUNDARY_CROSSED', b['known_at'],
                           zone_id=zone['id'], bar_start=b['time'],
                           parent_reversal_inferred=False, intrabar_order='UNKNOWN')
            if touch:
                zone['tap_count'] += 1
                self._emit('ESTABLISHED_ZONE_TAP_OBSERVED', b['known_at'],
                           zone_id=zone['id'], bar_start=b['time'],
                           zone_low=low, zone_high=high, direction=zone['direction'],
                           established_break_id=zone['break_id'],
                           established_break_known_at=zone['known_at'],
                           tap_sequence=zone['tap_count'], close_relation=relation,
                           same_bar_far_edge_crossed=breach,
                           any_far_edge_crossing_observed=zone['prior_far_edge_crossing'],
                           review_only=True, entry_validated=False,
                           new_break_required=False)

    def establish_zone(self, *, direction, origin_times, reference_time,
                       reference_known_at, break_time, selection_source, parent_id):
        """Register at break close only, using already ingested raw evidence.

        Caller selects the responsible origin/reference. Selection is supplied
        evidence, not claimed automatic/major structure authority. No geometry,
        teacher candle label or future bar can be passed as an answer.
        """
        if direction not in ('long', 'short'):
            raise ValueError('Explicit direction required')
        if not all(isinstance(x, str) and x for x in (selection_source, parent_id)):
            raise ValueError('Selected formation and parent provenance required')
        if (not isinstance(origin_times, (list, tuple)) or not origin_times or
                any(type(t) is not int for t in origin_times) or
                list(origin_times) != sorted(set(origin_times))):
            raise ValueError('Ordered unique observed origin members required')
        if any(type(t) is not int for t in (reference_time, reference_known_at, break_time)):
            raise ValueError('Integer event clocks required')
        if self._last is None or self._last['time'] != break_time:
            raise ValueError('Register only at the observed break close; no backdating')
        needed = list(origin_times)+[reference_time, break_time-self._step, break_time]
        if any(t not in self._bars for t in needed):
            raise ValueError('Unobserved break/reference/origin')
        if (reference_known_at < reference_time+self._step or reference_known_at > break_time or
                reference_time >= break_time or origin_times[-1]+self._step > break_time or
                list(origin_times) != list(range(origin_times[0], origin_times[-1]+self._step, self._step))):
            raise ValueError('Origin/reference not available before break')
        origin = [self._bars[t] for t in origin_times]
        ref, previous, broken = (self._bars[t] for t in (reference_time, break_time-self._step, break_time))
        level = ref['low'] if direction == 'short' else ref['high']
        crossed = (broken['close'] < level <= previous['close'] if direction == 'short'
                   else broken['close'] > level >= previous['close'])
        if not crossed:
            raise ValueError('Supplied prior break not observed in native closes')
        low, high = min(b['low'] for b in origin), max(b['high'] for b in origin)
        if low >= high:
            raise ValueError('Degenerate zone')
        key = (direction, tuple(origin_times), reference_time, break_time)
        if key in self._identities:
            raise ValueError('Formation already registered')
        self._identities.add(key)
        zid = ident('established_zone', self._source, key)
        zone = dict(id=zid, direction=direction, zone_low=low, zone_high=high,
                    known_at=broken['known_at'], break_id=broken['source_id'],
                    parent_id=parent_id, selection_source=selection_source,
                    active=True, tap_count=0, prior_far_edge_crossing=False)
        self._zones[zid] = zone
        self._emit('ESTABLISHED_ZONE_REGISTERED', broken['known_at'], zone_id=zid,
                   direction=direction, zone_low=low, zone_high=high,
                   origin_times=list(origin_times), reference_time=reference_time,
                   reference_known_at=reference_known_at, reference_price=level,
                   break_time=break_time, parent_id=parent_id,
                   selection_source=selection_source,
                   selection_authority='SUPPLIED_LOCAL_FORMATION_NOT_AUTONOMOUS')
        return zid

    def invalidate_zone(self, zone_id, known_at, reason):
        if (zone_id not in self._zones or self._last is None or
                type(known_at) is not int or known_at != self._last['known_at'] or
                not isinstance(reason, str) or not reason):
            raise ValueError('Invalidate at current observed clock with explicit reason')
        self._zones[zone_id]['active'] = False
        self._emit('ESTABLISHED_ZONE_REVIEW_BLOCKED', known_at,
                   zone_id=zone_id, reason=reason)
