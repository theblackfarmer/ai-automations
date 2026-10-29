# ASTRA 6 — Adaptive paths and causal liquidity landmarks (research candidate v1)

**Status: self-contained research candidate; NOT integrated into the QuantConnect clone, NOT order-executable.** The existing responsible-zone-return strategy stays unchanged and retains its mandatory confirmed **1m AND 5m** structure, higher-timeframe parent, exact selected-origin far-wick stop, and first relevant opposing-zone exit. This candidate implements a **separate** path, not a silent exception to those gates. No trades, profit evidence, or historical coverage are asserted. `execution_allowed=False` for every decision.

## Why this exists

The native observed baseline recorded 3,069 return observations, 2,054 without a relevant opposing target. That is **not** 2,054 failed trades; it is a set of structural states to study. Earlier local target catalogs showed many invalidated zones. A separate 2026-05 A/B test of forcing a later 5-minute correction break eliminated some early losing probes but did **not** produce a new qualified, profitable later entry. A universal 'wait for second return' rule is unsupported.

ASTRA needs to discriminate **no opportunity**, **pullback return**, **parent-aligned continuation beyond a known obstacle**, and **new trend after valid parent invalidation**. The prototype keeps all of them distinct so their incremental performance can be audited rather than presumed.

## Proposed policy paths

| Path | Structure condition | Entry timing | Lower TF gate | Exit objective |
|---|---|---|---|---|
| A: Responsible-zone return (existing) | Parent contact → confirmed correction/change → resumption → later responsible-zone return | Existing validated entry mechanism | Mandatory structural 1m **and** 5m agreement | First relevant opposing supply/demand zone |
| B: Continuation-through-zone (new research) | Selected parent still active; separately authenticated correction and selected opposite-color origin; **new** 15m or 1h protected-structure BOS close in parent direction; if an opposing zone is supplied, its **far edge** must also be exceeded on that completed close | Strictly subsequent **next-minute open**; no return or 1m+5m alignment gate added | 1m/5m structures recorded, **not reused as mandatory gates** in this distinct research policy | Nearest complete, decision-known, **not previously crossed** directional liquidity level ahead, as proxy only. No objective ⇒ watch/no trade. |
| C: Established reversal (new research) | Old controlling parent explicitly invalidated **and a new controlling parent separately confirmed** before the 15m/1h structure event; then same correction + protected BOS requirements as B | Subsequent next-minute open | Do **not** infer new major trend from a 1m/5m break alone | Same explicit candidate objective and risk checks as B. |

**Important:** merely crossing a supply/demand zone, sweeping PDH, or printing a BOS does not make a reversal trade. Correction membership, parent ownership, source lineages and decision-time knowledge must be demonstrated. The original user's last-opposite-color selected candle rule governs any supplied responsible origin; the prototype does not autonomously discover this origin.

## Liquidity landmarks included

The supplied verified-period adapter accepts completed high/low pairs from:

- `PD_ETH` / `PD_RTH`: previous *completed* Globex trading day and separate regular-hours session. These are distinct by design; never silently mix them under `PDH/PDL`.
- `PW_ETH`: previous completed exchange week; exported `PW_ETH_H` / `PW_ETH_L` (PWH/PWL).
- `PM_ETH`: previous completed exchange month; exported `PM_ETH_H` / `PM_ETH_L` (previous-month high/low). **PMH/PML is ambiguous:** use this explicit prefix for previous month, not the 03:00–08:00 premarket.
- `PREMARKET_0300_0800`: **03:00:00 to 07:59:59 America/New_York**, final levels recognized at **08:00 New York time** after all **300** actual one-minute source bars are present; incomplete periods are not eligible as targets. Running highs/lows are provisional and never mislabeled as a completed premarket range. DST handled by `zoneinfo`.
- `OVERNIGHT`, `ASIA`, `LONDON`, `OPENING_RANGE_0930_1000`: extra future-period high/low inputs; the supplying integration must explicitly define period bounds and validate the source calendar. No automatic fabricated windows for these are implemented.

