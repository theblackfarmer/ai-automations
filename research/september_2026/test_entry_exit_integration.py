"""Engine -> journal -> adapter -> exits, offline and research-only.

Eligible positive cases use public-feed synthetic bars, never market claims.
The hash-pinned real September fixture has no warm-up: it tests honest zero
eligibility, not a full-month strategy or execution validation.
"""
import copy
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from acquire_native_daily_v2 import decode, epoch
from engine import Engine
from entry_review_adapter_v1 import candidates
from exit_comparison_v1 import evaluate
from test_engine import SPEC, same_cutoff_target_records

ROOT = Path(__file__).resolve().parent


def eligible_records():
    records = same_cutoff_target_records()
    # Public-feed neighbor: respect starts after HTF target recognition.
    # No engine state injection and no retroactive target authorization.
    for tf, bar in records:
        if tf == 1:
            bar['time'] += 60
            bar['known_at'] += 60
    # Keep each overlapping H1 close consistent with its final M1 close.
    for tf, higher in records:
        if tf == 60:
            last = [b for mt, b in records if mt == 1 and b['known_at'] == higher['known_at']]
            if last:
                higher['close'] = last[0]['close']
    return records


def run_engine(records):
    engine = Engine(SPEC)
    engine.feed_batch(copy.deepcopy(records))
    return engine.events


