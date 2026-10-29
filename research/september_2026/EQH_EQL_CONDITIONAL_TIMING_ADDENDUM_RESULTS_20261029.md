# ASTRA 6 — EQH/EQL conditional entry timing: independent addendum result
**Date:** 2026-10-29. **Verdict:** NOT READY for adoption. The incremental historical entry-timing benefit is **NOT PROVEN**. No deployment, cloud backtest, native file edit, purchase or order authorized.

## User's exact policy
EQH/EQL sweep is **OPTIONAL**, not a mandatory condition on every trade. Keep canonical HTF parent, correction/BOS, later responsible-zone return, **both M1 and M5** confirmation, source-origin far-wick stop and canonical opposing-zone-first target. Normal valid continuation entries do **not** wait for a sweep. When independently source-known EQH/EQL rests in a relevant correction area, evaluate whether taking/reclaiming that liquidity followed by a **later** protected direction BOS improves entry timing, not whether it is visually attractive after the outcome.

Proposed decision-time research states: `NORMAL_ENTRY`, `WATCH_LIQUIDITY` (no automatic baseline veto), `OPTIONAL_SWEEP_CONFIRMATION`, distinct `CONTINUATION_BREAKOUT` (long through EQH or short through EQL with no reclaim and separately confirmed parent-direction BOS), `CONFIRMED_REVERSAL_RESEARCH` requiring an independently new parent, and `NO_SWEEP_SIGNAL/UNRESOLVED`. A strict raid/reclaim candle is evidence, **not entry authorization**; no intraminute high/low sequencing is inferred. A swept pool may no longer count as fresh target-side liquidity. Preserve target mandatory and same contract/source/time.

## Source inspection, not assumed semantics
Read-only QuantConnect clone `38584738` inspection: `correction_swing_tracker_v1.py` labels `EQH/EQL` by an equal comparator on locally confirmed pivots, but outside-bar ambiguity can be flagged `LOCAL_LEG_EXTREME_ONLY`. `engine.py`'s `EQUAL_EXTREMUM` is not a new independently confirmed swing. `sweep_observer.py` monitors retained 1m/5m pivot price breaches/reclaims, expressly marks `pre_registration_breach_history=UNASSESSED` and `actual_liquidity_authenticated=False`; it does not supply a linked prior-known **two-pivot equal pool → breach → later reclaim → later protected BOS** trade record. `decision_sweep_evidence.py` explicitly treats sweep as optional/nonmandatory.

## Completed bounded independent software audit
Created a separate, **non-executing local prototype** and 46 synthetic/deterministic unit tests: **46/46 passed**. It tests two separate source-known pivots with frozen NQ 0.25 tick/exact equality, same parent/dated contract, exact minute continuity from pool confirmation, strict raid, separate later reclaim, later protected source-bar BOS, source-clock/parent/contract/bars failures, same-bar and dual-side ambiguity, stale confirmation, unchanged canonical permission, far-wick geometry, correct-direction continuation, and fresh EQH/EQL target-side candidate checks. It has `execution_allowed=False` throughout. Research-only illustrative parameters (not optimized): pool proximity <= 1 original-stop R; signal age <= 60 minutes; neither has been adopted.

Read-only screen of the **19 archived local distinct candidates** from the previously completed parent review, without reprocessing the large gzip archive:
- Original economic-entry-eligible: **0 / 19** (all were previously target-vetoed).
- Verified **two independent prior-known EQ pivots** in decision packets: **0 source-verifiable / 19**; no valid sweep/reclaim/BOS trade can be authenticated.
- Complete 120-minute predecision source window: **1 / 19**; median **34 / 120** saved bars.
- Authenticated A-versus-conditional-C matched entries, fills, missed winners and net R: **0 available**.
- **This is missing-evidence failure, not proof zero actual market sweeps occurred.**
Original native **3,069 return observations / 2,054 missing target** and local **4,111 return observations** are DIFFERENT populations; no substitution, reclassified observations, or fabricated recovery P&L.

## Required true economic comparison
On a source-matched native *originally eligible-entry* population: **A** unchanged canonical; **B** optional known-at annotations, no economic change; **C** only precommitted contexts, delayed strict raid → later reclaim → later direction BOS, counting every missed original winner and avoided loser; **D** separately validated continuation. Measure causal entry delays/prices, source stop and target, MAE/MFE, early stop-outs, actual net R after fees/slippage, portfolio occupancy, drawdown, and untouched chronological OOS. Do not select post-outcome winners, move the far-wick stop, or bypass canonical gates.

## Exact blocker / next source action
The independent parent Full Validation completed software checks, **not** the native historical backtest. Its original decision-level 3,069-row ledger and complete authenticated matching NQ dated-contract one-minute bars are unavailable; QuantConnect `read_file` and `list_backtests` were reported gated by a provider **dedicated paid Assistant node**. Preserve original projects `38573075` and clone `38584738`. **Next unblock:** inspect preserved QuantConnect backtest snapshot `38595614` via GUI and export all original per-return packets plus genuine NQ 1m/minute coverage, prior pair pivot/source IDs, first-breach/reclaim clocks, protected BOS evidence, exact stop/target, orders/fills/costs. If only aggregate counters remain, use a reporting-only research instrument rather than inventing missing data. Retest the frozen variants chronologically, then untouched OOS.

**Artifacts:** independent report PDF + source/checks/tests + 19-row audit CSV/JSON delivered with the associated ASTRA 6 Sweep Timing ChatGPT task. Test cases are synthetic, not historical profitable examples.

**STATUS:** conditional research logic tested; **no verified economic improvement and no live readiness**. Draft PR only; do not merge this addendum as a production strategy.
