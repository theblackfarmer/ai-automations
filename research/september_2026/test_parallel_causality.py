"""Synthetic proofs for bounded parallel cold-prefix scheduling; no market results."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path

import engine
import month_replay as runner
import verify_causality as audit
from test_verify_causality import SPEC, synthetic_source, input_journal


def without_timing(value):
    if isinstance(value, dict):
        return {k: without_timing(v) for k, v in value.items() if k != 'elapsed_seconds'}
    if isinstance(value, list):
        return [without_timing(v) for v in value]
    return value


def runtime(runner_path=None):
    files = {name: dict(path=str(path), sha256=audit.byte_hash(path))
             for name, path in [('engine', Path(engine.__file__)),
                                ('runner', Path(runner_path or runner.__file__))]}
    return audit.FreshCapturedRuntime(files)


class ParallelColdPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start, cls.end, cls.rows = synthetic_source()
        cls.source_hash = 'a' * 64
        cls.plan, _ = runner.assemble(cls.rows, cls.start, cls.end, cls.source_hash)
        cls.events, _, _ = runner.replay(engine.Engine, SPEC, cls.plan)

    def test_native_serial_parallel_exact_semantic_equivalence(self):
        cold = runtime()
        reports = []
        for workers in (1, 2):
            progress = []
            result = audit.audit(None, cold.replay, SPEC, self.plan, self.events,
                input_audit=input_journal(self.plan, self.events), source_rows=self.rows,
                assemble_fn=cold.assemble, source_hash=self.source_hash,
                start=self.start, end=self.end, causal_workers=workers,
                progress=lambda n, total, row: progress.append((n, total, row['cutoff'])))
            self.assertEqual(result['failures'], [])
            self.assertEqual(result['status'], 'SURVIVES_PROVISIONAL_CAUSAL_PREFIX_ONLY')
            self.assertEqual(result['audited_cutoffs'], result['required_unique_cutoffs'])
            self.assertEqual([p[2] for p in progress], sorted({e['known_at'] for e in self.events if audit.is_decision(e)}))
            self.assertEqual([p[0] for p in progress], list(range(1, len(progress) + 1)))
            reports.append(without_timing(result))
        self.assertEqual(reports[0], reports[1])
        controls = audit.bounded_controls(None, cold.replay, SPEC, self.plan, self.events)
        self.assertTrue(controls['valid'])
        self.assertTrue(controls['deliberately_future_reading_detected'])

    def test_parallel_cold_process_refutes_future_global_memo(self):
        # A process/global cache must never transmit full-run future information
        # into a prefix. Fresh subprocesses expose this deliberately bad runner.
        code = '''
_LAST_CLOCK = None
_original = replay
def replay(engine_type, spec, plan, out=None):
    global _LAST_CLOCK
    if _LAST_CLOCK is None: _LAST_CLOCK = plan[-1][0]
    events, snapshot, head = _original(engine_type, spec, plan, out)
    for event in events:
        if event['kind'] == 'MAJOR_BOS':
            event['DELIBERATE_FUTURE_MEMO'] = _LAST_CLOCK
            payload = {k:v for k,v in event.items() if k != 'id'}
            event['id'] = event['kind'].lower()+':'+hashlib.sha256(canon([payload]).encode()).hexdigest()[:24]
    return events, snapshot, head
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runner.py'
            path.write_text(Path(runner.__file__).read_text() + code)
            cold = runtime(path)
            events, _, _ = cold.replay(None, SPEC, self.plan)
            reports = [audit.audit(None, cold.replay, SPEC, self.plan, events, causal_workers=n)
                       for n in (1, 2)]
            self.assertEqual(without_timing(reports[0]), without_timing(reports[1]))
            self.assertEqual(reports[1]['status'], 'REFUTED')
            self.assertTrue(any(f['gate'] == 'COLD_PREFIX' for f in reports[1]['failures']))

    def test_parallel_prefix_worker_exception_propagates(self):
        code = f'''
_original = replay
def replay(engine_type, spec, plan, out=None):
    if plan[-1][0] < {self.plan[-1][0]}:
        raise ValueError('DELIBERATE_PREFIX_WORKER_FAILURE')
    return _original(engine_type, spec, plan, out)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runner.py'
            path.write_text(Path(runner.__file__).read_text() + code)
            cold = runtime(path)
            with self.assertRaisesRegex(RuntimeError, 'DELIBERATE_PREFIX_WORKER_FAILURE'):
                audit.audit(None, cold.replay, SPEC, self.plan, self.events, causal_workers=2)

    def test_worker_bound_and_chronological_merge(self):
        lock = threading.Lock()
        together = threading.Barrier(2)
        counts = {'active': 0, 'peak': 0}
        def job(cutoff):
            with lock:
                counts['active'] += 1
                counts['peak'] = max(counts['peak'], counts['active'])
            if cutoff < 2:
                together.wait(timeout=10)
            with lock:
                counts['active'] -= 1
            return cutoff
        self.assertEqual(list(audit.ordered_cutoffs(job, range(6), 2)), list(range(6)))
        self.assertEqual(counts['peak'], 2)

    def test_invalid_or_unisolated_parallel_runtime_rejected(self):
        for workers in (0, 3, -1, True, 1.0):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                audit.audit(engine.Engine, runner.replay, SPEC, self.plan, self.events, causal_workers=workers)
        with self.assertRaisesRegex(ValueError, 'fresh captured runtime'):
            audit.audit(engine.Engine, runner.replay, SPEC, self.plan, self.events, causal_workers=2)


if __name__ == '__main__':
    unittest.main()
