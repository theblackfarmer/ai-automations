"""Run every offline profit-layer test and preserve an exact software receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest


HERE = Path(__file__).resolve().parent


def hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(HERE.glob('*.py'))}


class CountingResult(unittest.TextTestResult):
    successes = 0

    def addSuccess(self, test):
        self.successes += 1
        super().addSuccess(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    before = hashes()
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern='test_*.py')
    with (args.output / 'tests.log').open('w') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2,
                                        resultclass=CountingResult).run(suite)
    counts = dict(tests_run=result.testsRun, tests_passed=result.successes,
                  failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), expected_failures=len(result.expectedFailures),
                  unexpected_successes=len(result.unexpectedSuccesses))
    unchanged = hashes() == before
    passed = bool(result.wasSuccessful() and counts['tests_run'] > 0 and unchanged
                  and counts['tests_passed'] == counts['tests_run'])
    receipt = dict(schema='ASTRA6_QUOTE_SOFTWARE_VERIFICATION_V1', all_pass=passed,
                   counts=counts, python=sys.version, source_sha256=before,
                   sources_unchanged=unchanged, seconds=time.perf_counter() - started,
                   scope='Synthetic software and data-admission tests only; no market return.',
                   execution_allowed=False, profitability_established=False,
                   real_market_backtest_completed=False)
    (args.output / 'RESULT.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps(receipt))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
