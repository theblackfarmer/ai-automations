"""Public-feed regressions for simultaneous parent and child wick failures.

The source fixture is synthetic. These tests preserve factual failure records;
they do not add entries, fills, target choices, or execution permission.
"""
import unittest

from engine import Engine
from test_engine import M1, SPEC, TAP_START, bar, higher_records, same_cutoff_target_records


def prices_for(direction, prices):
    op, high, low, close = prices
    return (30 - op, 30 - low, 30 - high, 30 - close) if direction == 'short' else prices


def ready_engine(direction, minute_count=15):
    engine = Engine(SPEC)
    engine.feed_batch([
        (tf, bar(tf, candle['time'], prices_for(direction, tuple(candle[k] for k in ('open', 'high', 'low', 'close')))))
        for tf, candle in higher_records()
    ])
    for index, prices in enumerate(M1[:minute_count]):
        engine.feed(1, bar(1, TAP_START + index * 60, prices_for(direction, prices)))
    return engine


class ParentFailureObservationTests(unittest.TestCase):
    def active_children(self, engine):
        children = {key: value for key, value in engine.snapshot()['children'].items() if value['active']}
        self.assertEqual(len(children), 2, 'The public fixture must contain both active parent routes')
        return children

    def assert_no_review(self, emitted):
        self.assertFalse(any(event['kind'] in ('RESPECT_OBSERVED', 'ENTRY_REVIEW', 'ENTRY_REVIEW_BLOCKED')
                             for event in emitted))

    def test_first_return_and_child_breach_survive_same_minute_parent_breach(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                engine = ready_engine(direction)
                children = self.active_children(engine)
                authorities = engine.snapshot()['active_authorities']
                self.assertTrue(all(child['return_known_at'] is None for child in children.values()))
                candle = bar(1, TAP_START + 15 * 60, prices_for(direction, (18.5, 18.7, 8.9, 17)))
                emitted = engine.feed(1, candle)
                returns = [event for event in emitted if event['kind'] == 'RETURN_OBSERVED']
                failures = [event for event in emitted if event['kind'] == 'CHILD_ZONE_BREACHED']
                self.assertEqual({event['child_id'] for event in returns}, set(children))
                self.assertEqual({event['child_id'] for event in failures}, set(children))
                self.assertTrue(all(event['failed_same_bar'] and event['intrabar_order_unknown'] for event in returns))
                self.assertEqual(len([event for event in emitted if event['kind'] == 'PARENT_ZONE_BREACHED']), 2)
                self.assertEqual(len([event for event in emitted if event['kind'] == 'CHILD_PERMISSION_REVOKED']), 2)
                after = engine.snapshot()
                for child_id in children:
                    child = after['children'][child_id]
                    self.assertFalse(child['active'])
                    self.assertEqual(child['status'], 'CHILD_FAR_EDGE_BREACH')
                    self.assertEqual(child['return_known_at'], candle['known_at'])
                    self.assertEqual(child['failed_at'], candle['known_at'])
                self.assertEqual(after['active_authorities'], authorities)
                self.assert_no_review(emitted)

    def test_later_parent_breach_keeps_child_failure_without_repeating_return(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                engine = ready_engine(direction, minute_count=16)
                children = self.active_children(engine)
                self.assertTrue(all(child['return_known_at'] is not None for child in children.values()))
                emitted = engine.feed(1, bar(1, TAP_START + 16 * 60,
                                            prices_for(direction, (18, 18.7, 8.9, 17))))
                self.assertEqual({event['child_id'] for event in emitted if event['kind'] == 'CHILD_ZONE_BREACHED'},
                                 set(children))
                self.assertFalse(any(event['kind'] == 'RETURN_OBSERVED' for event in emitted))
                self.assert_no_review(emitted)

    def test_price_gap_beyond_entire_child_records_failure_without_inventing_return(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                engine = ready_engine(direction)
                children = self.active_children(engine)
                emitted = engine.feed(1, bar(1, TAP_START + 15 * 60,
                                            prices_for(direction, (8.7, 8.8, 8.0, 8.6))))
                self.assertEqual({event['child_id'] for event in emitted if event['kind'] == 'CHILD_ZONE_BREACHED'},
                                 set(children))
                self.assertFalse(any(event['kind'] == 'RETURN_OBSERVED' for event in emitted))
                self.assertTrue(all(engine.snapshot()['children'][child_id]['return_known_at'] is None
                                    for child_id in children))
                self.assert_no_review(emitted)

    def test_exact_parent_edge_contact_preserves_parent_and_existing_child_failure(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                engine = ready_engine(direction)
                children = self.active_children(engine)
                before = engine.snapshot()
                emitted = engine.feed(1, bar(1, TAP_START + 15 * 60,
                                            prices_for(direction, (18.5, 18.7, 9, 17))))
                self.assertFalse(any(event['kind'] == 'PARENT_ZONE_BREACHED' for event in emitted))
                self.assertEqual(len([event for event in emitted if event['kind'] == 'PARENT_ZONE_EDGE_CONTACT']), 2)
                self.assertEqual({event['child_id'] for event in emitted if event['kind'] == 'CHILD_ZONE_BREACHED'},
                                 set(children))
                after = engine.snapshot()
                self.assertEqual(after['active_authorities'], before['active_authorities'])
                self.assertEqual({key: zone['active'] for key, zone in after['zones'].items()},
                                 {key: zone['active'] for key, zone in before['zones'].items()})
                self.assert_no_review(emitted)

    def test_prior_minute_parent_retirement_does_not_reactivate_child_on_later_overlap(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                engine = ready_engine(direction)
                children = self.active_children(engine)
                engine.feed(1, bar(1, TAP_START + 15 * 60,
                                   prices_for(direction, (18.5, 18.7, 8.9, 17))))
                recorded = engine.events
                emitted = engine.feed(1, bar(1, TAP_START + 16 * 60,
                                            prices_for(direction, (17, 18.7, 15.4, 18))))
                self.assertFalse(any(event['kind'] in ('RETURN_OBSERVED', 'CHILD_ZONE_BREACHED')
                                     for event in emitted))
                self.assertTrue(all(not engine.snapshot()['children'][child_id]['active'] for child_id in children))
                self.assertEqual(engine.events[:len(recorded)], recorded)
                self.assert_no_review(emitted)

    def test_simultaneous_owner_revocation_and_parent_breach_keep_child_failure(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                records = []
                for tf, candle in same_cutoff_target_records():
                    prices = tuple(candle[key] for key in ('open', 'high', 'low', 'close'))
                    if tf == 60 and candle['time'] == 57 * 3600:
                        prices = (35, 36, 18, 18.5)
                    elif tf == 1 and candle['time'] == 208680:
                        prices = (28.5, 29, 28.2, 28.8)
                    elif tf == 1 and candle['time'] == 208740:
                        prices = (28.8, 29, 18, 18.5)
                    if direction == 'short':
                        op, high, low, close = prices
                        prices = (70 - op, 70 - low, 70 - high, 70 - close)
                    records.append((tf, bar(tf, candle['time'], prices)))
                engine = Engine(SPEC)
                engine.feed_batch([(tf, candle) for tf, candle in records if candle['known_at'] < 208800])
                before = engine.snapshot()
                children = {key for key, child in before['children'].items() if child['active']}
                self.assertTrue(children)
                emitted = engine.feed_batch([(tf, candle) for tf, candle in records if candle['known_at'] == 208800])
                self.assertTrue(any(event['kind'] == 'AUTHORITY_REVOKED' and event['timeframe'] == 60
                                    for event in emitted))
                self.assertTrue(any(event['kind'] == 'PARENT_ZONE_BREACHED' for event in emitted))
                self.assertEqual({event['child_id'] for event in emitted if event['kind'] == 'RETURN_OBSERVED'}, children)
                self.assertEqual({event['child_id'] for event in emitted if event['kind'] == 'CHILD_ZONE_BREACHED'}, children)
                after = engine.snapshot()
                self.assertIsNone(after['active_authorities'][60])
                self.assertEqual(after['active_authorities'][240], before['active_authorities'][240])
                self.assertTrue(all(not after['children'][child_id]['active'] for child_id in children))
                self.assert_no_review(emitted)


if __name__ == '__main__':
    unittest.main()
