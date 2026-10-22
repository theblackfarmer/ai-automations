# ASTRA 6 quote-based profit research

**Market profitability is not established.** This separate offline layer connects the repaired September signal engine to observed bid/ask quotes. Its tests use explicitly synthetic fixtures. The frozen signal engine and original target contract remain unchanged; no live orders are enabled.

## Current data result

On 2026-10-22, six network-enabled collector attempts across the local runtime and a GitHub-hosted runner returned **zero usable market ticks** (HTTP 503, timeout, or connection reset). One earlier sandbox-blocked request was also preserved. No full-month download was launched after the probes failed.

The hosted evidence is [run 39842141378](https://github.com/theblackfarmer/ai-automations/actions/runs/39842141378), probe commit `525357912415a0bba5b82177bd52f525a3d8021c`. Its artifact ZIP SHA-256 is `4fdb406f37015202358d5279792f4c12c8b67fce53b4f14f5a48b33b0c554909`. This is evidence of those failed requests, not proof of a provider-wide outage. No further retry is scheduled.

## Run

Python 3.9 and 3.11 are supported. The quote layer has no extra third-party dependency; the existing structural report requires the pinned dependency in `research/september_2026/requirements-report.txt`.

From the repository root:

```sh
python -B research/astra6_profitability/verify_profit.py --output /new/path/quote-checks
python -B research/september_2026/verify_all.py --output /new/path/engine-checks
python -B research/astra6_profitability/profit_study.py --acquisition /path/to/MANIFEST.json --output /new/path/profit-study --causal-workers=2
```

Output directories must be new. The last command exits **2** with `BLOCKED_NO_PROFIT_RESULT` when the required acquisition is incomplete. It does not emit a market PnL file for that case. A completed development simulation still sets `profitability_established:false`.

## Required input and audit

The input must contain the exact frozen **Dukascopy USATECHIDXUSD raw BID and ASK** acquisition: August 2026 warmup plus September 2026 study, ending October 1 exclusive. All 1,464 hourly receipts must validate as observed or explicitly empty HTTP-200 bodies. Source hashes, receipt lineage, the original raw bytes, and exact tick-to-BID-minute reconciliation are required. Unknown minutes are not silently treated as closed sessions.

The unchanged native causal pipeline runs before quote simulation. Every native `ENTRY_REVIEW` in the study window is included, with its immutable original target and original local stop geometry. The complete decision cutoff determines permission. Later revocations cancel only when their clocks arrive. The quote reader preserves provider record order, including equal timestamps, and rechecks captured bodies before use.

The signal baseline is commit `e12aa1385b3bb29e0c89f0870aee042321e1e059`; `engine.py` SHA-256 is `b8536b432436b0a994eed9e9f24bffb141b42b6914703d4e35771ad85154e2fb`. `PROFIT_PROTOCOL.json` pins the remaining signal and acquisition inputs. Each output records the protocol and research software hashes. The native replay handoff must match the validated acquisition, CSV, completion receipt, and sealed events.

## Preregistered policies and hypotheses

The policies were registered before any matching market outcome was computed:

| Policy | Exit objective |
| --- | --- |
| `ORIGINAL_TARGET` | Original parent target; original local far-edge stop |
| `ORIGINAL_TARGET_BE_1R` | Original target; move stop to entry after +1R |
| `ORIGINAL_TARGET_CONFIRMED_M1_SWING_TRAIL` | Original target; trail post-entry confirmed M1 swings after +1R |
| `ORIGINAL_TARGET_MAX_60M` | Original target; maximum 60-minute hold |
| `ORIGINAL_TARGET_MAX_240M` | Original target; maximum 240-minute hold |
| `CONTROL_FIXED_1R` | Diagnostic fixed-1R objective |
| `CONTROL_FIXED_2R` | Diagnostic fixed-2R objective |

The two fixed-R controls do not preserve the original target exit rule. All variants retain the original-target admission check. The generic swing trail is a new hypothesis; it is not asserted to be the native BOS-protecting-pivot exit contract. Every policy is reported, with no development winner promoted as an established edge.

Entry uses the first observed ASK for a long or BID for a short strictly after one second of latency, within a 60-second pending window. Original-target exhaustion during latency, stop crossing, permission loss, or expiry cancels the pending entry permanently. Each policy permits one position at a time and no exit-minute reentry.

Stops and targets use a declared **market-if-touched simulation** at the observed liquidation-side crossing quote, with zero additional barrier-exit latency. This is not a broker-fill claim or a reproduction of a default linked take-profit limit order. Stop changes activate only at a strictly later quote timestamp. Quote gaps over 60 seconds while open, missed buffered liquidation, or unresolved end-of-data exposure are censored with null realized PnL; that policy then halts further entries.

The daily flatten trigger is 19:59 UTC, with execution on a later quote before 20:00 and entries resuming at 22:00. Study-end liquidation uses a one-minute buffer. No retrospective last-quote fill is allowed.

Actual spread is included through bid/ask sides. The declared commission proxy is `52.5e-6 * (entry_price + exit_price)` index points per unit, based on the current smallest JForex index commission tier in the [Dukascopy fee schedule](https://www.dukascopy.com/swiss/english/about/fee-schedule/). It is not a verified historical or user-account fee. Separate adverse costs of 0, 0.25, and 1.0 index points per leg are reported without rewriting observed prices. One index point means a 1.0 price change.

## Limits on interpretation

September is previously exposed **development data**, not untouched out-of-sample data. The frozen calendar still flags September 7 as unresolved. Random-entry controls, statistical uncertainty, independent-sample sufficiency, broker depth/order acceptance, actual account fees, and a forward record are not established. Date and parent-context breakdowns expose concentration but do not prove independence.

`execution_coverage_eligible` means completed observed execution coverage without censoring. `rule_preserving_exit_candidate` additionally retains the original target. Neither field establishes profitability, teacher fidelity, or native approval. Reported drawdown is the maximum drawdown of closed-trade net points, not intratrade account drawdown. Censored exposures are never assigned zero realized profit.

The current separate Mac-native installation is not supplied or updated by this research layer. The older NQ/MNQ CSVs, synthetic September journals, small decoder fixture, quarantined native-daily collector, and historical ICC results cannot substitute for this study's required market input.

Acquisition/causal verification and network downloads are separate from quote simulation timing. A fast synthetic test run is not a claim that a full market backtest, complete acquisition, or causal audit finishes in seconds.

## Pre-outcome performance continuation (2026-10-22)

The earlier uncommitted ChatGPT workspace changes were unavailable in the published branch. With explicit user authorization, equivalent narrow improvements were rebuilt: at most two independent causal-cutoff jobs, merged in chronological order, and omission of no-op quote dispatch to policies with no position or pending candidate. Every quote remains validated and counted. Every required cutoff still performs fresh-process replay and native reaggregation; source bindings and deliberately future-reading positive controls remain mandatory. `--causal-workers` accepts 1 or 2, defaulting to 1. Frozen engine, replay, specification, collector, protocol, and all seven trading policies remain unchanged.

These changes precede any market PnL run in this continuation. Synthetic equivalence and software verification do not establish full-market speedup or profitability. On macOS, use a canonical temporary path such as `TMPDIR=/private/tmp` when running the complete verifier; the initial run retained two temporary-directory spelling failures before the canonical-path rerun.
