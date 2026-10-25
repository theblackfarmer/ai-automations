"""Causal opposite-candle evidence, independent of swing-turn qualification.

This is an observation ledger, NOT a supply/demand or BOS selector. A completed
close outside an opposite candle records departure. Later origins never replace
older records. No invalidation rule or trade authority is inferred from color.
Only validated, consecutive native bars supplied by AutonomousZoneReview enter.
"""
from copy import deepcopy
from engine import ident


class OriginEvidence:
    def __init__(self):
        self.previous = None
        self.records = {}

    def push(self, bar):
        events = []
        def emit(kind, **data):
            event = dict(kind=kind, known_at=bar['known_at'],
                         execution_allowed=False, entry_validated=False,
                         structure_verified=False, **deepcopy(data))
            events.append(event)
        previous = self.previous
        if previous and bar['time'] != previous['known_at']:
            for record in self.records.values():
                emit('ORIGIN_EVIDENCE_BLOCKED', origin_id=record['origin_id'],
                     reason='MISSING_NATIVE_BARS')
            self.records.clear()
            previous = None
        # Existing origin identities and geometry are immutable. A touch is a
        # whole-bar observation; no intrabar confirmation or entry is claimed.
        for record in self.records.values():
            if bar['low'] <= record['zone_high'] and bar['high'] >= record['zone_low']:
                emit('ORIGIN_EVIDENCE_OVERLAP', origin_id=record['origin_id'],
                     bar_start=bar['time'], intrabar_order='UNKNOWN')
        if previous:
            short = previous['close'] > previous['open'] and bar['close'] < previous['low']
            long = previous['close'] < previous['open'] and bar['close'] > previous['high']
            if short or long:
                direction = 'short' if short else 'long'
                record = dict(origin_id=ident('origin_evidence', previous['source_id'], direction),
                    direction=direction, origin_time=previous['time'],
                    origin_source_id=previous['source_id'], zone_low=previous['low'],
                    zone_high=previous['high'], departure_time=bar['time'],
                    known_at=bar['known_at'], structure_verified=False,
                    execution_allowed=False, entry_validated=False)
                self.records[record['origin_id']] = record
                emit('ORIGIN_EVIDENCE_RECORDED', record=record,
                     authority='OPPOSITE_CANDLE_DEPARTURE_ONLY')
        self.previous = deepcopy(bar)
        return events

    def snapshot(self):
        return deepcopy(list(self.records.values()))
