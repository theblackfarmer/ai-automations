"""Public-feed synthetic regressions for original target coverage continuity.

An unresolved indication cannot acquire an unrelated endpoint after unknown
coverage. These checks do not define fills, exits, profit, or new entry rules.
"""
import unittest

from engine import Engine
from test_engine import HTF, SPEC, bar


def prices_for(direction, prices):
    op, high, low, close = prices
    return (200-op, 200-low, 200-high, 200-close) if direction == 'short' else prices


def feed_prices(engine, timeframe, direction, index, prices, **extra):
    return engine.feed(timeframe, bar(timeframe, index*timeframe*60,
                                      prices_for(direction, prices), **extra))


def unresolved_engine(timeframe, direction):
    engine = Engine(SPEC)
    for index, prices in enumerate(HTF[:11]):
        feed_prices(engine, timeframe, direction, index, prices)
    bosses = engine.snapshot()['bos']
    if len(bosses) != 1:
        raise AssertionError('Synthetic prefix must establish exactly one BOS')
    bos_id = next(iter(bosses))
    if bosses[bos_id]['target'] is not None:
        raise AssertionError('Synthetic original endpoint must remain unresolved')
    return engine, bos_id


def target_recognitions(engine, bos_id):
    return [event for event in engine.events
            if event['kind'] == 'ORIGINAL_TARGET_KNOWN' and event['bos_id'] == bos_id]


