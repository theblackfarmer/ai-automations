"""Run every local September software check and preserve failures as well as passes.

This is synthetic contract/decoder verification, not a market backtest. Each
independent suite runs in a fresh process; a failure never suppresses later suites.
"""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import unittest


PROJECT = Path(__file__).resolve().parent
REQUIRED_MODULES = (
    'test_engine', 'test_month_replay', 'test_export_tradingview',
    'test_verify_causality', 'test_report', 'test_native_daily',
    'test_independent_boundaries', 'test_role_review',
    'test_parent_failure_observation', 'test_gap_recovery', 'test_verify_all',
)
COLLECTOR_MODULES = ('test_legacy_codec', 'test_portable_v4', 'test_portable_lineage')
PIPELINE_CASES = {
    'honest-actual': False,
    'initial-source-race': False,
    'replay-report-swap': False,
    'saved-proof-during-report': False,
    'saved-capture-during-report': False,
    'contract-consistent': True,
    'contract-refuted-zero': True,
    'contract-survives-one': True,
    'contract-explicit-failure': True,
    'contract-wrong-capture': True,
    'contract-missing-report': True,
}
SCOPE = (
    'September 4H→1H→1M research engine software contracts; synthetic pipeline '
    'fixtures, a small native decoder fixture, offline collector checks, and '
    'supplemental repository logic. No full-month market, performance, teacher '
    'fidelity, TradingView delivery, current-native or execution approval.'
)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_plan(project):
    project = Path(project)
    modules = set(REQUIRED_MODULES) | {p.stem for p in project.glob('test_*.py')}
    plan = [{'name': name, 'kind': 'unit', 'module': name} for name in sorted(modules)]
    plan.append({'name': 'pipeline', 'kind': 'pipeline'})
    plan.append({'name': 'offline_collector', 'kind': 'collector'})
    # The frozen collector runs its three original modules. Discover additions
    # without changing that byte-pinned verifier or running its tests twice.
    extras = {p.stem for p in (project / 'acquisition_tools').glob('test_*.py')} - set(COLLECTOR_MODULES)
    plan.extend({'name': 'acquisition_' + name, 'kind': 'unit', 'module': name,
                 'acquisition': True} for name in sorted(extras))
    plan.append({'name': 'repository_logic', 'kind': 'logic'})
    return plan


def child_environment(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    cache = output / 'matplotlib-cache'
    cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for name in ('PYTHONOPTIMIZE', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE'):
        env.pop(name, None)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', MPLBACKEND='Agg', MPLCONFIGDIR=str(cache),
               MONTH_ENGINE_SOURCE=str(project / 'engine.py'),
               MONTH_RUNNER_SOURCE=str(project / 'month_replay.py'))
    return env


def validate_counts(receipt):
    errors = []
    fields = ('tests_run', 'tests_passed', 'failures', 'errors', 'skipped',
              'expected_failures', 'unexpected_successes')
    for key in fields:
        if type(receipt.get(key)) is not int or receipt[key] < 0:
            errors.append('Missing or invalid count: ' + key)
    if errors:
        return errors
    if receipt['tests_run'] == 0:
        errors.append('No tests ran')
    for key in fields[2:]:
        if receipt[key]:
            errors.append('{}: {}'.format(key, receipt[key]))
    if receipt['tests_passed'] != receipt['tests_run']:
        errors.append('Not every executed test passed')
    if receipt.get('all_pass') is not True:
        errors.append('Worker did not report complete success')
    return errors


def validate_pipeline(receipt):
    errors = []
    tests = receipt.get('tests')
    if not isinstance(tests, list) or not all(isinstance(t, dict) for t in tests):
        return ['Pipeline receipt has no valid case records']
    names = [t.get('name') for t in tests]
    if not all(isinstance(name, str) for name in names) or len(set(names)) != len(names):
        return ['Pipeline case names are missing or duplicated']
    missing = sorted(set(PIPELINE_CASES) - set(names))
    if missing:
        errors.append('Missing pipeline cases: ' + ', '.join(missing))
    for case in tests:
        name = case['name']
        if case.get('passed') is not True or case.get('skipped', False) is not False:
            errors.append('Pipeline case did not pass: ' + name)
        if type(case.get('stubbed_contract')) is not bool:
            errors.append('Pipeline case has no actual/stubbed classification: ' + name)
        elif name in PIPELINE_CASES and case['stubbed_contract'] is not PIPELINE_CASES[name]:
            errors.append('Wrong actual/stubbed classification: ' + name)
    expected = {
        'total': len(tests), 'passed': sum(t.get('passed') is True for t in tests),
        'real_actual_tool_cases': sum(t.get('stubbed_contract') is False for t in tests),
        'stubbed_contract_cases': sum(t.get('stubbed_contract') is True for t in tests),
        'network_requests': 0,
    }
    for key, value in expected.items():
        if type(receipt.get(key)) is not int or receipt[key] != value:
            errors.append('Pipeline count mismatch: ' + key)
    for key, value in (('all_pass', True), ('synthetic_only', True), ('project_edits', False)):
        if receipt.get(key) is not value:
            errors.append('Pipeline scope/status mismatch: ' + key)
    return errors


class CountingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successes = 0

    def addSuccess(self, test):
        self.successes += 1
        super().addSuccess(test)


def result_counts(result):
    counts = dict(tests_run=result.testsRun, tests_passed=result.successes,
                  failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), expected_failures=len(result.expectedFailures),
                  unexpected_successes=len(result.unexpectedSuccesses))
    counts['all_pass'] = bool(result.wasSuccessful() and counts['tests_run'] > 0
                              and counts['tests_passed'] == counts['tests_run']
                              and not counts['skipped'] and not counts['expected_failures'])
    return counts


