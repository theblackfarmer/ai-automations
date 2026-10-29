# ASTRA 6 — Causal multi-source liquidity target fallback V1

**Research-only. No QuantConnect project was edited, no orders were placed, no profitability was established.** This proposal is a *separate target-policy variant* for evaluation, not a silent replacement of the frozen ASTRA entry/exit policy.

## Policy under test

1. **First relevant opposing supply/demand zone available:** retain the exact original QuantConnect `_target` zone ID and price unchanged. The native zone selector already evaluates 15-minute, 1-hour and 4-hour opposing formations. Confirmed lower-timeframe swing liquidity is not the same thing as supply/demand zones.
2. **No original opposing-zone target:** search in the trade's direction for eligible, source-confirmed liquidity. For a **long**, buy-side high prices must be strictly above the entry decision reference; for a **short**, sell-side lows must be strictly below. Search the nearest price, **not** the first level in a hardcoded timeframe-priority list.
3. Candidate families:
   - A **confirmed** 1m, 5m, 15m, 1h or 4h structural pivot high/low with exact native source, confirmation time and matching selected-parent ID.
   - Previous completed day high/low (ETH and RTH explicitly different), previous week high/low, previous month high/low.
   - Complete **3:00–8:00 a.m. America/New_York** premarket high/low, only usable **at or after 8:00 a.m.**, with all 300 source minutes sealed.
   - Additional source-verified complete overnight, Asia, London and opening-range highs/lows if the upstream data/calendars authenticate their boundaries.
4. A target is **excluded** if future-known, behind/at entry, stale/swept/touched, incomplete, from a different dataset/dated contract, from a replaced "previous" session, or from an unrelated parent (structural swings). The source-known 1m minute stream from the level's recognition to the decision must be contiguous. Missing minutes or unsourced market closure bridges cause `NO_VERIFIED_TARGET`, never a fabricated level.
5. If no eligible level remains, **no verified target**. The system does *not* infer an economic opportunity or invent a TP.
6. After the next observed price open, the **same original source-candle far-wick stop** and frozen target must still be geometrically valid; otherwise an entry proposal is rejected. This module only supplies a non-executing geometry check; no fill is inferred.

## Strategy constraints preserved

- The original parent → correction → resumption → responsible-zone return entry still requires confirmed **both 1m AND 5m** structural agreement. *Target search does not waive or substitute for this entry gate.*
- The selected origin candle's far-wick stop is never widened, moved, rounded or inferred from a PDH/PDL.
- The frozen first relevant opposing-zone target remains the comparison control. Liquidity fallback is **an unapproved alternative target policy**; historical trade results and source-fidelity verification are required before considering deployment.
- Continuation-break and confirmed-reversal entry routes in the existing draft PR remain **separate research candidates**. Their entry gates are not changed by this module.
- Session highs/lows and confirmed pivots are **liquidity proxies**, not authenticated resting-order inventory. A caller-supplied `PeriodEvidence(coverage_complete=True)` is not independent proof that the raw source period itself was correct. An external market-calendar and original-source adapter must authenticate its high/low and actual price continuity before a real experiment.

## Module contract

`select_fallback_target(side, decision_at, decision_reference, exact_far_wick_stop, canonical_target, liquidity_levels, source_periods, known_completed_minutes, dataset_id, contract_segment_id, confirmed_swings=(), selected_parent_id=None)`.

- `canonical_target` comes **unmodified** from `CausalMarketPipeline.context_for(...)["target"]`; native `known_at` is a Unix UTC epoch integer. The source target is always preferred when available.
- `liquidity_levels` comes from the existing `CausalLiquidityBook.levels(decision_at)` (its period fields must correspond to `source_periods`, which are the authentic source-period packets). The function independently compares the level and period high/low, period dates, original IDs, dataset and dated contract.
- `confirmed_swings` are separately typed `ConfirmedSwingLiquidity` objects from *already completed, already confirmed* source pivot events, **not** chart-high labels added after the fact. No candidate is usable without `selected_parent_id` that equals its actual owning parent.
- `known_completed_minutes` is the ordered 1m source history through the decision timestamp. For each level, minute continuity is checked **from that level's knowledge time** and a touched-or-swept level is excluded. Missing scheduled exchange hours require a separately audited calendar bridge; this V1 intentionally rejects the gap instead of accepting a boolean assertion.
- The return object has `execution_allowed=False`, `changes_entry_confirmation=False`, and `level_represents_actual_resting_orders=False` in all cases.

## Test and data status

Run using the dependency file already present on research branch `research/astra6-adaptive-paths-20261028`:

```bash
PYTHONPATH=research/september_2026 python -m unittest -v test_causal_target_fallback_v1.py
```

**42 focused synthetic tests passed** on this version. Tests include canonical zone priority, M1/M5/M15 swing target fallback, long/short directions, PDH/PDL/PWH/PWL/PMH/PML, 3–8 a.m. ET and DST, missing minutes, incorrect contract, future-known pivots, wrong parent, touched/swept levels, stop preservation and next-open rejection.

**Historical result limit:** the previously analyzed archived local cohort contains **4,153 return observations** and **3,298 no-target observations**, but its saved event stream is too sparse to reconstruct authenticated complete session liquidity and backtest these target alternatives. The separate QuantConnect **3,069-return native cohort with 2,054 no-target observations** also lacks the full saved decision/market ledger accessible from this session. **Neither cohort has been backtested with this fallback; no number of recovered trades or profit improvement is claimed.**

This version is ready for review and *a controlled, new research-only historical comparison* once the underlying period and source-minute data are made available. It has not been copied into, compiled in, or run on the QuantConnect clone.