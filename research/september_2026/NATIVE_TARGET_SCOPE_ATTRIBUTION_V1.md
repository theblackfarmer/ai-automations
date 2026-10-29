# ASTRA 6 — Native Target Scope Attribution, source-based diagnostic v1

**Research status: software-only, read-only. Not an executable target selector, not a historical performance test, and not a change to QuantConnect.**

## Evidence recovered and source boundaries

- Preserved QuantConnect native v2 fixed-exit runtime result: **3,069 return observations**; **1,015 with target**; **2,054 without target**; 532 bound candidates, 93 eligible contexts, 23 typed order intents and 16 actual simulated entries. These are different counting units and not a 2,054-trade loss population. Source: `source/astra_structure_exit_work/native_results/development_fixed_exit/decoded_run_audit.json` in `ASTRA_research_package_20261028.zip`, SHA-256 `aec78629a5bb0ff373e8d0b7e210c71505be4a5c5deb6d497f131d84e2e3b887`.
- **Do not import** the `103` earlier primary target-missing cases into the later run. They belong to a separate, older native 549-return zero-intent trial. Source: `source/astra_integrated_work/native/cloud_run_receipt.json`, SHA-256 `338d628b35850f447e7771dc19f546cbb24834e0590396fafc10a5620397471c`.
- Archived native selector source: `source/astra_structure_exit_work/plain_upload/market_primitives.py`, SHA-256 `40051e0a756cdb7f55fef852167f6dc9b5ed35810cab6889eb2e3a041816f1be`, lines 73–89. It considers all known `target_valid` opposing zones wholly ahead of max/min(proximal, last close), ranks nearest proximal edge, then longer timeframe, newer knowledge time, and zone ID. It **does not** validate relationship to selected parent, selected ancestor or independent liquidity relevance.
- The existing zone lifetime `target_valid` is separate from `valid`; its close-vs-farwick mode is in `market_pipeline.py` lines 142–177. This study does not change that distinction or the selected entry far-wick stop.
- The archived 4,153-return **local** research cohort has complete emitted return-event and target-catalog trace but is *not* the native 3,069 cohort. Do not transfer its 3,148 invalid-opposing-zone counts into the native census.

## What this executable utility does

`native_target_scope_probe_v1.py` takes **actual decision-time catalog snapshots** and recreates the canonical selector's geometric ranking without changing entries, exits or stops. It classifies absent targets into mutually exclusive source-inventory stages:

1. `NO_OPPOSING_ZONE_KNOWN`
2. `ALL_KNOWN_OPPOSING_INVALID`
3. `VALID_OPPOSING_NOT_AHEAD`
4. `TARGET_AVAILABLE`

It separately records a **shadow, nonexecuting** relevance comparison only for targets whose parent/scope relationship is independently sourced and already known. Unverified owner/relevance is `UNKNOWN_RELEVANCE`, never automatically rejected or promoted into a trade. If actual native selector IDs and reconstructed IDs disagree, the entire record is rejected as invalid. It rejects missing decision-time validity snapshots and fails the 3,069/2,054 population check if records are incomplete.

### Verified mechanical finding

The native selector can choose a nearer opposing zone associated with another context ahead of a farther, source-identified relevant zone, because it has no scope test. A synthetic two-zone example demonstrates this behavior. **This does not establish that any real historical zone was misclassified or profitable.** The new shadow comparison does not establish an accepted replacement target.

## Verification

- **20/20** synthetic unit tests passed (`python -m unittest -v test_native_target_scope_probe_v1.py`).
- **2,500/2,500** seeded synthetic decision snapshots matched the exact archived native `MarketContextHooks._target` target ID, with no divergence. This separately verifies selector replication, not live code or market outcomes. The parity script requires the actual archived market_primitives.py explicitly; it is not a standalone QuantConnect replacement.
- Both QuantConnect project IDs `38584738` (clone) and `38573075` (original) remained unchanged; zero cloud backtests, compilations, trading orders or purchases were launched by this work.

## Next real-data step (blocked, not guessed)

The clone's deleted `funnel_diagnosis_v1.py` was retained by historical backtest **snapshot ID 38595614**. Retrieve it through the supported QuantConnect backtest **Code → Clone Algorithm** UI; it is not recoverable through the currently blocked MCP source-read endpoint. Before any new run, read its snapshot source and inspect existing exports. Recover a 3,069-row *decision-time* catalog including invalid zones, status as-of each decision, and chronology. Classify the authentic 2,054 no-target observations and group repeated candidate returns. Check 1m and 5m confirmation and controlling parent separately; do not silently add PDH/PDL, PWH/PWL, previous-month or 03:00–08:00 premarket liquidity proxies as executable exit targets. If the old outputs lack enough snapshots, propose **one limited reporting-only change** within the research clone and freeze the audit before a necessary new diagnostic backtest; do not duplicate an existing run.

### Input format for stand-alone analysis

One JSONL item containing `{ "decision": { ... }, "catalog": [ ... ] }` per return. See `sample_decision.jsonl` for a **synthetic** example, not native data. All source catalog entries need `status_asof` exactly equal to the entry decision. Strict source provenance must be attached before calling any `scope` value verified.

Run:

```bash
python native_target_scope_probe_v1.py \
  --decision-snapshots actual_native_decision_catalog.jsonl \
  --output native_target_scope_diagnostic.json \
  --expected-total 3069 --expected-missing 2054
```

Without the full authentic 3,069-row packet, it deliberately raises an error rather than inventing missing-target attribution or P&L.