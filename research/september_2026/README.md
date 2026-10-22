# September 2026 causal research engine

Research implementation of the requested 4H → 1H → 1M sequence on NAS100 CFD. The exact swing, formation and respect algorithms are declared research interpretations in SPEC.json, not verified reproduction of discretionary teacher markings.

Only completed bars and known-time-ordered events are accepted. The runner captures inputs and source, seals decision journals, reruns every required decision cutoff in a fresh process, and binds reports to the exact proof. Future-reading positive controls must fail. A passing check proves its named software contract only.

## Run the offline checks

Use Python 3.9 or 3.11. From this directory, install the report dependency once,
then run the complete ASTRA engine checks with one command:

```sh
python -m pip install -r requirements-report.txt
python -B verify_all.py --output "${TMPDIR:-/tmp}/astra6-check-$(date -u +%Y%m%dT%H%M%SZ)"
```

The command also works from the repository root:

```sh
python -B research/september_2026/verify_all.py --output "${TMPDIR:-/tmp}/astra6-check-$(date -u +%Y%m%dT%H%M%SZ)"
```

It runs every local `test_*.py` module, including engine, replay, causal boundaries,
reports, TradingView export, native source decoding, role review, and new engine
regressions. It then runs the pipeline handoff regression, the frozen offline
collector verifier, and the repository's supplemental `tests/test_logic.py` checks.
Pipeline results distinguish the five actual-tool cases from six subprocess
contract cases. New local unit-test modules are discovered automatically; the
required baseline modules must remain present.

Independent suites continue after a failure. Missing checks, failed checks,
skipped required tests, and timeouts make the final exit code nonzero. The output
folder retains `RESULT.json`, per-suite logs and receipts in `suites/`, and the
pipeline evidence in `pipeline/`. The receipt records the interpreter, dependency
versions, tested source hashes, and whether source stayed unchanged during the run.
Each output directory must be new. `--timeout-per-suite` defaults to 300 seconds.
Use an output directory outside a managed or synchronized source checkout, as
shown above. The pipeline tests deliberately substitute captured replay files;
those substitutions must remain isolated from background file restoration.

The runner pins the source paths to this checkout, selects the noninteractive
plotting backend, and uses a writable output-local plotting cache. GitHub Actions
uses this same command on both supported Python versions, writes results under
the runner's temporary directory, and uploads its evidence even when checks fail.

The pipeline fixture is explicitly synthetic. The small Sep1 native source
fixture is retained solely for binary decoder and receipt regression checks.
These checks do not establish full-month market results or profitability.

### Parent and child failure accounting

If one completed minute breaches both a parent zone and its child, the engine
retains the child's actual first return and strict far-edge failure. Parent
retirement still ends the child's permission. The repair does not infer the
intrabar order of overlap and breach, create a return when price gaps past the
entire child zone, or permit a later respect/review after retirement. Mirrored
long and short regressions cover these boundaries and shared-cutoff owner
revocation.

## Current research limit

The actual captured September input is incomplete. A wider provider acquisition also disagrees with the raw-tick reference on two Sep2 candles and is quarantined. No full-month, OOS, expectancy, profit factor, drawdown, teacher-fidelity, paper-trading or deployment verdict has passed. Partial diagnostics never become full-month evidence. The saved native transport amendment binds the pre-repair engine hash and remains historical; it must not be silently retargeted to bypass adoption checks.

```sh
python -B run_study.py --acquisition /absolute/path/to/MANIFEST.json --output /new/output
```

Use `--allow-partial` only for an explicitly incomplete diagnostic. Market datasets, recordings, private teaching material, credentials and generated results are excluded from this branch. The full teaching source map and failure evidence remain in the local research record; its hash is preserved in the frozen spec.

## TradingView boundary

Python executes externally. The exporter provides known-time-gated recorded Pine drawings plus a chart-adapter contract, not an equivalent live Pine detector. Actual symbol/feed parity, compilation and visible chart readback are required separately. No broker orders or execution capability are included.

The role reviewer is a separate deterministic accounting pass; it does not fabricate live specialist attestations. Run `python -B role_review.py --run /sealed/replay --output /new/role-review`.

The hourly collector and its original byte-pinned lineage are in `acquisition_tools/`. Run `python -B acquisition_tools/verify_offline.py` for its source-only checks. Actual provider availability is independent of these tests. The package directory must be writable to persist its shared cooldown. The quarantined native collector supports `SEPTEMBER_RATE_LIMIT_FILE` for a writable shared rate-limit file; the original acquisition protocol remains frozen.
