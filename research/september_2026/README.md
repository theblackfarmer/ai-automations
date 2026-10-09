# September 2026 causal research engine

Research implementation of the requested 4H → 1H → 1M sequence on NAS100 CFD. The exact swing, formation and respect algorithms are declared research interpretations in SPEC.json, not verified reproduction of discretionary teacher markings.

Only completed bars and known-time-ordered events are accepted. The runner captures inputs and source, seals decision journals, reruns every required decision cutoff in a fresh process, and binds reports to the exact proof. Future-reading positive controls must fail. A passing check proves its named software contract only.

## Run the offline checks

Python 3.9 or 3.11; reports require the pinned matplotlib version.

```sh
python -m pip install -r requirements-report.txt
python -B -m unittest test_engine test_month_replay test_export_tradingview test_verify_causality test_report test_native_daily
python -B regression_pipeline.py --project . --fixture fixtures/pipeline --output runs/pipeline-check-new
```

The pipeline fixture is explicitly synthetic. The small Sep1 native source fixture is retained solely for binary decoder and receipt regression checks. It is not a market validation sample. Each output directory must be new.

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
