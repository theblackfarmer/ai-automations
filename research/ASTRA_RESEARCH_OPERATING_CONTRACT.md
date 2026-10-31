# ASTRA Research Operating Contract — Data, Compute, Evidence, Execution

This framework adopts useful principles from the user-provided QuantGPT infrastructure article without depending on the vendor or asserting its promotional claims.

## 1. Data integrity
- Every dataset carries provider, instrument identity, timezone, bar-close convention, session calendar, coverage, adjustments/roll policy, gaps and SHA-256.
- Record when signals, parent BOS, zones, liquidity, indicators and news became knowable. No future rows.
- Preserve exact baseline code and dataset manifests; never silently substitute NAS100 CFD for NQ futures.

## 2. Hypothesis register
Each experiment includes ID, prior research checked, rationale, mechanism, expected failure cases, baseline, variation, feature availability time, parameters, costs, predeclared success metrics and invalidation conditions.
Deduplicate completed tests before launching.

## 3. Controlled experimentation
- Existing ASTRA, Two Punch and Continuation Runner are immutable control versions.
- Isolate one change per arm; compare on common candidate/opportunity IDs; add combined arms only after component effects are measured.
- Audit fill assumptions, one-position constraints, tick size/value, commissions and slippage.
- Never use results of the evaluation period to choose its parameters. Mark already-inspected periods as development, not untouched holdout.
- Report monthly and regime-stratified P&L, expectancy, profit factor, max drawdown, sample counts, worst-case execution sensitivity, and retained/removed winners and losers.

## 4. Findings system
VALIDATED IMPROVEMENT: data and provenance checks pass; paired reproducible run; prespecified net expectancy and risk objectives improve on a genuinely untouched forward test; sensitivity and concentration acceptable.
TRADE-OFF: measurable improvement to one dimension with material deterioration elsewhere.
NO EDGE: adequately powered controlled evidence fails preregistered hurdle.
INSUFFICIENT EVIDENCE: missing data/access, too few trades, failed provenance, incomplete costs, unreliable fill simulation or no proper holdout.
No label guarantees future profitability.

## 5. Risk and promotion
Research -> unit tests -> deterministic historical replay -> walk-forward -> paper trading -> manual approval for live deployment. Neither an AI response nor this contract can place an order. Preserve far-wick stop, 1m+5m agreement, HTF structure and other accepted ASTRA guards unless explicit independent experiment and authorization supersede them.
No duplicate backtests, no unverified claims of finished runs.

## 6. Known unresolved gaps as of 2026-10-31
QuantConnect remote API blocked without dedicated Assistant node. NQ 1-minute history identified in prior report but its bytes not accessible in current execution container. Prior 2025 outcome inspections mean it is not a virgin holdout. Paired-source digest/reporting gate unresolved in prior QuantConnect exit comparison. QuantJourney's 50 examples require adaptation and cannot be characterized as 50 tested ASTRA improvements.

## Immediate priority
Recover exact historical data and original trade ledgers; reconcile baseline; classify every 2023-2024 losing month; run causal regime shadow comparisons; measure winner attrition, especially the 2025-11-25 large winner.
