"""Verification-runner contracts, with explicit synthetic subprocess receipts.

These tests never substitute receipts for the real engine release run. Small
actual child processes separately verify unittest failure and assertion handling.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import verify_all as verification


def successful_counts(tests=1):
    return dict(tests_run=tests, tests_passed=tests, failures=0, errors=0,
                skipped=0, expected_failures=0, unexpected_successes=0,
                all_pass=True)


def successful_pipeline():
    cases = [dict(name=name, passed=True, stubbed_contract=stubbed)
             for name, stubbed in verification.PIPELINE_CASES.items()]
    return dict(total=len(cases), passed=len(cases),
                real_actual_tool_cases=sum(not case['stubbed_contract'] for case in cases),
                stubbed_contract_cases=sum(case['stubbed_contract'] for case in cases),
                synthetic_only=True, project_edits=False, network_requests=0,
                all_pass=True, tests=cases)


def write_fixture_project(root):
    project = Path(root) / 'repository' / 'research' / 'september_2026'
    project.mkdir(parents=True)
    for module in verification.REQUIRED_MODULES:
        (project / (module + '.py')).write_text('# Synthetic runner-contract placeholder.\n')
    for name in ('engine.py', 'month_replay.py', 'regression_pipeline.py'):
        (project / name).write_text('# Synthetic runner-contract placeholder.\n')
    shutil.copyfile(Path(verification.__file__), project / 'verify_all.py')
    collector = project / 'acquisition_tools'
    collector.mkdir()
    for module in verification.COLLECTOR_MODULES:
        (collector / (module + '.py')).write_text('# Synthetic runner-contract placeholder.\n')
    logic = project.parents[1] / 'tests'
    logic.mkdir()
    (logic / 'test_logic.py').write_text('def test_synthetic_logic():\n    assert True\n')
    return project


def synthetic_child(calls, mutation=None):
    """Explicit test double for parent-process orchestration contracts only."""
    def run(command, cwd, env, log_path, timeout):
        command = list(command)
        if '--_receipt' in command:
            target = Path(command[command.index('--_receipt') + 1])
            result = successful_counts()
            name = command[command.index('--_module') + 1] if '--_module' in command else command[command.index('--_worker') + 1]
        else:
            target = Path(command[command.index('--output') + 1]) / 'RESULT.json'
            result = successful_pipeline()
            name = 'pipeline'
        calls.append(dict(name=name, command=command, cwd=str(cwd), env=dict(env)))
        metadata = dict(returncode=0, timed_out=False, seconds=0.001)
        write_receipt = True
        if mutation is not None:
            write_receipt = mutation(name, result, metadata, target, len(calls)) is not False
        Path(log_path).write_text('Explicit synthetic child result for runner contract test.\n')
        if write_receipt:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result) + '\n')
        return metadata
    return run


class ReceiptValidationTests(unittest.TestCase):
    def test_complete_nonzero_counts_are_accepted(self):
        self.assertEqual(verification.validate_counts(successful_counts(7)), [])

    def test_empty_skipped_failed_and_inconsistent_success_claims_are_rejected(self):
        bad = [successful_counts(0)]
        for field in ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'):
            value = successful_counts(); value[field] = 1; bad.append(value)
        for field, value in (('tests_run', True), ('tests_run', -1), ('tests_passed', 0),
                             ('tests_passed', 1.0), ('all_pass', 1), ('all_pass', False)):
            record = successful_counts(); record[field] = value; bad.append(record)
        record = successful_counts(); del record['errors']; bad.append(record)
        for record in bad:
            with self.subTest(receipt=record):
                self.assertTrue(verification.validate_counts(record))

    def test_pipeline_requires_all_cases_counts_and_honest_scope(self):
        good = successful_pipeline()
        self.assertEqual(verification.validate_pipeline(good), [])
        broken = []
        value = copy.deepcopy(good); value['tests'].pop(); broken.append(value)
        value = copy.deepcopy(good); value['tests'][1]['name'] = value['tests'][0]['name']; broken.append(value)
        value = copy.deepcopy(good); value['tests'][0]['stubbed_contract'] = True; broken.append(value)
        value = copy.deepcopy(good); value['tests'][0]['passed'] = False; broken.append(value)
        value = copy.deepcopy(good); value['tests'][0]['skipped'] = True; broken.append(value)
        value = copy.deepcopy(good); del value['tests'][0]['stubbed_contract']; broken.append(value)
        for field, changed in (('total', 0), ('passed', True), ('real_actual_tool_cases', 0),
                               ('stubbed_contract_cases', 0), ('synthetic_only', False),
                               ('project_edits', True), ('network_requests', 1), ('all_pass', False)):
            value = copy.deepcopy(good); value[field] = changed; broken.append(value)
        for value in broken:
            with self.subTest(receipt=value):
                self.assertTrue(verification.validate_pipeline(value))

    def test_extra_pipeline_case_requires_reconciled_counts(self):
        value = successful_pipeline()
        value['tests'].append(dict(name='synthetic-added-contract', passed=True, stubbed_contract=True))
        self.assertTrue(verification.validate_pipeline(value))
        value['total'] += 1; value['passed'] += 1; value['stubbed_contract_cases'] += 1
        self.assertEqual(verification.validate_pipeline(value), [])


class DiscoveryTests(unittest.TestCase):
    def test_required_suites_remain_required_when_their_files_are_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / 'repository' / 'research' / 'september_2026'
            project.mkdir(parents=True)
            plan = verification.build_plan(project)
            modules = {entry.get('module') for entry in plan if entry['kind'] == 'unit'}
            self.assertTrue(set(verification.REQUIRED_MODULES) <= modules)
            self.assertTrue({'pipeline', 'offline_collector', 'repository_logic'} <= {entry['name'] for entry in plan})
            with self.assertRaises(FileNotFoundError):
                verification.worker('unit', 'test_engine', project=project)
            with self.assertRaises(FileNotFoundError):
                verification.worker('collector', project=project)

    def test_new_native_and_collector_modules_are_discovered_once(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder)
            (project / 'test_extra_native.py').write_text('# synthetic\n')
            (project / 'acquisition_tools' / 'test_extra_collector.py').write_text('# synthetic\n')
            plan = verification.build_plan(project)
            names = [entry['name'] for entry in plan]
            self.assertEqual(len(names), len(set(names)))
            self.assertIn('test_extra_native', names)
            extra = [entry for entry in plan if entry.get('module') == 'test_extra_collector']
            self.assertEqual(len(extra), 1)
            self.assertIs(extra[0]['acquisition'], True)
            self.assertEqual(len([entry for entry in plan if entry['kind'] == 'collector']), 1)
            self.assertFalse(any(entry.get('module') in verification.COLLECTOR_MODULES for entry in plan))


class EnvironmentTests(unittest.TestCase):
    def test_child_environment_pins_current_sources_and_removes_python_redirection(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder)
            poisoned = dict(MONTH_ENGINE_SOURCE='/foreign/engine.py', MONTH_RUNNER_SOURCE='/foreign/replay.py',
                            PYTHONOPTIMIZE='2', PYTHONPATH='/foreign/imports', PYTHONHOME='/foreign/home',
                            PYTHONSTARTUP='/foreign/startup.py', PYTHONUSERBASE='/foreign/user', MPLBACKEND='TkAgg')
            with patch.dict(os.environ, poisoned):
                environment = verification.child_environment(project, Path(folder) / 'output')
                self.assertEqual(os.environ['MONTH_ENGINE_SOURCE'], '/foreign/engine.py')
            for name in ('PYTHONOPTIMIZE', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE'):
                self.assertNotIn(name, environment)
            self.assertEqual(environment['MONTH_ENGINE_SOURCE'], str(project.resolve() / 'engine.py'))
            self.assertEqual(environment['MONTH_RUNNER_SOURCE'], str(project.resolve() / 'month_replay.py'))
            self.assertEqual(environment['PYTHONNOUSERSITE'], '1')
            self.assertEqual(environment['MPLBACKEND'], 'Agg')
            self.assertTrue(Path(environment['MPLCONFIGDIR']).is_dir())

    def test_actual_child_assertion_is_not_disabled_by_inherited_optimization(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder)
            with patch.dict(os.environ, {'PYTHONOPTIMIZE': '1'}):
                environment = verification.child_environment(project, Path(folder) / 'output')
            command = [sys.executable, '-B', '-c', 'assert False, "deliberate assertion control"']
            unsafe = dict(environment, PYTHONOPTIMIZE='1')
            bypassed = subprocess.run(command, env=unsafe, capture_output=True, text=True, timeout=10)
            protected = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(bypassed.returncode, 0, 'Positive control must actually demonstrate assertion stripping')
            self.assertNotEqual(protected.returncode, 0)
            self.assertIn('AssertionError', protected.stderr)


class ActualWorkerTests(unittest.TestCase):
    def run_worker(self, source):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder)
            module = 'test_verify_all_synthetic_probe'
            (project / (module + '.py')).write_text(source)
            old_path = list(sys.path)
            try:
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    return verification.worker('unit', module, project=project)
            finally:
                sys.path[:] = old_path
                sys.modules.pop(module, None)

    def test_real_worker_distinguishes_pass_failure_skip_and_expected_failure(self):
        common = 'import unittest\nclass Probe(unittest.TestCase):\n'
        cases = [
            ('pass', '    def test_probe(self):\n        self.assertTrue(True)\n', True),
            ('failure', '    def test_probe(self):\n        self.fail("intentional")\n', False),
            ('skip', '    @unittest.skip("intentional")\n    def test_probe(self):\n        pass\n', False),
            ('expected_failure', '    @unittest.expectedFailure\n    def test_probe(self):\n        self.fail("intentional")\n', False),
            ('unexpected_success', '    @unittest.expectedFailure\n    def test_probe(self):\n        pass\n', False),
        ]
        for label, body, expected in cases:
            with self.subTest(outcome=label):
                result = self.run_worker(common + body)
                self.assertEqual(result['tests_run'], 1)
                self.assertIs(result['all_pass'], expected)
                self.assertEqual(not verification.validate_counts(result), expected)

    def test_empty_loader_and_failing_subtest_cannot_report_success(self):
        empty = 'import unittest\ndef load_tests(loader, tests, pattern):\n    return unittest.TestSuite()\n'
        result = self.run_worker(empty)
        self.assertEqual(result['tests_run'], 0)
        self.assertFalse(result['all_pass'])
        self.assertTrue(verification.validate_counts(result))
        failure = ('import unittest\nclass Probe(unittest.TestCase):\n'
                   '    def test_probe(self):\n'
                   '        for value in (True, False):\n'
                   '            with self.subTest(value=value): self.assertTrue(value)\n')
        result = self.run_worker(failure)
        self.assertEqual(result['tests_run'], 1)
        self.assertEqual(result['failures'], 1)
        self.assertFalse(result['all_pass'])

    def test_real_worker_cli_reports_import_failure_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder)
            (project / 'test_import_failure.py').write_text('raise RuntimeError("deliberate import control")\n')
            receipt = Path(folder) / 'worker.json'
            environment = verification.child_environment(project, Path(folder) / 'output')
            result = subprocess.run([sys.executable, '-B', str(project / 'verify_all.py'), '--_worker', 'unit',
                                     '--_module', 'test_import_failure', '--_receipt', str(receipt)],
                                    cwd=project, env=environment, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            record = json.loads(receipt.read_text())
            self.assertFalse(record['all_pass'])
            self.assertEqual(record['errors'], 1)
            self.assertIn('deliberate import control', record['exception'])

    def test_worker_entrypoint_checks_its_own_dependency_version(self):
        for version, expected_exit in (('3.9.4', 0), ('3.10.8', 1)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as folder:
                receipt = Path(folder) / 'worker.json'
                with patch.object(verification, 'worker', return_value=successful_counts()), \
                        patch.object(verification.importlib.metadata, 'version', return_value=version):
                    code = verification.main(['--_worker', 'unit', '--_module', 'synthetic_contract',
                                              '--_receipt', str(receipt)])
                record = json.loads(receipt.read_text())
                self.assertEqual(code, expected_exit)
                self.assertEqual(record['matplotlib_version'], version)
                self.assertEqual(record['tests_run'], 1)
                self.assertEqual(record['tests_passed'], 1)
                self.assertIs(record['all_pass'], expected_exit == 0)


class ExecuteContractTests(unittest.TestCase):
    def execute(self, project, output, child, version='3.9.4'):
        distribution = types.SimpleNamespace(metadata={'Name': 'matplotlib'}, version=version)
        with patch.object(verification.importlib.metadata, 'version', return_value=version), \
                patch.object(verification.importlib.metadata, 'distributions', return_value=[distribution]), \
                contextlib.redirect_stdout(io.StringIO()):
            return verification.execute(project, output, run_child=child)

    def test_all_required_suites_run_and_counts_remain_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder); calls = []
            result = self.execute(project, Path(folder) / 'output', synthetic_child(calls))
            plan = verification.build_plan(project)
            self.assertTrue(result['all_pass'])
            self.assertEqual(len(calls), len(plan))
            self.assertEqual(result['counts']['suites_passed'], len(plan))
            self.assertEqual(result['counts']['engine_tests'], len(verification.REQUIRED_MODULES))
            self.assertEqual(result['counts']['collector_tests'], 1)
            self.assertEqual(result['counts']['supplemental_logic_tests'], 1)
            self.assertEqual(result['counts']['pipeline_cases'], len(verification.PIPELINE_CASES))
            self.assertTrue(result['source_unchanged'])
            self.assertTrue(all(call['env']['MONTH_ENGINE_SOURCE'] == str(project.resolve() / 'engine.py') for call in calls))

    def test_failure_does_not_suppress_later_suites_or_the_failed_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder); calls = []
            def fail_first(name, result, metadata, target, index):
                if index == 1:
                    metadata['returncode'] = 3
            output = Path(folder) / 'output'
            result = self.execute(project, output, synthetic_child(calls, fail_first))
            self.assertFalse(result['all_pass'])
            self.assertEqual(len(calls), len(verification.build_plan(project)))
            self.assertEqual(calls[-1]['name'], 'logic')
            self.assertFalse(result['suites'][0]['all_pass'])
            self.assertTrue(result['suites'][-1]['all_pass'])
            self.assertEqual(json.loads((output / 'RESULT.json').read_text())['status'], 'FAILED_OR_INCOMPLETE')

    def test_zero_exit_cannot_hide_bad_worker_receipts_or_timeout(self):
        for mode in ('missing', 'zero', 'skipped', 'expected_failure', 'timed_out', 'nonzero'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                project = write_fixture_project(folder); calls = []
                def mutate(name, result, metadata, target, index):
                    if index != 1:
                        return None
                    if mode == 'missing':
                        return False
                    if mode == 'zero':
                        result.update(tests_run=0, tests_passed=0)
                    elif mode == 'skipped':
                        result.update(tests_passed=0, skipped=1)
                    elif mode == 'expected_failure':
                        result.update(tests_passed=0, expected_failures=1)
                    elif mode == 'timed_out':
                        metadata['timed_out'] = True
                    elif mode == 'nonzero':
                        metadata['returncode'] = 9
                    return None
                result = self.execute(project, Path(folder) / 'output', synthetic_child(calls, mutate))
                self.assertFalse(result['all_pass'])
                self.assertFalse(result['suites'][0]['all_pass'])
                self.assertEqual(len(calls), len(verification.build_plan(project)))

    def test_failed_pipeline_scope_prevents_aggregate_success(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder); calls = []
            def bad_pipeline(name, result, metadata, target, index):
                if name == 'pipeline':
                    result['tests'].pop()
            result = self.execute(project, Path(folder) / 'output', synthetic_child(calls, bad_pipeline))
            self.assertFalse(result['all_pass'])
            self.assertFalse(next(suite for suite in result['suites'] if suite['kind'] == 'pipeline')['all_pass'])
            self.assertTrue(result['suites'][-1]['all_pass'])

    def test_dependency_mismatch_prevents_success_even_when_children_claim_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder); calls = []
            result = self.execute(project, Path(folder) / 'output', synthetic_child(calls), version='3.10.8')
            self.assertFalse(result['all_pass'])
            self.assertTrue(any('matplotlib==3.9.4' in error for error in result['errors']))

    def test_source_mutation_prevents_success_even_when_every_child_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder); calls = []
            def change_source(name, result, metadata, target, index):
                if index == 1:
                    (project / 'engine.py').write_text('# changed during synthetic contract run\n')
            result = self.execute(project, Path(folder) / 'output', synthetic_child(calls, change_source))
            self.assertFalse(result['all_pass'])
            self.assertFalse(result['source_unchanged'])
            self.assertIn('research/september_2026/engine.py', result['changed_source_paths'])

    def test_existing_output_is_rejected_before_any_child_or_stale_receipt_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            project = write_fixture_project(folder); calls = []
            output = Path(folder) / 'output'; output.mkdir()
            stale = output / 'RESULT.json'; stale.write_text('{"all_pass":true}\n')
            with self.assertRaises(FileExistsError):
                verification.execute(project, output, run_child=synthetic_child(calls))
            self.assertEqual(calls, [])
            self.assertEqual(stale.read_text(), '{"all_pass":true}\n')

    def test_invalid_timeout_is_rejected_before_output_creation(self):
        for timeout in (0, -1, float('nan'), float('inf'), -float('inf')):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / 'output'
                with self.assertRaises(ValueError):
                    verification.execute(Path(folder), output, timeout=timeout)
                self.assertFalse(output.exists())

    def test_public_cli_exit_status_matches_overall_receipt(self):
        for success in (False, True):
            with self.subTest(success=success), patch.object(verification, 'execute', return_value={'all_pass': success}):
                self.assertEqual(verification.main(['--output', '/unused/synthetic-contract-output']), 0 if success else 1)


if __name__ == '__main__':
    unittest.main()
