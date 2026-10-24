"""OHLC-only local formation discovery with teacher-selected terminal origin.

Port of the frozen October 8 formation comparison, using the repository R5
tracker. Isaac selects the last green candle for sells, last red for buys.
R5 and completed-close BOS are research conventions, not universal
teacher rules. No drawings, labels, selected origin or future outcomes accepted.
"""
from copy import deepcopy
from engine import SwingTracker, ident
from established_zone_review_v1 import EstablishedZoneReview


class AutonomousZoneReview:
    def __init__(self, source_sha256, symbol, feed, step=300):
        self.review = EstablishedZoneReview(source_sha256, symbol, feed, step)
        self.tracker = SwingTracker(step)
        self.step = step
        self.previous = None
        self._events = []
        self._seen = set()
        self._review_cursor = 0

    @property
    def events(self):
        return deepcopy(self._events)

    def _emit(self, kind, known_at, **details):
        self._events.append(dict(kind=kind, known_at=known_at,
                                 execution_allowed=False, entry_validated=False,
                                 convention='ASSUMED_R5_LOCAL_SWINGS_AND_CLOSE_CROSS',
                                 **deepcopy(details)))

    def _drain(self):
        # Preserve review authority: this wrapper supplies machine-derived
        # terminal origins, never an independently verified parent or valid trade.
        rows = self.review.events_since(self._review_cursor)
        for row in rows:
            if row['kind'] == 'ESTABLISHED_ZONE_REGISTERED':
                row['selection_authority'] = 'AUTOMATIC_LOCAL_ORIGIN_TEACHER_TERMINAL_RULE'
                row.pop('id')
                row['id'] = ident(row['kind'], row)
            self._events.append(row)
        self._review_cursor += len(rows)

    def feed_bar(self, source_bar):
        # Validate before mutating tracker state; discard arbitrary extra fields.
        valid = self.review._validate(source_bar)
        bar = {k: valid[k] for k in ('time', 'known_at', 'open', 'high', 'low',
                                     'close', 'source_id', 'symbol', 'feed', 'complete')}
        self.review.feed_bar(bar)
        self._drain()
        previous = self.previous
        if previous and bar['time'] != previous['known_at']:
            self.tracker.reset()
            self._seen.clear()
            self._emit('AUTOMATIC_COVERAGE_RESET', bar['known_at'])
            previous = None
        tracker_events = self.tracker.push(bar)
        self._events.extend(dict(deepcopy(e), execution_allowed=False,
                                 entry_validated=False) for e in tracker_events)
        self.previous = bar
        if previous is None or any(e['kind'] == 'AMBIGUOUS_TURN' for e in tracker_events):
            return
        for direction in ('short', 'long'):
            short = direction == 'short'
            refs = [p for p in self.tracker.pivots if p['kind'] == ('low' if short else 'high')]
            pulls = [p for p in self.tracker.pivots if p['kind'] == ('high' if short else 'low')]
            if not refs:
                continue
            ref = refs[-1]
            close_cross = (bar['close'] < ref['price'] <= previous['close'] if short
                           else bar['close'] > ref['price'] >= previous['close'])
            wick_cross = (bar['low'] < ref['price'] <= previous['close'] if short
                          else bar['high'] > ref['price'] >= previous['close'])
            if not (close_cross or wick_cross):
                continue
            reasons = []
            for name, peers, label in (('REFERENCE', refs, 'LL' if short else 'HH'),
                                        ('PULLBACK', pulls, 'LH' if short else 'HL')):
                if len(peers) < 2:
                    reasons.append('MISSING_' + name + '_COMPARATOR')
                elif (peers[-1]['label'] != label or not
                      (peers[-1]['price'] < peers[-2]['price'] if short else
                       peers[-1]['price'] > peers[-2]['price'])):
                    reasons.append(name + '_NOT_' + label)
            pull = pulls[-1] if pulls else None
            if pull and ref['time'] >= pull['time']:
                reasons.append('PULLBACK_NOT_LATER_THAN_REFERENCE')
            if ref['known_at'] >= bar['time']:
                reasons.append('REFERENCE_NOT_KNOWN_STRICTLY_BEFORE_BREAK')
            if pull and pull['known_at'] > bar['known_at']:
                reasons.append('PULLBACK_NOT_KNOWN_BY_BREAK_CLOSE')
            details = dict(direction=direction, reference=ref, pullback=pull,
                           break_start=bar['time'])
            if reasons:
                if close_cross:
                    self._emit('AUTOMATIC_FORMATION_REJECTED', bar['known_at'],
                               reasons=reasons, **details)
                continue
            if not close_cross:
                self._emit('AUTOMATIC_WICK_BREAK_ONLY', bar['known_at'], **details)
                continue
            history = self.tracker.bars
            index = next(i for i, b in enumerate(history) if b['time'] == pull['time'])
            def opposite(b):
                return b['close'] > b['open'] if short else b['close'] < b['open']
            end = index if opposite(history[index]) else index - 1
            if end < 0 or not opposite(history[end]):
                self._emit('AUTOMATIC_FORMATION_REJECTED', bar['known_at'],
                           reasons=['NO_RESPONSIBLE_OPPOSITE_RUN'], **details)
                continue
            begin = end
            while begin and opposite(history[begin-1]):
                begin -= 1
            branches = {'historical_run': [b['time'] for b in history[begin:end+1]],
                        'terminal_single': [history[end]['time']]}
            diagnostics = {}
            for branch, members in branches.items():
                origin = [b for b in history if b['time'] in members]
                low, high = min(b['low'] for b in origin), max(b['high'] for b in origin)
                contacts = [dict(bar_start=b['time'], known_at=b['known_at'],
                    price=b['high'] if short else b['low'],
                    relation='excursion' if (b['high'] > high if short else b['low'] < low) else 'touch')
                    for b in history if members[-1] < b['time'] < bar['time']
                    and (b['high'] >= high if short else b['low'] <= low)]
                diagnostics[branch] = dict(zone_low=low, zone_high=high,
                    reference_overlap=ref['time'] in members,
                    origin_starts_before_reference=members[0] < ref['time'],
                    prebreak_boundary_contacts=contacts,
                    prebreak_far_edge_excursions=[c for c in contacts if c['relation']=='excursion'],
                    break_bar_far_edge_crossed=bar['high'] > high if short else bar['low'] < low,
                    break_bar_boundary_contact=(dict(bar_start=bar['time'], known_at=bar['known_at'],
                      price=bar['high'] if short else bar['low'],
                      relation='excursion' if (bar['high'] > high if short else bar['low'] < low) else 'touch')
                      if (bar['high'] >= high if short else bar['low'] <= low) else None),
                    departure_known_at=(bar['known_at'] if (bar['close'] < low if short else bar['close'] > high) else None),
                    departure_valid=bar['close'] < low if short else bar['close'] > high)
            self._emit('AUTOMATIC_FORMATION_COMPARISON', bar['known_at'], branches=branches,
                       diagnostics=diagnostics,
                       selected='terminal_single',
                       selection_status='TEACHER_LAST_OPPOSITE_CANDLE_20261024', **details)
            for branch, members in [('terminal_single', branches['terminal_single'])]:
                # Cluster is retained above only as historical diagnostic evidence.
                # Re-crossing the same reference does not create another zone.
                key = (direction, ref['time'], tuple(members))
                if key in self._seen:
                    continue
                self.review.establish_zone(direction=direction, origin_times=members,
                    reference_time=ref['time'], reference_known_at=ref['known_at'],
                    break_time=bar['time'], selection_source='AUTOMATIC_OHLC:' + branch,
                    parent_id='UNRESOLVED_PARENT')
                self._seen.add(key)
            self._drain()
