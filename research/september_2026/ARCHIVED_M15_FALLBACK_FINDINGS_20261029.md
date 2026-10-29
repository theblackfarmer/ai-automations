# ASTRA 6 — 15-minute liquidity fallback: archived decision-time screen

**Status:** Completed *read-only historical screen* of an archived local research run (2023–2025). **No target substituted into a live or simulated order. No performance claim. Not the separate QuantConnect native 3,069-return cohort.**

## Verified source / method

- Replayed `source/astra_refinement_work/full_history_target_close/transitions.jsonl.gz` inside the existing `ASTRA_research_package_20261028.zip`, using each `CANDIDATE_RETURN` event and the event's **own** frozen `context.liquidity_map`. No future prices or outcomes inspected.
- Expected original totals were asserted, not assumed: **4,111** return observations, **828** with original opposing supply/demand-zone targets, **3,283** without.
- Retained only relevant directional level groups **strictly ahead** of the return's frozen entry reference, recorded `NOT_CROSSED_SINCE_ENROLLMENT`, available by the decision, with fully observed reference tracking from **enrollment** through the decision. Missing/invalid event clocks or groups were excluded.
- Required the original selected parent's identity and contact lineage for classifying **1m + 5m structure alignment**; did not infer liquidity reference parent ownership from the entry's parent.
- Group timeframes can overlap; one price level may be represented by several source timeframes. No count of targets is a count of independent trades.

## Historical results (local research, no fills)

| Recorded condition | Return observations | Distinct candidates |
|---|---:|---:|
| Original zone target missing | 3,283 | — |
| Missing original zone target; both 1m and 5m structure aligned | **691** | **115** |
| Above group with an observed-not-crossed-ahead 15m liquidity proxy | **407** | **75** |
| Above group with 15m proxy but **no** 1m or 5m proxy in the recorded map | **36** | **19** |
| Above group with a *nearest* proxy that includes a 15m source | **51** | — |

Across **all** 3,283 missing-target return observations, an ahead-of-price level observed not crossed since its enrollment appeared in **3,261** observations; 15m was one of its timeframes in **2,607**. **These are research-only upper-bound candidates—not authenticated unused liquidity or executable targets.** All retained group observations had `pre_enrollment_freshness=UNKNOWN` (the 15,556 count of repeated memberships is *not* a count of unique liquidity pools).

## Two chronological source examples (unverified targets)

- **Short, 2023-03-13 09:34 UTC.** Existing decision reference **14,526.50**, no canonical opposing-zone target. A recorded **15m swing-low proxy** lay at **14,452.25**, 74.25 points below. No corresponding 1m or 5m not-crossed-ahead proxy was present in the decision map. Its selected-parent ownership and pre-enrollment freshness were **not** authenticated.
- **Long, 2024-01-23 12:03 UTC.** Existing reference **19,412.25**, no canonical opposing-zone target. A recorded **15m swing-high proxy** was **19,428.50**, 16.25 points above. Again there was no corresponding 1m/5m not-crossed-ahead proxy. It is not an approved TP or trade.

## What we cannot infer

- **Native 2,054:** The full event-level history of QuantConnect's distinct **3,069-return / 2,054-target-missing** native run is not recovered by this local archive. These local counts cannot be projected onto it.
- **Parent and liquidity:** The original observer records carry pivot identities and detection clocks, but not independently authenticated selected-parent *ownership*. All recorded ahead-of-price reference pools have unknown pre-enrollment freshness. Strictly touching a level may also invalidate a proposed fresh-liquidity target even without a strict crossing.
- **PDH/PDL, PWH/PWL, prior month, 3–8am NY premarket:** The saved event ledger lacks source-complete daily/weekly/monthly session candles and **no** complete 300-minute 3–8am NY premarket window was preserved. Therefore none of these session levels were fabricated or counted in this historical screen.
- **Execution:** There is no comparison of future fills, realistic spreads, exchange fees or stop/target ordering. None of the above observations is a newly accepted trade.

## Reproduce

```bash
python -m unittest -v test_astra_archived_liquidity_target_screen.py
python astra_archived_liquidity_target_screen.py \
  --archive /path/to/ASTRA_research_package_20261028.zip \
  --output /path/to/ASTRA6_archived_m15_liquidity_fallback_screen_v2.json
```

Ten focused causal source-snapshot tests **passed**. The actual archived run was independently reconciled to the frozen return counts. The research module must not be installed as a live QuantConnect target selector; it is a diagnostic only. The previously committed `causal_target_fallback_v1.py` is the separately tested proposed target-selection policy, with **42 synthetic tests passed**. These two files have different purposes and must not be conflated.