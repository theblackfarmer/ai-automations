# ASTRA 6: archived local targetless-return → later M15 screen (Oct 28, 2026)

**Status:** read-only source reconciliation; no trade admission, historical profit backtest, or change to the established ASTRA strategy. This source is the **4,153-return LOCAL archive**, **not** the separate 3,069-return / 2,054-no-target QuantConnect native run.

## Verified source checks

- Input: `ASTRA_research_package_20261028.zip` stored in the user's research Library.
- All 4,153 source `CANDIDATE_RETURN` records matched the independent catalog trace once by candidate ID, `known_at`, and native minute source ID. Existing selector audit reported 0 mismatches.
- Source member SHA256 (compressed archive members):
  - `source/astra_integrated_work/target_catalog_return_trace.jsonl.gz`: `be720cfc1b23c16388d31c34708a80567e81fd3c39dc4a66bab0f65bda9e5149`
  - `source/astra_integrated_work/full_history_calendar_verified/transitions.jsonl.gz`: `eb8def1cb4c3ee2ad734e12a1c88be7126aaba488fc8138cc8ce40729839962e`
  - `source/astra_integrated_work/TARGET_CATALOG_TRACE_REVIEW.json`: `bedb196433676cbf03b6de17c7ce478b76f666d779a8e6d9414461faa5201031`

## Frozen local census (do not mix with native)

| Field | Count |
|---|---:|
| Return observations | 4,153 |
| No opposing target in original decision | 3,298 |
| Target present in original decision | 855 |
| Distinct candidate IDs | 581 |
| Distinct parent IDs | 32 |
| First-return no-target candidates | 435 |
| First-return no-target then later a target in the retained return stream | 6 |
| First-return no-target, never a target in retained return stream | 429 |
| First-return no-target and original 1m **and** 5m structural proxies aligned | 114 |
| Initially targetless, structurally aligned candidates followed by active same-parent/direction 15m progression **within 1h** | 17 |
| Distinct 15m progression events behind those 17 associations | 16 |
| Same aligned first-return cohort, **within 4h** | 57 candidates / 44 distinct M15 events |
| All first-return no-target candidates followed by active same-parent M15 within 1h | 60 / 51 distinct M15 events |
| Old parent invalidated followed by a different opposite parent selected within 4h | 0 |
| Same parent-transition sequence within 24h (descriptive only) | 4 candidates / 2 distinct transitions |

The 15m events were additionally checked for completed-bar clocks, strict crossing of a previously known structure reference, 15 ordered minute source IDs, active selected parent, and matching direction. **They are research progression events, not source-complete 15-minute breakout entries or verified trades.** A future-known event is never used to revise an earlier return.

## Next validation gate

Restore archived native `funnel_diagnosis_v1.py` via backtest snapshot **38595614** (QuantConnect Code tab / Clone Algorithm). Require the full decision-level native 3,069-row ledger, original 1m NQ bar source, actual parent/correction/origin membership, source continuity, realistic next-minute price fill and stop/target behavior. Keep the canonical mandatory 1m+5m rule and first opposing-zone exit unchanged; the new continuation/reversal routes remain independent, non-executing research candidates.

The 1h/4h/24h intervals are retrospective screening windows, **not optimized signals**. This audit cannot claim 2,054 recovered trades, post-cost expectancy, or live readiness. Synthetic contract checks for the separate standalone archive screen passed 8/8; the archival report and 10 sourced examples are available as a separate conversation attachment. No NQ raw bar export or private historical dataset was committed to GitHub.
