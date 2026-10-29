# ASTRA 6 — Adaptive population reconciliation v1

**Status:** development research, no native QuantConnect integration, no historical-profitability claim, no executable orders.

## What this enables

The preserved first-relevant-opposing-zone baseline produced 3,069 native return *observations*, of which 2,054 lacked a relevant target. The missing-target count is not a failed-trade count. This package checks whether independently supplied, later **continuation** or **new-parent reversal** structure events can be linked to those earlier observations **without backdating knowledge or changing the original rulebook**. It is *not* a detector or strategy.

Scripts in this bundle:

- `targetless_opportunity_audit_v1.py`: classifies the native return/target decision population and overlaps in rejection causes; no entry orders.
- `adaptive_market_paths_v1.py`: supplied, causal completed 15m/1h structure-break experiment and historical source-period liquidity landmarks (PDH/PDL, PWH/PWL, previous month high/low and 03:00–08:00 ET premarket).
- `adaptive_population_reconciliation_v1.py`: joins the two research views without ever treating a later confirmation as a fact available at the earlier return.

## Input contract (do not fabricate historical rows)

**Original file** `decisions.jsonl`: one genuine frozen native observation per line in the schema of `TARGETLESS_OPPORTUNITY_CONTRACT.md`, with stable `observation_id`, selected `parent_id`, all decision-time context/knowledge clocks, original selector's target boolean, and the actual zone catalog.

**Later event file** `adaptive_events.jsonl`: one real, independently authenticated later 15m/1h structure event per line. Every object must contain:

- `event_id` and `related_original_observation_id`, stable identities; `event_id == setup.id`.
- `previous_parent_id`: explicitly identified old parent for a `REVERSAL` (the opposite-direction new parent must be different); otherwise null.
- `setup`: `StructureSetup` fields from `adaptive_market_paths_v1.py` with offset-aware ISO-8601 times. Parent, correction, protected reference, exact selected opposite-color origin/stop and BOS must have their own actual source provenance.
- `liquidity_periods`: closed source-backed `PeriodEvidence` objects with explicit `dataset_id`, `contract_segment_id`, period start/end/known time, true coverage and extreme prices. Never reuse a different raw/back-adjusted price basis.
- `known_completed_minutes`: actual complete, consecutive one-minute history from each candidate period's `known_at` through the new break start; may be empty only when that period first becomes known exactly at the break start. Each bar must have `start`, `end`, `open`, `high`, `low`, `close`, `source_id`, `dataset_id`, `contract_segment_id`, `observed_from_source=true`, `fill_forward=false`. Missing minute gaps conservatively mark the previously active levels unknown. A subset of minutes does **not** prove complete continuity. If an earlier period has even one missing start, interior, or terminal minute, its levels are marked `UNKNOWN_COVERAGE`, and a result cannot be `BREAK_CONFIRMED_AWAITING_OPEN` on those levels. No scheduled-closure bridge is supported in this revision.
- `known_coverage_complete`: source's mandatory boolean completeness assertion; **true is not enough by itself**. The code also checks every source minute for each period after the level became known. Records remain unverified against an independent exchange/provider feed.
- `next_minute`: optional native observed minute, same source/segment fields, starting **exactly** at the break close. This is only a next-open geometry probe, never a native order fill.

The `related_original_observation_id` is a research association, **not** an assertion that the original observation should have been traded. The later break cannot start before the related original decision. Each candidate requires its own causal parent state, so a 1m/5m local break alone cannot flip the higher-timeframe parent.

## Commands

From this directory:

```bash
python -m unittest discover -s . -p 'test_*.py' -v
python adaptive_population_reconciliation_v1.py \
  --original-decisions decisions.jsonl \
  --adaptive-events adaptive_events.jsonl \
  --expected-total 3069 --expected-missing 2054 \
  --output results/real_source_reconciliation.json
```

The command **refuses to overwrite** any existing results. It records `original_cohort_complete=false` if counts do not exactly match the source expectation, and never labels resulting adaptive events as validated trades or historical wins.

One intentionally synthetic smoke fixture is included under `synthetic_demo/`. The expected population check is intentionally false on it.

## What is still required before historical performance

1. Recover the source-native return and candidate logs, especially missing `funnel_diagnosis_v1.py` from a preserved historical backtest snapshot if needed. Do not reconstruct undocumented source logic from summary counts.
2. Obtain the same historical **NQ** raw dated-contract minute bars with exact roll, timestamp, calendar, coverage and origin evidence. Earlier 2026 OANDA SPX and Dukascopy USATECHIDXUSD examples are different instruments/feeds.
3. Source-authenticate each selected parent, correction, new protected BOS, responsible opposite-color candle, liquidity level and 1m+5m gate state at the decision clock.
4. Freeze separate policies A (original), B (continuation), C (confirmed reversal). Run chronological one-position/portfolio-capacity controls, native bid/ask/fees/slippage, and source-valid fills/exit events before evaluating performance on genuinely untouched history.

**Never report 2,054 extra trades.** The experiment could find no new valid entries and should report that result explicitly.

## 2026-10-28 coverage and source correction (v1 source-compatible revision)

The previous research code could output a breakout and next-open geometry using an empty `known_completed_minutes` stream and `known_coverage_complete=false`. This was misleading because no price-path evidence established an unswept target. The repaired reconciliation labels such cases `WATCH` / `NOT_EVALUATED`, marks the affected levels `UNKNOWN_COVERAGE`, and preserves unverified state. It checks periods individually so a newly known period is not invalidated solely by an older missing prefix. A 15m/1h previous-close anchor must be known at the break start, and reversal confirmation may not precede old-parent invalidation. Explicit per-bar dataset and dated-contract identities prevent cross-series liquidity updates; the 03:00–08:00 premarket seal rejects mixed segments. Optional supplied next-minute records are validated even when the setup is not selected.

**Verification:** 57 synthetic tests passed locally; no historical native test, true market fill, or profit claim. Nothing was merged into the established strategy or QuantConnect projects.
