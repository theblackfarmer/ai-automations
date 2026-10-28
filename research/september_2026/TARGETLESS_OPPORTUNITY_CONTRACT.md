# ASTRA targetless opportunity audit v1 — source contract

This is a **reporting-only diagnostic**. It does not change the selected ASTRA strategy, admit a trade, generate a stop/target, execute an order, or label a setup profitable. Its purpose is to investigate the 2,054 missing-target observations in the 3,069-observation native baseline (and any separately identified source cohort). The actual event-level ledger is required before population conclusions can be made. Do not substitute earlier local 4,153-return results for the native cohort.

## Input

One JSON object per line. Export **only decision-time state**. Do not include outcomes, next bars, future pivots, final R, or later-selected target prices. All clocks are integer UTC epochs. Every item must be backed by the actual source code/record at its causal availability time.

Required per observation:
- `observation_id`, `parent_id`, `formation_id`: stable original identities; no invented grouping IDs.
- `decision_at`, `side` (`LONG` / `SHORT`), `entry_reference`, `exact_far_wick_stop`.
- `m1_state`, `m5_state`: `ALIGNED`, `OPPOSED`, or `UNKNOWN` **structural** confirmation for the selected parent (not color of the latest bar).
- `parent_state`: `ACTIVE`, `INVALID`, `UNKNOWN`.
- `coverage`: `COMPLETE` or `UNKNOWN`.
- `canonical_target_available`: existing selector's actual boolean (not a reconstructed rule and not a rescued trade).
- `knowledge_times`: a map of known-at timestamps for parent, structure, formation and later responsible-zone return. Null means unavailable.
- `candidate_zones`: catalog **at the decision**, including rejected/consumed/expired entries when logged, with `id`, `kind` (`SUPPLY`/`DEMAND`), `known_at`, `status_known_at`, `state` (`VALID`, `SWEPT_RECLAIM`, `CONSUMED`, `INVALID`, `UNKNOWN`), `owner_relation` (`SELECTED_PARENT`, `SELECTED_ANCESTOR`, `OTHER`, `UNKNOWN`), `timeframe_minutes` (1,5,15,60,240), `low`, `high`, `coverage` (`COMPLETE`/`UNKNOWN`). A status marked `VALID` is not itself audited proof of valid origin, parent, source continuity or liquidity, so output proposals remain non-executable.

Example **synthetic only**, not historical:

```json
{"observation_id":"SYNTHETIC_R1","parent_id":"P1","formation_id":"F1","decision_at":600,"side":"LONG","entry_reference":100.0,"exact_far_wick_stop":96.0,"m1_state":"ALIGNED","m5_state":"ALIGNED","parent_state":"ACTIVE","coverage":"COMPLETE","canonical_target_available":false,"knowledge_times":{"parent":300,"m1":480,"m5":600,"formation":420,"return":600},"candidate_zones":[{"id":"Z1","kind":"SUPPLY","known_at":480,"status_known_at":540,"state":"VALID","owner_relation":"SELECTED_PARENT","timeframe_minutes":15,"low":110.0,"high":113.0,"coverage":"COMPLETE"}]}
```

## Interpretation and safety

1. An absent canonical target stays absent. A valid-looking shadow zone is a **selector-mapping research discrepancy**, not an accepted target or an entry.
2. Zone candidates discovered after the decision, lacking parent relevance, already consumed, geometrically behind, or with incomplete continuity are not proposed.
3. `SWEPT_RECLAIM` can be explored in completed-close lifecycle research but is not promoted to executable without authenticated status and coverage. The original precise far-wick protective stop never changes.
4. Baseline 1m+5m structural agreement remains mandatory. 15m continuation is a separate post-entry consideration.
5. Parent and formation grouping identifies first/later **observations**, not predetermined first/second opportunities. The after-the-fact transition table cannot be used as a prior entry feature.
6. Counts are **not** trade opportunities unless exact source predicates are independently proven. Outcome analysis, if ever authorized, requires an execution/fill model, costs, occupancy and an untouched evaluation window.
7. This audit deliberately refuses to infer an opportunity from a bare OHLC swing proxy or an arbitrary target. A separate named variant would need a full predeclared entry/exit specification to test those hypotheses.

Run `python -m unittest test_targetless_opportunity_audit_v1.py` for synthetic invariants. Run the audit CLI only on actual exported source packets, with `--expected-total 3069 --expected-missing 2054`; an incomplete file is explicitly reported as incomplete, never silently accepted as the population.
