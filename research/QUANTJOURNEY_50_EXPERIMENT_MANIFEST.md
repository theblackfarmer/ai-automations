# ASTRA 6 x QuantJourney 50: experiment manifest (2026-10-31)

STATUS: DESIGN ONLY. No new NQ/ES historical simulations completed by this manifest.

## Verified source
QuantJourneyOrg/quantjourney-bt/strategies/README.md: 25 weight strategies (W01-W25), 20 order strategies (O01-O20), and 5 validation workflows (WF01-WF05).

## Baselines: do not overwrite
1. ASTRA 6 ICC V2 / current vetted QuantConnect clone; preserve 15m ownership, mandatory 1m+5m confirmation, S/D far-wick stops, causal targets.
2. Continuation Runner.
3. Two Punch.
Treat any strategy not available in versioned source + historical trade ledger as UNAVAILABLE, not 0 P&L.

## 150-slot matrix, with correct semantics
For each of three baselines, evaluate W01-W25 as overlay/proxy features, O01-O20 as independently implemented candidate entry/execution policies, and WF01-WF05 as VALIDATION METHODS rather than alpha-generating strategies. Thus 135 alpha-related comparisons and 15 workflow evaluations; do not misrepresent all 150 as equivalent strategy combinations.

## Required outcomes
- Year/month and regime of each candidate and each executed trade
- Entry timestamp, knowledge-time of every feature, structural parent ID, target provenance, initial stop, decision latency
- Per-arm filled orders and explicit unfilled/untested/unavailable states
- Gross points/R, tick value, commission, slippage assumptions, net dollars/R, drawdown, profit factor
- Winner/loser attrition: removed losers, removed winners, moved entries, outlier sensitivity
- Separate range/trend/transition metrics and trade-availability coverage
- Paired tests by original opportunity ID, not unmatched gross trade counts

## Study design
Development 2023-2024 is exploratory. 2025 is holdout only if not already consulted for tuning; prior analyses have inspected 2025, so a new strictly untouched period or nested forward windows are needed for independent final claims. Never leak post-decision data into regime classification. Optimize only on prior data, freeze thresholds before forward testing; use per-fold refits rather than slice diagnostics where fitting is involved. Reconcile original baseline replay exactly before experiments.

## Source availability blockers
- QuantConnect connector returns dedicated Assistant node required for list_backtests/read_project.
- NQ CSV (Dataset_NQ_1min_2022_2025.csv) referenced by prior ASTRA report, but bytes are not attached to this turn.
- ASTRA_entry_stop_exit_report.html is available in ChatGPT Library; development results and source-audit issues are documented, but are not a substitute for the historical 1m candles and executable runners.
- Hosted QuantJourney requests require QJ_API_KEY and account entitlement; the sample-data demo does not establish historical profitability.

## Immediate forensic safeguards from existing ASTRA report
- Preserve a 2025-11-25 winner that made $16,825.70 fixed-exit net vs $680.70 structure-exit net; diagnose trimmed-winner losses.
- Audit entry-minute and next-minute stops, not merely directional win rate.
- Never characterize a filtered-out winner as a saved loss.
- Keep QC original project unchanged. Build an isolated branch/clone; do not launch live deployments.

## Acceptance
Publish per-month baseline and arm comparison only after the input hashes, exact baselines, no-lookahead checks, costs, and coverage gates pass. If an arm can't run, label BLOCKED with cause; don't invent zeros or simulated performance.
