"""Offline synthetic tests: no real quotes, network calls, examples or outcomes."""
import contextlib
import copy
import http.client
import importlib.util
import io
import json
import lzma
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest import mock
import urllib.error

SCRIPT = Path(__file__).parent / 'legacy_acquire.py'
spec = importlib.util.spec_from_file_location('september_acquisition', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
START = m.parse_hour(m.EXPECTED['warmup_start'])


def tick(ms, ask=101000, bid=100000, av=1.0, bv=2.0):
    return struct.pack('>IIIff', ms, ask, bid, av, bv)


def pack(*ticks):
    return lzma.compress(b''.join(ticks), format=lzma.FORMAT_ALONE)


class Response:
    def __init__(self, body, url=None, status=200, length=None, read_error=None):
        self.body = body
        self.url = url or m.hour_url(START).replace('https://www.', 'http://datafeed.')
        self.status = status
        self.headers = {'Content-Length': str(len(body)) if length is None else length,
                        'Content-Type': 'application/octet-stream'}
        self.read_error = read_error

    def read(self, _limit):
        if self.read_error:
            raise self.read_error
        return self.body

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def factory(response):
    opener = mock.Mock()
    if isinstance(response, Exception):
        opener.open.side_effect = response
    else:
        opener.open.return_value = response
    return lambda *_args: opener


class TickTests(unittest.TestCase):
    def test_equal_tick_times_ohlc_and_absent_minutes(self):
        body = pack(tick(1, bid=100000), tick(1, bid=100005),
                    tick(59000, bid=99990), tick(120100, bid=100010))
        rows, info = m.decode_hour(body, START)
        self.assertEqual(rows, [(START, 100000, 100005, 99990, 99990),
                                (START + 120, 100010, 100010, 100010, 100010)])
        self.assertEqual(info['tick_count'], 4)
        holes = m.missing_ranges(START, START + 3600, {r[0] for r in rows})
        self.assertEqual(sum(x['minutes'] for x in holes), 58)
        self.assertEqual(holes[0], {'start': START + 60, 'end_exclusive': START + 120, 'minutes': 1})
        self.assertIn(b'100.000,100.005,99.990,99.990', m.csv_bytes(rows))

    def test_reject_entire_hour_for_bad_record(self):
        bad_records = [tick(3600000), tick(1, bid=102000), tick(1, bid=0),
                       tick(1, av=float('nan')), tick(1, bv=float('inf')),
                       tick(1, av=-1), tick(1, bv=0)]
        for bad in bad_records:
            with self.subTest(bad=bad), self.assertRaises(m.AcquisitionError) as caught:
                m.decode_hour(pack(tick(0), bad), START)
            self.assertEqual(caught.exception.state, 'INVALID_TICKS')
        with self.assertRaises(m.AcquisitionError):
            m.decode_hour(pack(tick(2), tick(1)), START)
        with self.assertRaises(m.AcquisitionError):
            m.decode_hour(lzma.compress(tick(1) + b'x'), START)

    def test_decompression_and_empty_are_distinct(self):
        for body in (b'not-lzma', pack(tick(1))[:-3], pack(tick(1)) + b'junk'):
            with self.subTest(body=body), self.assertRaises(m.AcquisitionError) as caught:
                m.decode_hour(body, START)
            self.assertEqual(caught.exception.state, 'DECOMPRESSION_ERROR')
        rows, info = m.decode_hour(b'', START)
        self.assertEqual((rows, info['empty_kind']), ([], 'ZERO_BYTE_HTTP_200'))
        rows, info = m.decode_hour(pack(), START)
        self.assertEqual((rows, info['empty_kind']), ([], 'DECOMPRESSED_ZERO_TICKS'))


class TransportTests(unittest.TestCase):
    def test_statuses_never_become_silent_empty(self):
        cases = [(Response(b''), 'EMPTY_200'),
                 (Response(b'corrupt'), 'DECOMPRESSION_ERROR'),
                 (socket.timeout(), 'TIMEOUT'),
                 (urllib.error.URLError(socket.timeout()), 'TIMEOUT'),
                 (Response(pack(tick(0)), length='999999'), 'INCOMPLETE_RESPONSE'),
                 (Response(b'', read_error=http.client.IncompleteRead(b'partial', 100)), 'INCOMPLETE_RESPONSE')]
        for response, state in cases:
            with self.subTest(state=state):
                attempt, _, rows = m.fetch_attempt(START, opener_factory=factory(response))
                self.assertEqual(attempt['state'], state)
                self.assertEqual(rows, [])
        error = urllib.error.HTTPError(m.hour_url(START), 404, 'Not Found', {}, io.BytesIO(b'absent'))
        attempt, body, rows = m.fetch_attempt(START, opener_factory=factory(error))
        self.assertEqual((attempt['state'], attempt['status'], body, rows), ('HTTP_404', 404, b'absent', []))

    def test_documented_redirect_only(self):
        original = m.hour_url(START)
        allowed = original.replace('https://www.', 'http://datafeed.')
        m.check_redirect(allowed, original)
        for url in (allowed.replace('dukascopy.com', 'example.org'), allowed + '?token=a',
                    allowed.replace('/2026/', '/2025/'), allowed.replace('http://', 'http://user@')):
            with self.subTest(url=url), self.assertRaises(m.AcquisitionError):
                m.check_redirect(url, original)

    def test_success_receipt_no_prices(self):
        body = pack(tick(1))
        attempt, returned, rows = m.fetch_attempt(START, opener_factory=factory(Response(body)))
        self.assertEqual(attempt['state'], 'OK')
        self.assertEqual(attempt['body_sha256'], m.sha(body))
        self.assertTrue(attempt['received_via_plain_http'])
        self.assertEqual(returned, body)
        self.assertEqual(len(rows), 1)
        self.assertFalse(any(k in attempt for k in ('open', 'high', 'low', 'close', 'bid', 'ask')))


class ResumeAndProtocolTests(unittest.TestCase):
    def test_verified_resume_and_hash_corruption_refetch(self):
        body = pack(tick(1), tick(120000))
        fetcher = mock.Mock(side_effect=lambda epoch, _timeout: m.fetch_attempt(
            epoch, opener_factory=factory(Response(body))))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            first = m.acquire_hour(target, START, 'protocol', 'script', fetcher=fetcher)
            self.assertFalse(first[2])
            second = m.acquire_hour(target, START, 'protocol', 'script', fetcher=fetcher)
            self.assertTrue(second[2])
            self.assertEqual(fetcher.call_count, 1)
            path = target / first[0]['attempts'][-1]['body_file']
            path.write_bytes(b'corrupted')
            third = m.acquire_hour(target, START, 'protocol', 'script', fetcher=fetcher)
            self.assertFalse(third[2])
            self.assertEqual(fetcher.call_count, 2)
            self.assertEqual(third[0]['cache_events'][-1]['reason'], 'Cached body hash or length mismatch')
            self.assertEqual(m.sha(path.read_bytes()), m.sha(body))

    def test_retry_limit_and_persist_each_failure(self):
        fetcher = mock.Mock(side_effect=lambda epoch, _timeout: m.fetch_attempt(
            epoch, opener_factory=factory(socket.timeout())))
        with tempfile.TemporaryDirectory() as folder:
            receipt, rows, _ = m.acquire_hour(Path(folder), START, 'p', 's', fetcher=fetcher, sleeper=lambda _s: None)
            self.assertEqual(fetcher.call_count, 3)
            self.assertEqual(len(receipt['attempts']), 3)
            self.assertEqual(receipt['state'], 'TIMEOUT')
            self.assertEqual(rows, [])
            saved = json.loads((Path(folder) / 'hours' / (m.hour_key(START) + '.json')).read_bytes())
            self.assertEqual(saved['missing_minutes'], 60)

    def test_receipt_claim_cannot_make_partial_body_reusable(self):
        body = pack(tick(1))
        fetcher = lambda epoch, _timeout: m.fetch_attempt(epoch, opener_factory=factory(Response(body)))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            receipt, _, _ = m.acquire_hour(target, START, 'p', 's', fetcher=fetcher)
            receipt['minute_count'] = 60
            with self.assertRaises(ValueError):
                m.validated_cache(target, receipt, START, 'p', 's')

    def test_fixed_protocol_and_dry_run_make_no_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            protocol = Path(folder) / 'protocol.json'
            protocol.write_text(json.dumps(m.EXPECTED))
            target = Path(folder) / 'not-created'
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                result = m.main(['--protocol', str(protocol), '--target', str(target), '--dry-run'])
            self.assertEqual(result, 0)
            self.assertFalse(target.exists())
            output = json.loads(captured.getvalue())
            self.assertEqual(output['requested_hours'], 288)
            changed = dict(m.EXPECTED, review_end='2026-09-06T00:00:00Z')
            protocol.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                m.load_protocol(protocol)

    def test_manifest_counts_unresolved_hour_and_all_gaps(self):
        body = pack(tick(1), tick(120000))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            valid = m.acquire_hour(target, START, 'p', 's', fetcher=lambda epoch, _timeout:
                m.fetch_attempt(epoch, opener_factory=factory(Response(body))))
            missing = m.acquire_hour(target, START + 3600, 'p', 's', sleeper=lambda _s: None,
                                     fetcher=lambda epoch, _timeout: m.fetch_attempt(
                                         epoch, opener_factory=factory(socket.timeout())))
            manifest, rows = m.metadata_manifest([missing, valid], START, START + 7200,
                                                  'p', 's', 'test', False)
            self.assertEqual(manifest['minute_count'], 2)
            self.assertEqual(manifest['missing_minutes'], 118)
            self.assertFalse(manifest['all_hours_validated_or_explicit_empty_200'])
            self.assertEqual(manifest['unresolved_hours'], [{'hour_start': START + 3600, 'state': 'TIMEOUT'}])
            self.assertEqual(rows, valid[1])
            self.assertNotIn('100.000', json.dumps(manifest))

    def test_cli_probe_emits_metadata_and_cache_without_csv(self):
        body = pack(tick(1), tick(60001))
        original_acquire = m.acquire_hour
        fake_fetch = lambda epoch, _timeout: m.fetch_attempt(
            epoch, opener_factory=factory(Response(body)))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            protocol = root / 'protocol.json'
            protocol.write_text(json.dumps(m.EXPECTED))
            target = root / 'acquisition'
            with mock.patch.object(m, 'acquire_hour', side_effect=lambda *args:
                    original_acquire(*args, fetcher=fake_fetch)):
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    result = m.main(['--protocol', str(protocol), '--target', str(target),
                                     '--probe-hour', m.EXPECTED['warmup_start']])
            self.assertEqual(result, 0)
            self.assertFalse((target / 'native-1m.csv').exists())
            self.assertFalse((target / 'RUN.lock').exists())
            self.assertEqual((target / 'FROZEN_PROTOCOL.json').read_bytes(), protocol.read_bytes())
            report = json.loads((target / 'PROBE_MANIFEST.json').read_bytes())
            self.assertEqual(report['scope'], 'SINGLE_HOUR_PROBE')
            self.assertEqual(report['minute_count'], 2)
            self.assertEqual(report['processed_hours'], 1)
            self.assertFalse(report['all_hours_validated_or_explicit_empty_200'])
            self.assertNotIn('100.000', captured.getvalue())
            with self.assertRaises(ValueError):
                m.ensure_identity(target, protocol.read_bytes(), m.sha(protocol.read_bytes()), 'changed-script')


if __name__ == '__main__':
    unittest.main()
