"""Independent synthetic journal -> quote integration and publication gates.

Small seal fixtures and mocked pipeline calls below test contract boundaries.
They are explicitly not full native replays or historical acquisitions.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import data_source
import profit_study as study
from quote_execution import Config, Policy, Quote, simulate_many


def event(kind, known, identity, **fields):
    return dict(kind=kind, known_at=known, id=identity,
                execution_allowed=False, synthetic_fixture=True, **fields)


def journal(review_time=120):
    return [
        event('ZONE_CANDIDATE', 0, 'parent-a-event', zone_id='parent-a',
              authority_id='h4-a', bos_id='parent-a-bos'),
        event('HTF_LINK', 0, 'link-a-event', link_id='link-a',
              h4_authority_id='h4-a', h1_authority_id='h1-a'),
        event('CHILD_CANDIDATE', 60, 'child-a-event', child_id='child-a',
              parent_zone_id='parent-a', link_id='link-a', direction='long',
              zone_low=98.0, zone_high=100.0),
        event('ENTRY_REVIEW', review_time, 'review-a', child_id='child-a',
              direction='long', bar_start=review_time - 60,
              target={'price': 106.0, 'known_at': 0, 'reached': False},
              target_already_reached=False),
    ]


def execute(rows, quotes=None, config=None):
    candidates, pivots, audit = study.extract_candidates(rows, 0, 1_000_000)
    if quotes is None:
        quotes = [Quote(121001, 100, 101, 'synthetic-entry'),
                  Quote(122000, 106, 107, 'synthetic-target')]
    result = simulate_many(
        quotes, candidates, pivots, [Policy('structural-audit')],
        config or Config(commission_per_million=0))
    return candidates, result['policies']['structural-audit'], audit


class JournalQuoteIntegration(unittest.TestCase):
    def test_actual_parent_target_survives_unrelated_later_target_and_failure(self):
        prefix = journal()
        later = prefix + [
            event('ORIGINAL_TARGET_KNOWN', 180, 'other-target',
                  bos_id='unrelated-bos', target={'price': 999.0, 'known_at': 180}),
            event('PARENT_ZONE_BREACHED', 180, 'later-parent-failure',
                  zone_id='parent-a')]
        early_candidates, early, _ = execute(prefix)
        late_candidates, late, audit = execute(later)
        self.assertEqual(early['trades'], late['trades'])
        trade = late['trades'][0]
        self.assertEqual(trade['parent_zone_id'], 'parent-a')
        self.assertEqual(trade['target_bos_id'], 'parent-a-bos')
        self.assertEqual(trade['original_target'], 106)
        self.assertEqual(trade['exit_price'], 106)
        self.assertEqual(trade['gross_points'], 5)
        self.assertIsNone(early_candidates[0].invalidated_at_ms)
        self.assertEqual(late_candidates[0].invalidated_at_ms, 180000)
        self.assertEqual(audit['provenance'][0]['target_at_review']['price'], 106)

    def test_all_same_cutoff_permissions_apply_before_any_fill(self):
        failures = [
            ('AUTHORITY_REVOKED', {'authority_id': 'h4-a'}),
            ('AUTHORITY_REVOKED', {'authority_id': 'h1-a'}),
            ('LINK_REVOKED', {'link_id': 'link-a'}),
            ('PARENT_ZONE_BREACHED', {'zone_id': 'parent-a'}),
            ('CHILD_PERMISSION_REVOKED', {'child_id': 'child-a'}),
            ('CHILD_ZONE_BREACHED', {'child_id': 'child-a'}),
            ('CHILD_COVERAGE_UNKNOWN', {'child_id': 'child-a'})]
        for kind, fields in failures:
            failed = event(kind, 120, 'failure-' + kind, **fields)
            for after_review in (False, True):
                with self.subTest(kind=kind, after_review=after_review):
                    rows = journal()
                    if after_review:
                        rows.append(failed)
                    else:
                        rows.insert(-1, failed)
                    candidates, result, _ = execute(rows)
                    self.assertFalse(candidates[0].valid_at_decision)
                    self.assertEqual(result['trades'], [])
                    self.assertEqual(result['decisions'][0]['status'],
                                     'PERMISSION_INVALID_AT_DECISION')

    def test_future_revocation_cancels_delayed_order_only_after_its_clock(self):
        rows = journal() + [event('LINK_REVOKED', 180, 'future-link-failure',
                                  link_id='link-a')]
        candidates, result, _ = execute(
            rows, [Quote(191001, 100, 101, 'after-revocation')],
            Config(entry_latency_ms=70000, pending_expiry_ms=120000,
                   commission_per_million=0))
        self.assertTrue(candidates[0].valid_at_decision)
        self.assertEqual(candidates[0].invalidated_at_ms, 180000)
        self.assertEqual(result['trades'], [])
        self.assertEqual(result['decisions'][0]['status'],
                         'PERMISSION_REVOKED_BEFORE_ENTRY')
        self.assertEqual(result['decisions'][0]['recorded_at_ms'], 180000)

    def test_transient_context_unavailable_does_not_retire_later_genuine_review(self):
        rows = journal(review_time=180)
        rows.insert(-1, event('CHILD_CONTEXT_UNAVAILABLE', 120,
                              'temporarily-unavailable', child_id='child-a'))
        candidates, result, _ = execute(rows, [
            Quote(181001, 100, 101, 'recovered-entry'),
            Quote(182000, 106, 107, 'recovered-exit')])
        self.assertTrue(candidates[0].valid_at_decision)
        self.assertIsNone(candidates[0].invalidated_at_ms)
        self.assertEqual(result['decisions'][0]['status'], 'FILLED')
        self.assertEqual(result['trades'][0]['exit_price'], 106)

    def test_transient_context_still_blocks_its_own_cutoff_and_pending_order(self):
        same = journal() + [event('CHILD_CONTEXT_UNAVAILABLE', 120,
                                  'same-cutoff-unavailable', child_id='child-a')]
        candidates, result, _ = execute(same)
        self.assertFalse(candidates[0].valid_at_decision)
        self.assertEqual(result['trades'], [])
        future = journal() + [event('CHILD_CONTEXT_UNAVAILABLE', 180,
                                    'future-unavailable', child_id='child-a')]
        candidates, result, _ = execute(
            future, [Quote(191001, 100, 101, 'pending-after-loss')],
            Config(entry_latency_ms=70000, pending_expiry_ms=120000,
                   commission_per_million=0))
        self.assertEqual(candidates[0].invalidated_at_ms, 180000)
        self.assertEqual(result['decisions'][0]['status'],
                         'PERMISSION_REVOKED_BEFORE_ENTRY')


class AcquisitionAndPublicationGates(unittest.TestCase):
    def test_incomplete_real_validator_stops_before_pipeline_or_profit_calculation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            acquisition = root / 'acquisition'
            acquisition.mkdir()
            (acquisition / 'MANIFEST.json').write_text(json.dumps({
                'all_hours_validated_or_explicit_empty_200': False,
                'run_in_progress': False, 'synthetic_fixture': True,
                'reason': 'synthetic missing-hour control'}))
            with patch.object(study.subprocess, 'run') as pipeline, \
                    patch.object(study, 'simulate_many') as simulation:
                result, code = study.run(acquisition, root / 'result')
            pipeline.assert_not_called()
            simulation.assert_not_called()
            self.assertEqual(code, 2)
            self.assertEqual(result['status'], 'BLOCKED_NO_PROFIT_RESULT')
            self.assertIn('BLOCKED_FULL_MONTH_ACQUISITION', result['reason'])
            self.assertFalse(result['real_market_backtest_completed'])
            self.assertFalse(result['publication_integrity_verified'])
            self.assertFalse(result['profitability_established'])
            self.assertNotIn('policy_summaries', result)
            self.assertFalse((root / 'result/QUOTE_SIMULATION.json').exists())
            self.assertFalse((root / 'result/structure').exists())

    def test_late_integrity_failure_cannot_publish_computed_synthetic_profit(self):
        # Only orchestration boundaries are stubbed. The journal join and
        # synthetic quote calculation run normally before the induced failure.
        protocol = json.loads((study.HERE / 'PROFIT_PROTOCOL.json').read_text())
        protocol['market'].update(study_start_utc='1970-01-01T00:00:00Z',
                                  end_exclusive_utc='1970-01-01T01:00:00Z')
        quotes = [Quote(121001, 100, 101, 'synthetic-entry'),
                  Quote(122000, 106, 107, 'synthetic-exit')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(study, 'load_protocol', side_effect=[
                    protocol, ValueError('synthetic final integrity failure')]), \
                    patch.object(data_source, 'validate_acquisition',
                                 return_value={'summary': {'synthetic_fixture': True}}), \
                    patch.object(data_source, 'iter_quotes', return_value=iter(quotes)), \
                    patch.object(study.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                    patch.object(study, 'load_sealed_events', return_value=(journal(), {})), \
                    patch.object(study, 'simulate_many', wraps=study.simulate_many) as simulation:
                result, code = study.run(root / 'synthetic-acquisition', root / 'result')
            self.assertEqual(simulation.call_count, 1)
            self.assertEqual(code, 2)
            self.assertEqual(result['status'], 'BLOCKED_NO_PROFIT_RESULT')
            self.assertIn('synthetic final integrity failure', result['reason'])
            self.assertFalse(result['real_market_backtest_completed'])
            self.assertFalse(result['publication_integrity_verified'])
            self.assertFalse(result['profitability_established'])
            self.assertNotIn('policy_summaries', result)
            self.assertNotIn('simulation_sha256', result)
            self.assertFalse((root / 'result/QUOTE_SIMULATION.json').exists())
            saved = json.loads((root / 'result/RESULT.json').read_text())
            self.assertEqual(saved, result)

    def sealed_fixture(self, root, changed_role=None):
        replay = root / 'replay'
        replay.mkdir()
        event_bytes = ('\n'.join(json.dumps(e, sort_keys=True) for e in journal()) + '\n').encode()
        (replay / 'events.jsonl').write_bytes(event_bytes)
        role_files = {'engine': 'engine.py', 'runner': 'month_replay.py',
                      'spec': 'SPEC.json', 'clarifications': 'IMPLEMENTATION_CLARIFICATIONS.json',
                      'source': 'source.csv', 'acquisition': 'acquisition.json'}
        source_bytes = {role: ('synthetic-' + role).encode() for role in role_files}
        expected = {role: hashlib.sha256(body).hexdigest()
                    for role, body in source_bytes.items()}
        actual = deepcopy(expected)
        if changed_role and changed_role != 'engine_bytes_only':
            actual[changed_role] = hashlib.sha256(b'synthetic substituted source').hexdigest()
        capture = replay / 'captures'
        capture.mkdir()
        for role, filename in role_files.items():
            body = source_bytes[role]
            if role == changed_role or (role == 'engine' and changed_role == 'engine_bytes_only'):
                body = b'synthetic substituted source'
            (capture / filename).write_bytes(body)
        completion = {
            'output_hashes': {'events.jsonl': hashlib.sha256(event_bytes).hexdigest()},
            'captured_input_hashes': actual}
        completion_bytes = json.dumps(completion, sort_keys=True).encode()
        (replay / 'COMPLETION.json').write_bytes(completion_bytes)
        data = {
            'manifest_sha256': expected['acquisition'], 'csv_sha256': expected['source'],
            'source_sha256': {'research/september_2026/' + filename: expected[role]
                              for role, filename in role_files.items()
                              if role not in ('source', 'acquisition')}}
        pipeline = {
            'status': 'RESEARCH_PIPELINE_FINISHED_NOT_DEPLOYMENT',
            'acquisition_sha256': data['manifest_sha256'],
            'source_sha256': data['csv_sha256'],
            'replay_completion_sha256': hashlib.sha256(completion_bytes).hexdigest(),
            'stage_source_hashes': {filename: actual[role]
                                    for role, filename in role_files.items()}}
        (root / 'PIPELINE.json').write_text(json.dumps(pipeline, sort_keys=True))
        return data

    def test_sealed_handoff_requires_the_validated_engine_and_rules(self):
        for changed_role in (None, 'engine', 'runner', 'spec', 'clarifications', 'engine_bytes_only'):
            with self.subTest(changed_role=changed_role), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                data = self.sealed_fixture(root, changed_role)
                if changed_role is None:
                    rows, bindings = study.load_sealed_events(root, data)
                    self.assertEqual(rows, journal())
                    self.assertEqual(len(bindings), 9)
                    self.assertIn(str(root / 'replay/captures/engine.py'), bindings)
                else:
                    with self.assertRaises(ValueError):
                        study.load_sealed_events(root, data)


if __name__ == '__main__':
    unittest.main()
