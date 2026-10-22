"""Public adapter boundaries; fixtures are synthetic, never market results."""
import copy
from dataclasses import asdict
import unittest

from profit_study import extract_candidates


def event(kind, known, identity, **values):
    return dict(kind=kind, known_at=known, id=identity, execution_allowed=False, **values)


def journal():
    return [
        event('ZONE_CANDIDATE', 0, 'parent-event', zone_id='parent', authority_id='h4', bos_id='parent-bos'),
        event('HTF_LINK', 0, 'link-event', link_id='link', h4_authority_id='h4', h1_authority_id='h1'),
        event('CHILD_CANDIDATE', 60, 'child-event', child_id='child', parent_zone_id='parent',
              link_id='link', direction='long', zone_low=98.0, zone_high=100.0),
        event('ENTRY_REVIEW', 120, 'review', child_id='child', bar_start=60, direction='long',
              target={'price': 106.0, 'known_at': 0}, target_already_reached=False),
    ]


class CandidateJoinBoundaries(unittest.TestCase):
    def extract(self, rows):
        return extract_candidates(rows, 0, 1_000_000)

    def test_original_geometry_and_target_survive_future_updates(self):
        rows = journal()
        rows.append(event('ORIGINAL_TARGET_KNOWN', 180, 'later-target', target={'price': 200.0}))
        candidates, _, audit = self.extract(rows)
        self.assertEqual((candidates[0].stop, candidates[0].target, candidates[0].target_bos_id),
                         (98.0, 106.0, 'parent-bos'))
        self.assertEqual(audit['provenance'][0]['review_event_id'], 'review')

    def test_same_cutoff_revocation_blocks_later_entry_independent_of_journal_order(self):
        revocation = event('AUTHORITY_REVOKED', 120, 'revocation', authority_id='h4')
        for order in (journal() + [revocation], journal()[:-1] + [revocation, journal()[-1]]):
            with self.subTest(order=[e['id'] for e in order]):
                candidates, _, _ = self.extract(order)
                self.assertFalse(candidates[0].valid_at_decision)

    def test_future_parent_failure_is_timed_without_retrospective_rejection(self):
        prefix, _, _ = self.extract(journal())
        rows = journal() + [event('PARENT_ZONE_BREACHED', 180, 'failure', zone_id='parent')]
        full, _, _ = self.extract(rows)
        self.assertTrue(full[0].valid_at_decision)
        self.assertEqual(full[0].invalidated_at_ms, 180000)
        before, after = asdict(prefix[0]), asdict(full[0])
        before.pop('invalidated_at_ms'); after.pop('invalidated_at_ms')
        self.assertEqual(before, after)

    def test_future_each_permission_failure_cancels_pending_candidate(self):
        cases = [('CHILD_PERMISSION_REVOKED', {'child_id': 'child'}),
                 ('CHILD_COVERAGE_UNKNOWN', {'child_id': 'child'}),
                 ('CHILD_CONTEXT_UNAVAILABLE', {'child_id': 'child'}),
                 ('CHILD_ZONE_BREACHED', {'child_id': 'child'}),
                 ('LINK_REVOKED', {'link_id': 'link'}),
                 ('AUTHORITY_REVOKED', {'authority_id': 'h1'})]
        for kind, attrs in cases:
            with self.subTest(kind=kind):
                c, _, _ = self.extract(journal() + [event(kind, 180, 'failure', **attrs)])
                self.assertEqual(c[0].invalidated_at_ms, 180000)

    def test_unrelated_context_failure_does_not_cancel_candidate(self):
        c, _, _ = self.extract(journal() + [event('AUTHORITY_REVOKED', 180, 'other', authority_id='unrelated')])
        self.assertIsNone(c[0].invalidated_at_ms)

    def test_blocked_native_review_is_accounted_without_candidate_promotion(self):
        rows = journal()
        rows[-1].update(kind='ENTRY_REVIEW_BLOCKED', review_status='BLOCKED_TARGET_UNKNOWN', target=None)
        c, _, audit = self.extract(rows)
        self.assertEqual(c, [])
        self.assertEqual(audit['native_blocked_count'], 1)
        self.assertEqual(audit['native_blocked'][0]['reason'], 'BLOCKED_TARGET_UNKNOWN')

    def test_missing_child_or_parent_cannot_create_execution_geometry(self):
        for missing in (0, 1, 2):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                self.extract([e for i, e in enumerate(journal()) if i != missing])

    def test_duplicate_or_reversed_events_are_rejected(self):
        cases = [journal() + [copy.deepcopy(journal()[-1])], list(reversed(journal()))]
        for rows in cases:
            with self.assertRaises(ValueError):
                self.extract(rows)

    def test_premature_child_and_conflicting_direction_are_rejected(self):
        for field, value in (('known_at', 120), ('direction', 'short')):
            rows = journal(); rows[2][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.extract(rows)

    def test_only_completed_native_one_minute_pivots_feed_swing_variant(self):
        rows = journal()
        for tf in (1, 60):
            rows.append(event('PIVOT_CONFIRMED', 180, 'pivot-' + str(tf), timeframe=tf,
                              pivot={'known_at': 180, 'time': 60, 'kind': 'low', 'price': 100.0}))
        _, pivots, _ = self.extract(rows)
        self.assertEqual(len(pivots), 1)
        self.assertEqual((pivots[0].known_at_ms, pivots[0].pivot_time_ms), (180000, 60000))

    def test_every_review_is_accounted_not_first_ten_chart_examples(self):
        rows = journal()[:2]
        for n in range(12):
            child = copy.deepcopy(journal()[2]); child.update(child_id='child-' + str(n), id='c-' + str(n))
            rows.append(child)
        for n in range(12):
            review = copy.deepcopy(journal()[3]); review.update(child_id='child-' + str(n), id='r-' + str(n))
            rows.append(review)
        candidates, _, audit = self.extract(rows)
        self.assertEqual(len(candidates), 12)
        self.assertEqual(audit['review_count'], 12)


if __name__ == '__main__':
    unittest.main()
