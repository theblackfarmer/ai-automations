# ASTRA 6 — QuantConnect clone native target-funnel integration handoff
**29 October 2026 | DRAFT / RESEARCH-ONLY | NOT DEPLOYED**

## Scope and ownership
Apply, compile and audit a **passive target-inventory observer** in QuantConnect **CLONE 38584738 only**, after confirming no other assistant is editing that clone. Do not edit original project **38573075**, reference project **38564637**, or GitHub's strategy sources. No orders, backtests, live deployment, paid upgrade, ObjectStore-write workaround, hidden export, strategy-policy change, or source-feed substitution as part of this handoff. **Compile only**. Report before proposing a backtest. Preserve the existing PR #6 unmerged.

Reuse existing tested observer [native_funnel_capture_v1.py](./native_funnel_capture_v1.py) at branch `research/astra6-adaptive-paths-20261028`. Existing observer is source-classification and aggregate census logic: `capture_return`, `NativeFunnelCensus`, `compact_receipt`. Do not reinvent the selector or treat its statuses as entries.

## Preflight / immutable checks
Read the CURRENT clone before writing. 25 source files were present in the prior inspection, so avoid a 26th file. The prior `read_project` source SHA-256 identities were:
- `research_pretrade_context.py`: `002a28363298ebaf5f8f4ee84d8b1c460764fca17769d8f28ab32fb8ce075384`
- `native_signal_session.py`: `9f128151bc4d669429f6b6ca1830e23b7ea6e75472d93279fcd0541d222f815e`
- `native_reporting.py`: `072b4569d24812fbb30f04ebff35f478f0d70ade281a97b26d5c11efd38294a2`
- `main.py`: `ff4d716b9c6d1fe7448270c4d173009981f9d8a1440aeb552e9c6236d5e8d769`

**Stop and reconcile if current bytes differ**, if the observer is already installed, or if another editor owns the clone. The original `SOURCE_MODULE_SHA256` table already has stale entries for main, session and reporter; never claim the old manifest authenticated the current source. Determine real hashes from unchanged actual source bytes, and generate accurate new hashes after edits.

## Source-only patch specification
1. `research_pretrade_context.py`: retain its existing `ResearchPretradeContext` class and imports exactly; append the complete existing `native_funnel_capture_v1.py` research observer to this file with marker `ASTRA_NATIVE_FUNNEL_PATCH_V1`. This avoids adding an extra QC source slot. Ensure no import cycles, syntax error, or module import side effect. Previous estimated final size: 13,509 chars.

2. `native_signal_session.py`:
   - Add `from research_pretrade_context import capture_return` alongside imports.
   - Extend `NativeSignalSession.__init__` with trailing optional `funnel=None`; assign `self.funnel = funnel`.
   - Inside `process_bar`, ONLY in the existing bound `CANDIDATE_RETURN` branch, immediately after `context = event["context"]` and BEFORE the original `self.bridge.evaluate_return`, call:
```python
if self.funnel is not None:
    self.funnel.observe(capture_return(
        source_namespace=self.namespace_sha256,
        event=event, context=context,
        packet=self.pipeline.candidates[cid]["packet"],
        catalog=self.pipeline.zones))
```
   Do not mutate `event`, `packet`, `context`, `catalog` or any normal trading decision. If provenance/parity fails, FAIL CLOSED; never manufacture a target. Previous estimated final size 24,159 chars.

3. `main.py`: add `from research_pretrade_context import NativeFunnelCensus`; initialize `self._funnel = NativeFunnelCensus()` exactly once in `initialize`, next to `self._sessions = []`. Pass `funnel=self._funnel` to each `NativeSignalSession` instantiated in `_change_contract` without replacing other args. This shares a collector across contract rolls. Previous estimated final size 17,612 chars.

4. `native_reporting.py`: do not disturb original native trade, fee, result, census, artifact or diagnostic output. AFTER existing `self._log_return_diagnostics_export()` in `on_end_of_algorithm`, compute `receipt = self._funnel.compact_receipt()` with **no expected 3,069/2,054 count asserted until exact development cohort and source proven**. Compute:
```python
unbound = sum(s.counts.get("RETURN_WITHOUT_BOUND_CHAIN", 0) for s in self._sessions)
parity = (receipt["observations"] + unbound == census["returns_total"]
          and receipt["with_target"] == census["returns_with_target"])
```
   Surface runtime statistics for parity, count of observed and unbound returns, stage totals `NONE/INVALID/BEHIND/TARGET`, aligned 1m+5m by stage, distinct and first-return-missing candidates, transcript SHA-256 and `Full Rows Exported = 0`. Optional `ASTRA_NATIVE_FUNNEL_V1` compact log ONLY after prior existing logs; disclose whether budget declined it. **An aggregate receipt is not the 3,069 full row ledger**. Previous estimated final size 29,285 chars.

5. `native_configuration.py`: update only the `SOURCE_MODULE_SHA256` entries for the four actually changed modules and the `NATIVE_WRAPPER_SHA256` value for updated `main.py`; do not alter normal `NATIVE_CONFIGURATION`, `BASELINE_SIGNAL_CONFIGURATION`, exit policy, target invalidation, stop, entry criteria, order handling, fees, capital, or research windows. Preserve old-vs-new digest audit.

## Required checks and report
- Python parser/import validation, source-bounded catalog/clock/contract tests and **same source catalog canonical target selector parity** must pass before any cloud compile.
- `NativeSignalSession` still requires both **1m AND 5m** structure agreement and exact original physical opposite-color far-wick stop. The canonical first opposing-zone target always wins; fallback levels remain **non-executable**.
- Every changed file must stay under QC 32,000-byte/character limit; no extra source file slot.
- Compile the research clone **once** when available. Do not start a backtest without separately validating source and output retention.
- The clone editor currently shows `exit_policy=structure_exit` and `research_window=q1_2023`. For any later **development baseline** run, explicitly set `exit_policy=fixed_exit`, `research_window=development`, `target_invalidation=close`. Q1 counts must never be compared against the 3,069-observation development census.
- The full native 3,069-row ledger and matching authenticated dated-contract NQ 1m feed are still **unavailable** in the Free configuration; aggregate metrics and synthetic tests are NOT historical validation or P&L.

**Return:** files and SHA before/after; exact edits; compilation success/error; parity/guard outcome; explicit `backtest_started=false`, `orders_created=false`, `original_project_unchanged=true`. If blocked, report exact blocker and stop. No duplicate work or run.
