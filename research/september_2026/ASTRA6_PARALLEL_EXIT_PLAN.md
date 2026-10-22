# ASTRA 6 — parallel one-month development plan

Status: research only. No execution clearance or profit claim.

## Dataset separation
- August 2026 is the frozen 4H/1H context warm-up for the September 2026 study.
- September 2026 is development/research, not out-of-sample validation.
- A single month can exercise the engine and prototype exits, but cannot prove profitability.
- Do not replace the frozen August warm-up with an invented or look-ahead context.
- Keep the existing two-month acquisition job unchanged. Its in-progress filesystem is inaccessible until artifacts are published.

## Exit experiment (pre-registered; no outcome-driven selection)
Evaluate on every causally eligible event, including no-return, failed and unresolved events:
1. Fixed initial risk multiples: 1R, 1.5R, 2R, 3R.
2. Frozen causally known opposing-zone target.
3. Partial at 1R plus runner with a previously defined trailing rule.
4. Time-stop at 15, 30, 60 completed 1M bars.

Record entry/stop eligibility, bid/ask execution assumptions, spread, commissions,
slippage, intrabar stop/target ambiguity, MAE, MFE, realized R, exposure, and drawdown.
Never substitute future swing endpoints for known-at-decision targets.
If tick coverage or fill ordering is insufficient, mark the trade unresolved,
not a win or loss. Evaluate risk-adjusted return and drawdown, not just gross profit.

## Validation gates
- Synthetic fixtures and causality tests must pass.
- Exact source and coverage receipts required for any reported backtest.
- Keep all exit candidates, including losing ones, in the audit trail.
- Freeze selection before evaluating a separate unseen period.
- No live execution without independent risk and order-handling review.