Confirmed swing highs/lows, equal highs/lows, unfilled gaps, unswept session extremes and prior-day closes may be added **only with source-verified causal recognition**, not guessed from later chart appearances. OHLC extremes are **liquidity proxies**, not direct evidence that stops are resting there.

## Hard guards

1. Source readings and all event times are **timezone-aware UTC**. `America/New_York` handles DST for the user-specified 03:00–08:00 window. Do not convert naive clock strings or assume 08:00 ET is always 12:00 UTC.
2. Period high/low can become usable only after its period ends and its recorded `known_at`. Any incomplete/unknown coverage causes ineligibility. A new period cannot overwrite its recorded earlier price. A target candidate must match the setup's exact `dataset_id` and `contract_segment_id` (no mixing continuous-adjusted and raw/front-contract levels without verified mapping).
3. When a completed minute **strictly crosses** a known liquidity price, record `SWEPT_RECLAIM`, `CLOSED_BEYOND` or `CROSSED_CLOSE_EQUAL_UNRESOLVED` based only on completed price evidence. It does **not** identify the intrabar crossing order or actual resting orders. A swept level is not silently reused as a *fresh* target.
4. Unknown unscheduled coverage is **not** retroactive proof that a previous level remained available. In this prototype it blocks the affected old levels; new complete period evidence can create new levels.
5. Break references, selected origin, parent and completed correction must all be known **before** the 15m/1h break begins. A second consecutive close beyond the same reference does not count as a fresh break.
6. Exact stop is the separately selected opposite-color correction origin's **far wick**, supplied with its identity; it is never widened to accommodate momentum. Positive risk and an ahead-of-price target are checked again at the next observed open. No fill is inferred from OHLC next-open geometry.
7. A new supplied and source-audited 15m/1h BOS is the research path B/C **candidate signal**; it is **not** a pretext to reverse the old higher-timeframe parent. The reversal path requires explicit old-parent invalidation **and** new-parent confirmation.
8. The old and new paths must be replayed as **separate strategy policies**, each with chronological one-position limits, contracts/rolls, source-minute coverage, realistic bid/ask and commissions, native OCO event reconciliation, and untouched out-of-sample evaluation. Never optimize paths from the examined losing cohort and treat results as clean OOS.
9. A first relevant opposing zone remains the exit of the *canonical* A policy. The nearest liquidity level target is an unapproved, separately testable B/C exit hypothesis. Do not silently insert it into A or treat it as an executable resting order.

## Current implementation boundaries

`CausalLiquidityBook` and `Premarket0300To0800` build, freeze and status-track known levels from supplied completed observations. `StructureSetup` is a typed input contract for **pre-verified** parent/correction/origin/BOS events. `evaluate_break` emits a research `BREAK_CONFIRMED_AWAITING_OPEN` with frozen stop, target, and explicit 1m/5m evidence; `evaluate_next_open` checks exact next-minute geometry, never emits executable permission. The missing engineering task is a real native adapter to source-authoritative QuantConnect structure and calendar events. Test fixtures do not constitute such an adapter.

## Validated locally

`python -m unittest -v test_adaptive_market_paths_v1.py`

27 synthetic software checks pass after the source-continuity correction. No historical market replay, new QuantConnect backtest, native order, cloud compile, paid node or production deployment has been run as part of this candidate. The QuantConnect MCP currently rejects full file reads/backtests unless an A1-1/A4-12/A16-32 assistant node is available.
**Correction:** a prior supplied close must be timestamped at the current break start; old-parent invalidation must not follow new-parent confirmation. Minute price updates and premarket sealing must match the referenced dataset and contract segment. Without a complete supplied per-period native minute prefix, liquidity targets remain unknown. These software checks are not independent authentication of the supplied bars or an autonomous detector.