def load_file(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def worker(kind, module=None, acquisition=False, project=PROJECT):
    """Run one suite in the already isolated child and return real unittest counts."""
    project = Path(project).resolve()
    if kind == 'collector':
        collector_root = project / 'acquisition_tools'
        for name in COLLECTOR_MODULES:
            if not (collector_root / (name + '.py')).is_file():
                raise FileNotFoundError('Required collector module: ' + name)
        captured = []
        original_runner = unittest.TextTestRunner

        class CaptureRunner(original_runner):
            def __init__(self, *args, **kwargs):
                kwargs['resultclass'] = CountingResult
                super().__init__(*args, **kwargs)

            def run(self, suite):
                result = super().run(suite)
                captured.append(result)
                return result

        # Observe the original verifier's TestResult; do not alter its sources,
        # frozen manifest, network denial, dry-run assertions, or test suite.
        unittest.TextTestRunner = CaptureRunner
        try:
            verifier = load_file(collector_root / 'verify_offline.py', '_verify_all_collector')
            code = verifier.main()
        finally:
            unittest.TextTestRunner = original_runner
        if len(captured) != 1:
            raise ValueError('Original collector did not produce one complete test result')
        result = result_counts(captured[0])
        result.update(collector_exit_code=code, modules=list(COLLECTOR_MODULES),
                      original_verifier_completed=code == 0)
        if code != 0:
            result['all_pass'] = False
        return result
    if kind == 'logic':
        source = project.parents[1] / 'tests' / 'test_logic.py'
        loaded = load_file(source, '_verify_all_repository_logic')
        functions = [value for name, value in sorted(vars(loaded).items())
                     if name.startswith('test_') and inspect.isfunction(value)
                     and value.__module__ == loaded.__name__]
        suite = unittest.TestSuite(unittest.FunctionTestCase(fn) for fn in functions)
    else:
        directory = project / 'acquisition_tools' if acquisition else project
        source = directory / (module + '.py')
        if not source.is_file():
            raise FileNotFoundError('Required test module: ' + str(source))
        sys.path.insert(0, str(directory))
        loaded = load_file(source, module)
        suite = unittest.defaultTestLoader.loadTestsFromModule(loaded)
    result = unittest.TextTestRunner(verbosity=2, resultclass=CountingResult).run(suite)
    return result_counts(result)


def run_process(command, cwd, env, log_path, timeout):
    started = time.monotonic()
    timed_out = False
    with Path(log_path).open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=os.name == 'posix')
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            code = process.wait()
            log.write('\nSuite exceeded timeout of {} seconds.\n'.format(timeout))
    return dict(returncode=code, timed_out=timed_out, seconds=round(time.monotonic() - started, 6))


