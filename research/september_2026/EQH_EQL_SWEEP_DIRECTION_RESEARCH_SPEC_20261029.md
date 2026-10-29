# ASTRA 6 — Equal-High / Equal-Low Liquidity, Sweep and Direction Confirmation
**User instruction:** October 29, 2026. **Status:** required additional research check; **not** production-authorized, not a tested strategy variant and not a historical fill.

## Intent and existing source
The user wants ASTRA to recognize **equal highs (EQH)** and **equal lows (EQL)** as liquidity, observe price **clearly take that liquidity**, then wait for **a separate structural confirmation in the intended trade direction**. This is an enhancement to the research and the liquidity-target inventory, not permission to discard the original entry rules.

Actual QuantConnect research clone `38584738` already contains:
- `correction_swing_tracker_v1.py`: confirmed pivot labels `EQH` / `EQL` when a new extremum exactly equals the predecessor (source's current price comparison; inspect its confirmation timing).
- `engine.py`: emits `EQUAL_EXTREMUM` events, but these are not independently proof of a confirmed prior liquidity pool.
- `sweep_observer.py`: `SWEEP_RECLAIM_OBSERVED` source evidence; must check reference enrollment, causal price timestamps, direction, unknown coverage and parent ownership.
These must **not** be conflated automatically. Build an explicit linked research packet and test it on real data only where support is complete.

## 1. Causal pool recognition
A proposed EQH/EQL pool requires **at least two separately formed and confirmed swing pivots** (not two adjacent candles within the same pivot), source IDs, their price highs or lows, and `known_at` times **strictly prior** to the proposed raid. Freeze an instrument-specific fixed tick-size/tolerance before reviewing outcomes (e.g. NQ tick = 0.25; exact equality or a separately declared one-tick clustering hypothesis). Record both the pool's upper and lower boundary for a nonexact tolerance; do not treat an approximate cluster as a single arbitrary price. Do not backdate the pool to the first pivot. Preserve higher-timeframe and parent linkage, dated-contract namespace, exchange calendar and data-continuity status.

## 2. Raid and rejection vs breakout acceptance
- **Bullish after EQL raid:** price must trade strictly **below the known EQL lower boundary** on a completed source candle, and a reclaim back above the pool boundary must be independently observed before a reversal-style entry is entertained. Then require **subsequent** bullish displacement/protected reference BOS or equivalent specified structural confirmation in line with a valid long controlling parent or newly authenticated long parent.
- **Bearish after EQH raid:** price must trade strictly **above the known EQH upper boundary** and reclaim below the boundary; subsequently require a separate bearish structural break/confirmation aligned with an authorized short parent.
- A mere touch, equal-high/low label, sweep wick or unconfirmed reclaim **is not an entry**. No same-bar guess about the ordering of high, low and close; same-minute double raids can be **AMBIGUOUS** and must not be counted as two ordered signals.
- If price **accepts and closes beyond** the equal pool instead of reclaiming it, classify **BREAKOUT/CONTINUATION**, not a bullish/bearish reversal sweep. Preserve the distinct continuation-entry path with confirmed correction/BOS/retest and parent direction; do not suppress valid trend continuation just because no reclaim occurred.
- Distinguish external vs internal liquidity, prior-day, session and HTF pools, and whether the pool belongs to the selected structural parent. Unknown parent relation is `UNRESOLVED`, not `VALID`.

## 3. EQH/EQL as trade targets
While **prior-known, available and unswept**, EQH above the long entry can be **buy-side target-side liquidity**, and EQL below the short entry can be **sell-side target-side liquidity**. Include them as separate named candidate sources in the existing causal fallback. Canonical **first relevant opposing supply/demand zone** retains priority; only if canonical target is absent compare decision-known, correctly owned and source-complete directional liquidity objectives (EQH/EQL, confirmed M15 swing, PDH/PDL, PWH/PWL, prior-month, complete 03:00–08:00 NY). Choose nearest relevant objective genuinely ahead of entry, not an already swept/consumed or future-confirmed level. Never count a pool just swept by the proposed entry as fresh target liquidity. No execution when valid target unavailable.

## 4. Entry, stop, and evidence
The **original canonical** responsible-zone-return entry still requires confirmed **BOTH 1m and 5m structural agreement** with its selected parent, later source-zone return, exact selected-origin candle far-wick stop and source-authoritative lineage. The EQH/EQL raid is supporting confirmation unless a separately named continuation/reversal entry policy is prospectively frozen and tested. It does not bypass the canonical gate. Follow-up structural confirmation must be knowledge-time valid and causally subsequent to the raid, with parent still active and local correction completed. Do not auto-enter at the raid candle, move the stop to the sweep wick, or choose a target from later price action.

## 5. Required independent validation / report
Freeze and report source-derived cohorts and clear decision-time charts:
1. Confirmed EQH → strict buy-side sweep → reclaim → separate bearish BOS → short opportunity *or rejection*.
2. Confirmed EQL → strict sell-side sweep → reclaim → separate bullish BOS → long opportunity *or rejection*.
3. EQH/EQL **close-through continuation** without reclaim, keep as distinct candidate.
4. Pool known **after** raid (reject for live recognition).
5. Same-bar high/low raids ambiguous, including wick equal to level (not strictly taken).
6. Two pivots within the same swing or different contract series (reject).
7. Unknown missing native minutes/coverage, scheduled closures without proof (unresolved).
8. Wrong-side, stale or already consumed liquidity target (reject).
9. EQH/EQL that is nearer than other eligible liquidity level, and farther than a canonical opposing zone (respect priority).
10. Exact far-wick stop, next-open geometry, fees/slippage, portfolio occupancy, and genuine untouched OOS performance before any profit or deployment verdict.

Report distinct **opportunities** separately from repeated **return observations**, verified directional breaks, and actual fills. Real older local 4,153 or 4,111 return cohorts cannot be substituted for 3,069 native returns / 2,054 native missing targets. No full native profit claim absent actual native ledger.

## Coordination
This is a non-executable research specification attached to ASTRA draft PR #6. A separate independent **ASTRA 6 Full Validation** task is already running: preserve its task ownership, avoid duplicate work and merge conflicts. Do not change original QuantConnect projects `38573075` or `38584738`, and do not submit live orders or purchase Assistant nodes.