def real_fixture():
    directory = ROOT / 'fixtures' / 'sep1-native'
    receipt = json.loads((directory / 'RECEIPT.json').read_text())
    body = (directory / (receipt['body_sha256'] + '.bi5')).read_bytes()
    if hashlib.sha256(body).hexdigest() != receipt['body_sha256']:
        raise AssertionError('Source body digest mismatch')
    rows, excluded = decode(body, epoch('2026-09-01T00:00:00Z'))
    bars = {t: tuple(v / 1000 for v in prices) for t, prices in rows.items()}
    records = []
    for tf in (240, 60, 1):
        for start in sorted({t // (tf * 60) * (tf * 60) for t in bars}):
            minutes = list(range(start, start + tf * 60, 60))
            if not all(t in bars for t in minutes):
                continue  # Never fill a missing minute to build HTF context.
            values = [bars[t] for t in minutes]
            records.append((tf, dict(time=start, known_at=start + tf * 60,
                open=values[0][0], high=max(v[1] for v in values),
                low=min(v[2] for v in values), close=values[-1][3],
                complete=True, source_id='SAVED_DUKASCOPY_BID:%s:%s' % (tf, start))))
    return records, bars, excluded


class EntryExitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.records = eligible_records()
        self.events = run_engine(self.records)
        self.reviews = [e for e in self.events if e['kind'] == 'ENTRY_REVIEW']
        self.assertEqual(len(self.reviews), 1)
        self.t = self.reviews[0]['known_at']
        respect = next(e for e in self.events if e['kind'] == 'RESPECT_OBSERVED')
        self.stop = respect['zone_low']
        self.entry = self.stop + 2
        self.bars = {self.t: (self.entry, self.entry + .5, self.stop - .5, self.stop),
                     self.t + 60: (self.entry, self.entry + 10, self.entry, self.entry + 5)}

    def test_public_engine_journal_to_entry_minute_stop(self):
        events = [json.loads(json.dumps(e)) for e in self.events]
        self.assertNotIn('zone_low', self.reviews[0])
        self.assertEqual(self.reviews[0]['direction'], 'long')
        signals, blocked = candidates(events, self.bars)
        self.assertEqual(blocked, [])
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]['stop'], self.stop)
        results = evaluate(self.bars, signals)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r['reason'] == 'STOP' and r['exit_epoch'] == self.t for r in results))
        self.assertTrue(all(e['execution_allowed'] is False for e in events))

    def test_synthetic_higher_timeframe_close_matches_last_minute(self):
        for tf, higher in self.records:
            if tf != 60:
                continue
            for mt, minute in self.records:
                if mt == 1 and minute['known_at'] == higher['known_at']:
                    self.assertEqual(higher['close'], minute['close'])

    def test_public_short_engine_to_exit(self):
        records = copy.deepcopy(self.records)
        for _, b in records:
            b['open'], b['high'], b['low'], b['close'] = (
                100 - b['open'], 100 - b['low'], 100 - b['high'], 100 - b['close'])
        events = run_engine(records)
        bars = {t: (100-o, 100-l, 100-h, 100-c) for t, (o,h,l,c) in self.bars.items()}
        signals, blocked = candidates(events, bars)
        self.assertEqual(blocked, [])
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]['side'], 'SHORT')
        self.assertTrue(all(r['reason'] == 'STOP' for r in evaluate(bars, signals)))

    def test_same_cutoff_target_stays_blocked_end_to_end(self):
        events = run_engine(same_cutoff_target_records())
        signals, blocked = candidates(events, self.bars)
        self.assertEqual(signals, [])
        self.assertEqual(len(blocked), 1)
        self.assertEqual(evaluate(self.bars, signals), [])

    def test_missing_exact_next_open_does_not_jump_to_later_bar(self):
        signals, blocked = candidates(self.events, {self.t + 60: self.bars[self.t + 60]})
        self.assertEqual(signals, [])
        self.assertEqual(blocked[0]['reason'], 'NEXT_OPEN_UNOBSERVED')

    def assert_prefix_equal(self, adapter):
        prefix = run_engine([(tf, b) for tf, b in self.records if b['known_at'] <= self.t])
        # Only the contemporaneous open is available for hypothetical entry.
        open_only = {self.t: (self.entry,)}
        future_records = copy.deepcopy(self.records)
        future_records.append((1, dict(time=self.t, known_at=self.t + 60,
            open=28.8, high=29.0, low=27.0, close=27.5,
            complete=True, source_id='SYNTHETIC:FUTURE')))
        full_events = run_engine(future_records)
        self.assertGreater(len(future_records), len(self.records))
        self.assertTrue(any(e['known_at'] > self.t for e in full_events))
        self.assertEqual([e for e in full_events if e['known_at'] <= self.t], prefix)
        self.assertEqual(adapter(full_events, self.bars), adapter(prefix, open_only))

    def test_candidate_prefix_causality(self):
        self.assert_prefix_equal(candidates)

    def test_prefix_audit_rejects_injected_future_dependency(self):
        def leaky(events, bars):
            rows, blocked = candidates(events, bars)
            if self.t + 60 in bars:
                rows[0]['entry'] = bars[self.t + 60][3]
            return rows, blocked
        with self.assertRaises(AssertionError):
            self.assert_prefix_equal(leaky)

    def test_exit_suffix_cannot_rewrite_completed_outcome(self):
        signals, _ = candidates(self.events, self.bars)
        self.assertEqual(evaluate(self.bars, signals), evaluate({self.t: self.bars[self.t]}, signals))

    def test_cli_journal_csv_exit_and_no_execution_receipts(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            events = directory / 'events.jsonl'
            events.write_text(''.join(json.dumps(e) + '\n' for e in self.events))
            bars = directory / 'bars.csv'
            with bars.open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['epoch', 'open', 'high', 'low', 'close'])
                writer.writerows((t, *v) for t, v in self.bars.items())
            adapted, exits = directory / 'adapter', directory / 'exits'
            for script, args in (
                ('entry_review_adapter_v1.py', ['--events', str(events), '--bars', str(bars), '--output', str(adapted)]),
                ('exit_comparison_v1.py', ['--signals', str(adapted / 'signals.csv'), '--bars', str(bars), '--output', str(exits)])):
                result = subprocess.run([sys.executable, '-B', str(ROOT / script), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((adapted / 'MANIFEST.json').read_text())
            summary = json.loads((exits / 'summary.json').read_text())
            self.assertEqual(manifest['eligible'], 1)
            self.assertIs(manifest['fill_verified'], False)
            self.assertIs(manifest['execution_allowed'], False)
            self.assertIs(summary['execution_allowed'], False)
            self.assertEqual(summary['status'], 'RESEARCH_ONLY_NOT_VALIDATED_PERFORMANCE')
            rows = [json.loads(line) for line in (exits / 'exits.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row['reason'] == 'STOP' for row in rows))

    def test_saved_real_bid_source_does_not_invent_warmup_or_entries(self):
        records, bars, excluded = real_fixture()
        self.assertEqual(len(bars), 1335)
        self.assertEqual(len(excluded), 105)
        self.assertFalse(set(bars) & set(excluded))
        events = run_engine(records)
        self.assertGreater(len(events), 0)
        self.assertTrue(all(e['execution_allowed'] is False for e in events))
        signals, blocked = candidates(events, bars)
        self.assertEqual(signals, [])
        self.assertEqual(blocked, [])
        self.assertEqual(evaluate(bars, signals), [])


if __name__ == '__main__':
    unittest.main()
