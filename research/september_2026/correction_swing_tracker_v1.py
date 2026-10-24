"""Teacher correction: a previous-candle cross is a leg change, not structure loss.

The inherited local swing convention remains a research encoding. On an outside
turn retain the observed extended extreme and earlier comparisons. No intrabar
high/low ordering or major-structure reversal is inferred.
"""
from copy import deepcopy
from engine import SwingTracker, ident


class CorrectionSwingTracker(SwingTracker):
    def __init__(self, step):
        super().__init__(step)
        self._ambiguous_extreme_time = None

    def reset(self):
        super().reset()
        self._ambiguous_extreme_time = None

    def _ordinary_push(self, b):
        uncertain_time = self._ambiguous_extreme_time
        events = super().push(b)
        for event in events:
            if event['kind'] == 'PIVOT_CONFIRMED' and event['pivot']['time'] == uncertain_time:
                # Preserve the observation, but never let an unknown same-bar
                # extreme become a confirmed correction reference/comparator.
                self.pivots[-1].update(sequence_eligible=False,
                                      intrabar_order='UNKNOWN',
                                      reason='OUTSIDE_BAR_OPPOSITE_EXTREME_ORDER_UNKNOWN')
                event['pivot'] = deepcopy(self.pivots[-1])
                event['authority'] = 'UNRESOLVED_LEG_ENVELOPE'
                event['intrabar_order'] = 'UNKNOWN'
        if self.extreme is None or self.extreme['time'] != uncertain_time:
            self._ambiguous_extreme_time = None
        return events

    def push(self, bar):
        b = deepcopy(bar)
        previous = self.bars[-1] if self.bars else None
        if previous is None or self.run_direction is None:
            return self._ordinary_push(b)
        move = 'long' if b['close'] > previous['high'] else 'short' if b['close'] < previous['low'] else None
        field = 'high' if self.run_direction == 'long' else 'low'
        extended = b[field] > self.extreme[field] if field == 'high' else b[field] < self.extreme[field]
        if not (move and move != self.run_direction and extended):
            return self._ordinary_push(b)
        # A's new low/high is observed by its close; do not erase older swings.
        peers = [p for p in self.pivots if p['kind'] == field]
        comparator = deepcopy(peers[-1]) if peers else None
        value = b[field]
        label = 'H' if field == 'high' else 'L'
        if comparator:
            old = comparator['price']
            label = ('HH' if value > old else 'LH' if value < old else 'EQH') if field == 'high' else ('HL' if value > old else 'LL' if value < old else 'EQL')
            comparator.pop('comparator', None)
        pivot = dict(id=ident('pivot', self.step, self.segment, field, b['time']),
                     kind=field, time=b['time'], known_at=b['known_at'], price=value,
                     label=label, comparator=comparator, source_id=b['source_id'])
        self.pivots.append(pivot)
        old_direction = self.run_direction
        self.run_direction = move
        # This is an observed whole-bar envelope, NOT a claim that its other
        # extreme occurred after the pivot. Same-bar ref/pull associations are
        # rejected downstream; a later extreme needs actual later evidence.
        self.extreme = b
        self._ambiguous_extreme_time = b['time']
        self.bars.append(b)
        return [dict(kind='PIVOT_CONFIRMED', known_at=b['known_at'], pivot=deepcopy(pivot),
                     authority='LOCAL_LEG_EXTREME_ONLY', intrabar_order='UNKNOWN'),
                dict(kind='CORRECTION_LEG_STARTED', known_at=b['known_at'],
                     bar_start=b['time'], previous_leg=old_direction,
                     correction_leg=move, extended_extreme=deepcopy(pivot),
                     prior_pivots_retained=len(self.pivots)-1,
                     structure_break=None, major_direction_changed=None,
                     structure_break_authorized=False, major_reference_assessed=False,
                     intrabar_order='UNKNOWN', execution_allowed=False)]