def source_snapshot(project):
    project = Path(project).resolve()
    repository = project.parents[1]
    candidates = [p for p in project.iterdir() if p.is_file()]
    for folder in ('fixtures', 'acquisition_tools'):
        candidates.extend(p for p in (project / folder).rglob('*')
                          if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    candidates.extend((repository / 'automations').glob('*.py'))
    candidates.append(repository / 'tests' / 'test_logic.py')
    return {str(p.relative_to(repository)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(candidates)) if p.is_file()}


def summarize(suites):
    counts = dict(suites=len(suites), suites_passed=sum(s.get('all_pass') is True for s in suites),
                  unittest_tests=0, unittest_passed=0, skipped=0, expected_failures=0,
                  failures=0, errors=0, unexpected_successes=0, engine_tests=0, engine_passed=0,
                  pipeline_cases=0, pipeline_passed=0, pipeline_actual_cases=0, pipeline_stubbed_cases=0,
                  collector_tests=0, supplemental_logic_tests=0)
    for suite in suites:
        result = suite.get('result') or {}
        if suite['kind'] == 'pipeline':
            cases = result.get('tests', [])
            if isinstance(cases, list):
                counts['pipeline_cases'] += len(cases)
                counts['pipeline_passed'] += sum(isinstance(t, dict) and t.get('passed') is True for t in cases)
                counts['pipeline_actual_cases'] += sum(isinstance(t, dict) and t.get('stubbed_contract') is False for t in cases)
                counts['pipeline_stubbed_cases'] += sum(isinstance(t, dict) and t.get('stubbed_contract') is True for t in cases)
        else:
            for target, source in (('unittest_tests', 'tests_run'), ('unittest_passed', 'tests_passed'),
                                   ('skipped', 'skipped'), ('expected_failures', 'expected_failures'),
                                   ('failures', 'failures'), ('errors', 'errors'),
                                   ('unexpected_successes', 'unexpected_successes')):
                value = result.get(source, 0)
                if type(value) is int and value >= 0:
                    counts[target] += value
            if suite['kind'] == 'unit' and not suite.get('acquisition'):
                for target, source in (('engine_tests', 'tests_run'), ('engine_passed', 'tests_passed')):
                    if type(result.get(source)) is int and result[source] >= 0:
                        counts[target] += result[source]
            key = {'collector': 'collector_tests', 'logic': 'supplemental_logic_tests'}.get(suite['kind'])
            if key and type(result.get('tests_run')) is int:
                counts[key] += result['tests_run']
    return counts


def execute(project, output, timeout=300, run_child=None):
    project, output = Path(project).resolve(), Path(output).resolve()
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'suites').mkdir()
    env = child_environment(project, output)
    runner = run_process if run_child is None else run_child
    started = time.monotonic()
    before = source_snapshot(project)
    problems = []
    try:
        matplotlib_version = importlib.metadata.version('matplotlib')
    except importlib.metadata.PackageNotFoundError:
        matplotlib_version = None
    if matplotlib_version != '3.9.4':
        problems.append('Report dependency requires matplotlib==3.9.4; found ' + str(matplotlib_version))
    plan = build_plan(project)
    receipt = dict(status='RUNNING', all_pass=False, scope=SCOPE, execution_allowed=False,
                   source_sha256=before, source_unchanged=None, errors=problems, plan=plan, suites=[],
                   environment=dict(python=sys.version, executable=sys.executable,
                                    matplotlib_version=matplotlib_version,
                                    distributions=sorted((distribution.metadata['Name'], distribution.version)
                                                         for distribution in importlib.metadata.distributions()),
                                    removed_import_or_optimization_overrides=[
                                        'PYTHONOPTIMIZE', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE'],
                                    overrides={name: env[name] for name in (
                                        'PYTHONNOUSERSITE', 'MPLBACKEND', 'MPLCONFIGDIR',
                                        'MONTH_ENGINE_SOURCE', 'MONTH_RUNNER_SOURCE')}))
    write_json(output / 'RESULT.json', receipt)
    for entry in plan:
        name, kind = entry['name'], entry['kind']
        folder = output / 'suites' / name
        folder.mkdir()
        log_path = folder / 'output.log'
        if kind == 'pipeline':
            result_path = output / 'pipeline' / 'RESULT.json'
            command = [sys.executable, '-B', str(project / 'regression_pipeline.py'),
                       '--project', str(project), '--fixture', str(project / 'fixtures' / 'pipeline'),
                       '--output', str(output / 'pipeline')]
        else:
            result_path = folder / 'RESULT.json'
            command = [sys.executable, '-B', str(project / 'verify_all.py'), '--_worker', kind,
                       '--_receipt', str(result_path)]
            if kind == 'unit':
                command += ['--_module', entry['module']]
                if entry.get('acquisition'):
                    command.append('--_acquisition')
        item = dict(entry, command=command, log=str(log_path.relative_to(output)),
                    receipt=str(result_path.relative_to(output)), errors=[])
        print('Running ' + name, flush=True)
        try:
            item.update(runner(command, project, env, log_path, timeout))
        except Exception as exc:
            item.update(returncode=None, timed_out=False, seconds=0)
            item['errors'].append('Child launch failed: {}: {}'.format(type(exc).__name__, exc))
            log_path.write_text(traceback.format_exc(), encoding='utf-8')
        if item.get('returncode') != 0:
            item['errors'].append('Child exit code: ' + str(item.get('returncode')))
        if item.get('timed_out'):
            item['errors'].append('Child timed out')
        try:
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if not isinstance(result, dict):
                raise ValueError('Receipt must be an object')
            item['result'] = result
            item['errors'].extend(validate_pipeline(result) if kind == 'pipeline' else validate_counts(result))
        except Exception as exc:
            item['errors'].append('Missing or unreadable receipt: {}: {}'.format(type(exc).__name__, exc))
            if kind == 'pipeline':
                # Its honest baseline is a prerequisite for later attacks. If
                # that prerequisite aborts, retain observed cases without
                # pretending a complete regression receipt exists.
                partial = []
                for path in sorted((output / 'pipeline' / 'cases').glob('*/CHECK.json')):
                    try:
                        case = json.loads(path.read_text(encoding='utf-8'))
                        if isinstance(case, dict):
                            partial.append(case)
                    except (OSError, ValueError):
                        pass
                item['result'] = dict(receipt_complete=False, tests=partial)
        item['all_pass'] = not item['errors']
        receipt['suites'].append(item)
        receipt['counts'] = summarize(receipt['suites'])
        write_json(output / 'RESULT.json', receipt)
        print('{}: {}'.format(name, 'PASS' if item['all_pass'] else 'FAIL'), flush=True)
    after = source_snapshot(project)
    receipt['source_unchanged'] = before == after
    if before != after:
        problems.append('Source or fixture files changed during verification')
        receipt['changed_source_paths'] = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        receipt['source_sha256_after'] = after
    receipt['all_pass'] = not problems and len(receipt['suites']) == len(plan) and all(s['all_pass'] for s in receipt['suites'])
    receipt['status'] = 'ALL_SOFTWARE_CHECKS_PASSED' if receipt['all_pass'] else 'FAILED_OR_INCOMPLETE'
    receipt['seconds'] = round(time.monotonic() - started, 6)
    write_json(output / 'RESULT.json', receipt)
    print(json.dumps({key: receipt[key] for key in ('status', 'all_pass', 'counts', 'seconds')}), flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New exclusive directory for logs, cases, and RESULT.json')
    parser.add_argument('--timeout-per-suite', type=float, default=300)
    parser.add_argument('--_worker', choices=('unit', 'collector', 'logic'), help=argparse.SUPPRESS)
    parser.add_argument('--_module', help=argparse.SUPPRESS)
    parser.add_argument('--_receipt', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--_acquisition', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        if args._receipt is None or (args._worker == 'unit' and not args._module):
            parser.error('Internal worker requires receipt and module')
        try:
            result = worker(args._worker, args._module, args._acquisition)
        except Exception as exc:
            traceback.print_exc()
            result = dict(all_pass=False, tests_run=0, tests_passed=0, failures=0,
                          errors=1, skipped=0, expected_failures=0, unexpected_successes=0,
                          exception='{}: {}'.format(type(exc).__name__, exc))
        try:
            result['matplotlib_version'] = importlib.metadata.version('matplotlib')
        except importlib.metadata.PackageNotFoundError:
            result['matplotlib_version'] = None
        if result['matplotlib_version'] != '3.9.4':
            result['all_pass'] = False
            result['environment_errors'] = ['Child report dependency is not matplotlib==3.9.4']
        write_json(args._receipt, result)
        return 0 if not validate_counts(result) else 1
    if args.output is None:
        parser.error('--output is required; use a new directory')
    if not math.isfinite(args.timeout_per_suite) or args.timeout_per_suite <= 0:
        parser.error('--timeout-per-suite must be finite and positive')
    return 0 if execute(PROJECT, args.output, args.timeout_per_suite)['all_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