class TargetContinuityTests(unittest.TestCase):
    def scenarios(self):
        return ((timeframe, direction) for timeframe in (60, 240)
                for direction in ('long', 'short'))

    def test_unclassified_native_gap_cannot_supply_old_original_target(self):
        for timeframe, direction in self.scenarios():
            with self.subTest(timeframe=timeframe, direction=direction):
                engine, bos_id = unresolved_engine(timeframe, direction)
                authority_id = engine.snapshot()['bos'][bos_id]['authority_id']
                # Bar11 is absent. New swings are around100, disconnected from
                # the original indication around18; public feed resets coverage.
                for index, prices in enumerate(((100,102,99,101), (101,106,100,105),
                                                 (105,105.5,98,99)), start=12):
                    feed_prices(engine, timeframe, direction, index, prices)
                state = engine.snapshot()
                self.assertTrue(any(event['kind'] == 'UNCLASSIFIED_COVERAGE_GAP'
                                    for event in engine.events))
                self.assertTrue(any(event['kind'] == 'PIVOT_CONFIRMED'
                                    and event['pivot']['time'] >= 12*timeframe*60
                                    for event in engine.events))
                self.assertIsNone(state['bos'][bos_id]['target'])
                self.assertEqual(target_recognitions(engine, bos_id), [])
                self.assertFalse(state['authorities'][authority_id]['active'])
                self.assertIsNone(state['active_authorities'][timeframe])
                self.assertFalse(any(event['kind'] == 'ENTRY_REVIEW' for event in engine.events))
                self.assertTrue(all(event['execution_allowed'] is False for event in engine.events))

    def test_contiguous_opposite_swing_still_resolves_original_endpoint(self):
        for timeframe, direction in self.scenarios():
            with self.subTest(timeframe=timeframe, direction=direction):
                engine, bos_id = unresolved_engine(timeframe, direction)
                feed_prices(engine, timeframe, direction, 11, (17,17.8,10,10.5))
                target = engine.snapshot()['bos'][bos_id]['target']
                self.assertEqual(target['price'], 18 if direction == 'long' else 182)
                self.assertEqual(target['known_at'], 12*timeframe*60)
                self.assertEqual(target['pivot']['time'], 10*timeframe*60)
                self.assertEqual(len(target_recognitions(engine, bos_id)), 1)

    def test_evidenced_scheduled_bridge_preserves_pending_target_resolution(self):
        for timeframe, direction in self.scenarios():
            with self.subTest(timeframe=timeframe, direction=direction):
                engine, bos_id = unresolved_engine(timeframe, direction)
                authority_id = engine.snapshot()['bos'][bos_id]['authority_id']
                feed_prices(engine, timeframe, direction, 12, (17,17.8,10,10.5),
                            gap_before='scheduled', schedule_evidence='SYNTHETIC_DOCUMENTED_CLOSURE')
                state = engine.snapshot()
                self.assertTrue(any(event['kind'] == 'SCHEDULED_COVERAGE_BRIDGE'
                                    for event in engine.events))
                self.assertFalse(any(event['kind'] == 'UNCLASSIFIED_COVERAGE_GAP'
                                     for event in engine.events))
                self.assertEqual(state['bos'][bos_id]['target']['price'],
                                 18 if direction == 'long' else 182)
                self.assertEqual(state['bos'][bos_id]['target']['known_at'], 13*timeframe*60)
                self.assertEqual(state['active_authorities'][timeframe], authority_id)

    def test_already_known_target_and_reached_history_survive_unknown_coverage(self):
        for timeframe, direction in self.scenarios():
            for reached in (False, True):
                with self.subTest(timeframe=timeframe, direction=direction, reached=reached):
                    engine, bos_id = unresolved_engine(timeframe, direction)
                    feed_prices(engine, timeframe, direction, 11, (17,17.8,10,10.5))
                    touch = (17,18 if reached else 17.9,16,17.5)
                    engine.feed(1, bar(1, 12*timeframe*60, prices_for(direction, touch)))
                    before = engine.snapshot()['bos'][bos_id]['target']
                    self.assertEqual(before['reached'], reached)
                    engine.gap(12*timeframe*60+60, 'SYNTHETIC_UNKNOWN_COVERAGE',
                               timeframes=(timeframe, 1))
                    for index, prices in enumerate(((100,102,99,101), (101,106,100,105),
                                                     (105,105.5,98,99)), start=13):
                        feed_prices(engine, timeframe, direction, index, prices)
                    state = engine.snapshot()
                    self.assertEqual(state['bos'][bos_id]['target'], before)
                    self.assertEqual(len(target_recognitions(engine, bos_id)), 1)
                    self.assertIsNone(state['active_authorities'][timeframe])
                    self.assertFalse(any(event['kind'] == 'ENTRY_REVIEW' for event in engine.events))

    def test_new_post_gap_bos_can_resolve_its_own_contiguous_target(self):
        for timeframe, direction in self.scenarios():
            with self.subTest(timeframe=timeframe, direction=direction):
                engine, original_id = unresolved_engine(timeframe, direction)
                for index, prices in enumerate(HTF[:11], start=12):
                    feed_prices(engine, timeframe, direction, index,
                                tuple(value+100 for value in prices))
                feed_prices(engine, timeframe, direction, 23, (117,117.8,110,110.5))
                state = engine.snapshot()
                self.assertEqual(len(state['bos']), 2)
                newer_id = next(key for key in state['bos'] if key != original_id)
                self.assertIsNone(state['bos'][original_id]['target'])
                self.assertEqual(target_recognitions(engine, original_id), [])
                self.assertEqual(state['bos'][newer_id]['target']['price'],
                                 118 if direction == 'long' else 82)
                self.assertEqual(len(target_recognitions(engine, newer_id)), 1)
                self.assertTrue(state['authorities'][state['bos'][newer_id]['authority_id']]['active'])

    def test_ambiguous_turn_without_coverage_loss_keeps_existing_target_semantics(self):
        # Tracker ambiguity and missing coverage are distinct frozen mechanics.
        # This repair changes only the latter, not the authority or target rule
        # for a fully observed outside reversal followed by a new confirmation.
        for timeframe, direction in self.scenarios():
            with self.subTest(timeframe=timeframe, direction=direction):
                engine, bos_id = unresolved_engine(timeframe, direction)
                authority_id = engine.snapshot()['bos'][bos_id]['authority_id']
                for index, prices in enumerate(((17,19,12,12.5), (12.5,22,12.5,21),
                                                 (21,21.5,20,20.5), (20.5,21,16,18)), start=11):
                    feed_prices(engine, timeframe, direction, index, prices)
                state = engine.snapshot()
                self.assertTrue(any(event['kind'] == 'AMBIGUOUS_TURN' for event in engine.events))
                self.assertFalse(any(event['kind'] == 'UNCLASSIFIED_COVERAGE_GAP'
                                     for event in engine.events))
                self.assertEqual(state['active_authorities'][timeframe], authority_id)
                self.assertEqual(state['bos'][bos_id]['target']['price'],
                                 22 if direction == 'long' else 178)
                self.assertEqual(len(target_recognitions(engine, bos_id)), 1)


if __name__ == '__main__':
    unittest.main()
