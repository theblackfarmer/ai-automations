# ASTRA 6 — Native 3,069-return funnel capture (research-only)

**Status:** tested read-only observer for the existing source-level `MarketContextHooks._target`, NOT a change to entry or execution logic, NOT run on actual 3,069 native events. Full private QuantConnect project source remains blocked by the dedicated Assistant-node restriction. Original project **38573075** and clone **38584738** are unchanged.

## Why this is necessary

The October 28 fixed-exit QuantConnect development result reports **3,069 CANDIDATE_RETURN** events, **1,015** with targets, and **2,054** lacking targets. Those are repeated observations, not trade counts. The canonical source has `NativeSignalSession.return_diagnostics` in memory, but its Free-plan `_save_json_artifact("return-diagnostics", ...)` path is disabled and custom result logs have a small total budget. The real Q1 snapshot `funnel_diagnosis_v1.py` is missing from the current clone and may be restored by **snapshot 38595614**.

This observer takes each **real** `CANDIDATE_RETURN` decision, its frozen context and the *current* in-process zone catalog; it does **not** replay the market or build a future-defined target. It enforces exact parity against the original selector, captures the complete four-stage no-target classification, and counts distinct original candidate IDs separately from repeated minute returns. It does not infer a target's controlling-parent relevance from mere proximity: `UNVERIFIED_FROM_CATALOG` is deliberate.

## Smallest supported integration inside clone only (after original backtest source inspection)

Avoid changing the original project or creating any entry/exit criteria. The existing free plan may already use 25 source-file slots; this utility can be inlined into an *existing* diagnostic module if no new file is permitted. Confirm exact current source before applying changes.

In `native_signal_session.py`, create one collector for each contract session or pass in a **shared** collector created by the algorithm. At the existing `CANDIDATE_RETURN` processing branch, immediately after the canonical `context` and original policy decision are known, call:

```python
self._funnel.observe(capture_return(
    source_namespace=self.namespace_sha256,
    event=event, context=context,
    packet=self.pipeline.candidates[cid]["packet"],
    catalog=self.pipeline.zones,
))
```

**Do not** mutate `context`, `zones`, or policy decisions. Do not call this on later bars or after a target has changed. For all 12 contract segments, use one shared collector if the source lifecycle supports it; otherwise reconcile per-session summaries without conflating candidate identities across sessions. This observer must not be reinitialized on contract changes if a single 3,069-result census is expected.

At the existing result boundary after the existing trade/fee and source-provenance reporting, call `report(expected_total=3069, expected_missing=2054, expected_targets=1015)` on the complete collector. Output only a bounded **aggregate** summary, not raw bars or a 3,069-row export, subject to QuantConnect's final result/report budget. If the current log budget cannot fit the report, use only existing supported runtime statistic slots for grouped counters; don't bypass provider limits or suppress critical trade/source data. Do not run a new backtest until the historical snapshot is inspected for already-completed equivalent diagnostics, and freeze the reporting-only delta beforehand.

Every summary bucket distinguishes:

- `NO_OPPOSING_ZONE_KNOWN`: no direction-opposing catalog zone recognized by the return decision.
- `ALL_KNOWN_OPPOSING_INVALID`: opposing zones known, but none `target_valid` as of return cutoff.
- `VALID_OPPOSING_NOT_AHEAD`: at least one target-valid opposing zone exists, none entirely ahead under exact canonical geometry.
- `TARGET_AVAILABLE`: same known-time and nearest-valid-zone ordering as the frozen selector.

Also records `M1|M5` structural agreement pairs and **all** joint context issues; the `m1`/`m5` states are matched to selected parent **and contact lineage**, not candle colors. Counts of first-return missing-target candidates and later reacquisition are separate from total observations.

## Software receipt and limitations

- 27 focused synthetic regression tests: **PASS**, covering missing catalog, invalid zones, close-based retained target, direction, future-known target, exact selector parity, mismatched contact/clock, duplicate observations, repeated candidates, and fail-closed expected counts.
- 1,000 seeded independent canonical-selection comparisons against the archived `MarketContextHooks._target`: **1,000 matching zone IDs**, all categories represented. This verifies the observer's reproduction of the **archived source** only.
- **No actual 3,069-row native population was processed.** Do not report a breakdown of the 2,054 cases, more eligible trades, returns, R, or expectancy from this synthetic fixture.
- This observer is a **diagnostic**, not permission to use PDH/PDL, PWH/PWL, previous-month or 03:00–08:00 NY premarket as executable targets; adaptive break/reversal entries and authenticated liquidity coverage still require their separate source verification.