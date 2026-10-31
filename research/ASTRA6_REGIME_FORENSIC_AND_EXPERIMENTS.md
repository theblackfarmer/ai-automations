# ASTRA 6 regime forensic and causal experiments

## Scope and reproducibility

This is a research-only follow-up to [Actions run #41252039235](https://github.com/theblackfarmer/ai-automations/actions/runs/41252039235). The run's uploaded artifacts remain unchanged. The new comparison writes separate `astra_regime_experiment_summary.csv` and `astra_regime_experiment_setups.csv` artifacts and checks that its `as_run_reference` still reproduces 72,454 setups, 156 aligned, 1,763 unmatched (`UNKNOWN`), and 64,759 `WARMUP`.

The minute source has SHA-256 `1577e60a7feab411e49da7a56c7052a64738cd1757cfd60aa11fd783ff43b60b`. The experiments use that same source and frozen discovery setup IDs. They classify candidates only; they do not simulate fills, calculate trade P&L, or place orders.

## What caused the low aligned count

The run's 72,454 candidate records break down as follows:

| Classification | Candidates |
|---|---:|
| WARMUP | 64,759 |
| UNKNOWN (no causal match within 15 minutes) | 1,763 |
| TRANSITION | 5,214 |
| RANGE | 404 |
| TREND_UP | 297 |
| TREND_DOWN | 17 |

The classifier requires `vol_lookback + lookback + 1 = 81` completed 15-minute bars before it emits a label: 20 bars for current ATR and directional efficiency plus 60 prior true ranges for the volatility baseline. Directional efficiency is `abs(close[t] - close[t-20]) / sum(abs(close change))` over 20 15-minute changes. The unchanged thresholds are trend `>= 0.38`, range `<= 0.20`, and volatility expansion `current 20-bar mean true range / prior 60-bar median true range > 1.8`.

The existing replay resets the classifier after *any* missing 15-minute bucket. The source has ordinary roughly 61-minute daily NQ session breaks; those resets repeatedly restart an 81-bar warmup (20 hours 15 minutes of trading bars). That recurring warmup, not a threshold that was simply too high, accounts for 89.4% of all candidates. The original implementation also gives volatility expansion priority over direction, so a high-volatility, otherwise efficient move is labeled `TRANSITION`.

Only 314 candidates were assigned a directional trend label in the original run; 149 long setups aligned with `TREND_UP`, 7 short setups aligned with `TREND_DOWN`, and 158 had the opposite direction. Thus the 156 aligned count is a combination of low regime coverage, a strong up/down imbalance in the labeled trends, and strict directional agreement—not evidence that the underlying entry logic only generated 156 opportunities.

## Candle timing and missing data

The source column is named `timestamp ET`, but the source does not document whether its minute timestamps mark bar opens or closes. The original `floor(timestamp, 15m) + 15m` grouping assumes open-stamped rows. If rows are close-stamped, a candle should end at `ceil(timestamp, 15m)`; for example, the consecutive rows `18:01` through `18:15` form a candle ending at `18:15`, not `18:30`. Under the tested conventions, the source yields 69,012 complete open-aligned bars versus 69,777 close-aligned bars. This is a material provenance ambiguity, so neither interpretation is silently promoted as the production convention.

The revised research builder requires exactly 15 distinct consecutive minute rows and drops incomplete windows; it never fills missing minutes. It tests both timestamp conventions. The close-stamped arm may use a 15-minute candle at an equal decision timestamp because that candle is complete at the decision time. The open-stamped arm advances the setup's knowledge time by one minute before matching. All matches are backward-only, at most 15 minutes old, and checked against future matches. Unmatched setups remain `UNKNOWN`.

## Isolated experiment results

An accepted setup below means only that its direction matches the assigned regime (`long`/`TREND_UP` or `short`/`TREND_DOWN`). Rejected is the complement and includes unknown, warmup, range, transition, and direction mismatch; the separate columns show availability. Counts are not trades or performance.

| Arm | Accepted | Rejected | Unknown | Warmup |
|---|---:|---:|---:|---:|
| `as_run_reference` | 156 | 72,298 | 1,763 | 64,759 |
| `open_aligned_gap_reset` | 155 | 72,299 | 1,678 | 64,774 |
| `close_aligned_gap_reset` | 170 | 72,284 | 1,238 | 64,621 |
| `close_aligned_all_gap_carry` | 4,554 | 67,900 | 1,238 | 87 |
| `close_aligned_90m_gap_carry` | 3,443 | 69,011 | 1,238 | 14,977 |
| `close_aligned_volatility_overlay` | 6,713 | 65,741 | 1,238 | 87 |
| `close_aligned_displacement_confirmed` | 3,258 | 69,196 | 1,238 | 87 |

The gap-reset arms isolate timestamp interpretation while retaining the original regime definition. The all-gap-carry arm retains the same efficiency and volatility thresholds but does not restart the rolling history after a gap; it still uses only completed candles and refuses stale candidate-to-regime matches. The 90-minute arm preserves context across the observed daily session break but resets after longer gaps, including weekends and longer data holes. Its remaining warmups quantify the cost of that more conservative gap policy. The volatility-overlay arm keeps the `0.38` efficiency hurdle and treats volatility expansion as a separate context rather than vetoing an efficient directional regime. The stricter displacement arm retains the original thresholds and additionally requires absolute 20-bar displacement of at least 4 current ATRs to call a trend.

These are exploratory research alternatives, not selected winners or outcome-tuned parameters. The 90-minute gap limit spans the observed daily break while restarting after longer holes; the 4-ATR hurdle is a stricter sensitivity arm. The increased accepted count from carrying history or treating expansion as an overlay says nothing about net expectancy. The per-setup artifact preserves each arm's regime, feature values, match time, acceptance, and rejection reason for paired follow-up.

## Candidate validity and limits

The frozen setups are not verified executable ICC entries. `astra6_causal_transition_engine.py` discovers one-minute pivot breach/reclaim, ATR repricing, partial correction, and one-minute confirmation patterns. It records a confirmation close and next-minute open but does not apply the original ASTRA ICC 15-minute BOS ownership, mandatory 1-minute plus 5-minute agreement, supply/demand zone validation, far-wick stop, causal target, one-position constraints, or fill/cost simulation. Consequently these candidates cannot establish which real ICC trades were retained or removed, and neither accepted nor rejected counts can establish profitability.

No ASTRA trading rule, stop, target, or execution path was changed. No live-trading workflow was added or enabled. The required next evidence for the stated objective is a reconciled executable ICC trade/opportunity ledger with costs and outcomes, then paired, chronological out-of-sample expectancy and winner/loser attrition analysis. This experiment is coverage diagnostics only.
