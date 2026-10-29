# ASTRA 6 — verified archived local target and 1M/5M breakdown

## Scope and evidence

Read-only replay of the **4,153 local CANDIDATE_RETURN** events in
`source/astra_integrated_work/full_history_calendar_verified/transitions.jsonl.gz`
inside the user's October 28 research archive. The full uncompressed transition
stream was SHA256-verified against the frozen run summary:
`f40738610fc6f65d574e6126a9d558e42c678da55b1781da85e0d03bd98fd5d1`.
Input source namespace `1577e60a7feab411e49da7a56c7052a64738cd1757cfd60aa11fd783ff43b60b`.
No new price bars were invented and no native QuantConnect backtest ran.

## Stage counts (local, not native)

| As-of-decision target state | All return observations | M1 AND M5 aligned observations |
|:--|--:|--:|
| All known opposing zones invalid | 3,148 | 663 |
| No opposing zone known | 104 | 34 |
| Valid opposing zones, none entirely ahead | 46 | 4 |
| Target available | 855 | 108 |
| **Total** | **4,153** | **809** |

**701** local return observations had both structural proxies aligned but no
valid opposing target. These are NOT 701 independent opportunities or verified
trades. Further parent/correction/origin fidelity still needs source review.

### Candidate-level evidence

- **581** unique local candidate IDs in 4,153 returns.
- **435** unique candidates missed a target at their first return.
- Of those, **6** later had a target during their retained return observations.
- The earlier no-target local inventory and its complete four-way partition
  were reproduced exactly with zero target-ID mismatches.
- Joint context issues from the retained archive: 1M opposed 1,310;
  1M unknown 1,064; 5M opposed 857; 5M unknown 1,607;
  missing target 3,298. These overlap, so they are **not** a mutually exclusive
  count of 4,153.

### Frozen reproduction

`python replay_archived_local_stream.py --archive /path/to/ASTRA_research_package_20261028.zip --output /tmp/archived_local_check.json`

Software: 28 focused tests plus 1,000 seeded parity examples against the
archived real `_target` selector implementation passed (29 unittest cases
total including the synthetic seeded equivalence test).

### What this does not prove

This is **NOT** the separate QuantConnect 3,069-return / 2,054-no-target cohort.
The original native run was simulated across dated contracts, and its complete
decision-level catalog was not exported by the Free-plan reporting path.
These counts must never be transplanted into the native cohort. Even though
1M/5M agreement was observed, no alternate continuation, reversal, target or
actual profitable trade is inferred from it. Full native decision audit requires
recovering the saved backtest snapshot `38595614` or adding a reporting-only
as-of-decision census to the existing native signal session and performing a
*single authorized diagnostic run* only if historical outputs cannot answer it.

Existing canonical: parent-owned return to responsible zone, BOTH 1M/5M support,
exact far-wick stop, first relevant opposing-zone exit. New breakout/reversal
paths stay independent research until completed-bar and parent/zone evidence,
actual fills/costs and untouched data establish eligibility and performance.