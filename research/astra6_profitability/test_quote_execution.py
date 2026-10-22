"""Synthetic arithmetic/chronology tests, never market-profit evidence."""
from dataclasses import replace
import unittest

from quote_execution import Candidate, Config, DEFAULT_POLICIES, HOUR, MINUTE, Pivot, Policy, Quote, simulate_many


T = 9 * HOUR


def candidate(child_id="a", time=T, **changes):
    base = Candidate(child_id=child_id, decision_time_ms=time, bar_start_ms=time - MINUTE,
                     direction="long", stop=90.0, target=120.0,
                     target_known_at_ms=time - 2 * MINUTE, valid_at_decision=True,
                     parent_zone_id="parent-a", target_bos_id="bos-a")
    return replace(base, **changes)


def quote(offset, bid=99.0, ask=100.0, time=T, source=None):
    return Quote(time + offset, bid, ask, source or "synthetic-quote-%s-%s" % (offset, bid))


def simulate(quotes, candidates=None, policy=None, pivots=(), config=None):
    policy = policy or Policy("test")
    result = simulate_many(quotes, [candidate()] if candidates is None else candidates,
                           pivots, [policy], config or Config(commission_per_million=0.0))
    return result["policies"][policy.name]


class QuoteExecutionTests(unittest.TestCase):
    def test_entry_requires_strictly_later_actual_quote_than_latency_boundary(self):
        out = simulate([quote(500), quote(1000), quote(1001), quote(2000, 121, 122)])
        trade, = out["trades"]
        self.assertEqual(trade["entry_time_ms"], T + 1001)
        self.assertEqual(trade["entry_price"], 100)
        self.assertEqual(trade["exit_price"], 121)
        self.assertEqual(trade["exit_quote"]["record_index"], 3)
        self.assertEqual(trade["gross_points"], 21)
        self.assertEqual(trade["gross_r"], 2.1)
        self.assertFalse(trade["execution_allowed"])

    def test_long_short_execution_uses_opposite_quote_sides_and_actual_crossing(self):
        long_quotes = [quote(1001), quote(2000, 121, 122)]
        short_quotes = [Quote(q.timestamp_ms, 200 - q.ask, 200 - q.bid, q.source_id) for q in long_quotes]
        long = simulate(long_quotes)["trades"][0]
        short = simulate(short_quotes, [candidate(direction="short", stop=110, target=80)])["trades"][0]
        self.assertEqual((long["entry_price"], short["entry_price"]), (100, 100))
        self.assertEqual((long["exit_price"], short["exit_price"]), (121, 79))
        self.assertEqual(long["gross_points"], short["gross_points"])
        self.assertEqual(long["mfe_points"], short["mfe_points"])
        self.assertEqual(long["mae_points"], short["mae_points"])
        # A jump through the stop executes at the observed worse quote, not90.
        stop = simulate([quote(1001), quote(2000, 87, 88)])["trades"][0]
        self.assertEqual((stop["exit_price"], stop["gross_points"]), (87, -13))

    def test_target_touch_and_stop_touch_during_latency_cancel_permanently(self):
        for adverse, expected in [(quote(500, 121, 122), "ORIGINAL_TARGET_REACHED_BEFORE_ENTRY"),
                                  (quote(500, 89, 90), "STOP_REACHED_BEFORE_ENTRY")]:
            with self.subTest(expected=expected):
                out = simulate([adverse, quote(1500), quote(2000, 121, 122)])
                self.assertEqual(out["trades"], [])
                self.assertEqual(out["decisions"][0]["status"], expected)

    def test_short_pending_native_target_is_bid_based_even_when_ask_not_at_target(self):
        c = candidate(direction="short", stop=110, target=80)
        out = simulate([quote(500, 79, 81), quote(1500, 100, 101)], [c])
        self.assertEqual(out["trades"], [])
        self.assertEqual(out["decisions"][0]["status"], "ORIGINAL_TARGET_REACHED_BEFORE_ENTRY")

    def test_same_cutoff_and_delayed_revocation_and_future_state_are_separate(self):
        qs = [quote(400), quote(1500), quote(2000, 121, 122)]
        invalid = simulate(qs, [candidate(valid_at_decision=False)])
        self.assertEqual(invalid["decisions"][0]["status"], "PERMISSION_INVALID_AT_DECISION")
        cancelled = simulate(qs, [candidate(invalidated_at_ms=T + 500)])
        self.assertEqual(cancelled["trades"], [])
        self.assertEqual(cancelled["decisions"][0]["status"], "PERMISSION_REVOKED_BEFORE_ENTRY")
        future = simulate(qs, [candidate(invalidated_at_ms=T + 3000)])
        self.assertEqual(future["trades"][0]["gross_points"], 21)

    def test_all_controls_preserve_original_target_admission(self):
        bad = [candidate("unknown", target=None, target_known_at_ms=None),
               candidate("late", target_known_at_ms=T),
               candidate("reached", target_already_reached=True),
               candidate("spread", target=99)]
        # Check separately so simultaneous-candidate exclusion does not hide a
        # broken target guard in any fixed-R control.
        for c in bad:
            for policy in DEFAULT_POLICIES:
                with self.subTest(candidate=c.child_id, policy=policy.name):
                    out = simulate([quote(1001, 98, 100), quote(2000, 121, 122)], [c], policy)
                    self.assertEqual(out["trades"], [])

    def test_deterministic_tie_is_selected_before_later_outcome(self):
        c1 = candidate("a", stop=98)
        c2 = candidate("b", stop=90)
        qs = [quote(500, 97, 98), quote(1500), quote(2000, 121, 122)]
        one = simulate(qs, [c2, c1])
        two = simulate(qs, [c1, c2])
        self.assertEqual(one, two)
        self.assertEqual(one["trades"], [])
        statuses = {x["child_id"]: x["status"] for x in one["decisions"]}
        self.assertEqual(statuses, {"a": "STOP_REACHED_BEFORE_ENTRY", "b": "EARLIER_PENDING_CANDIDATE"})

    def test_expired_pending_order_does_not_block_next_minute_review(self):
        # A has no quote after its latency window; B is known exactly when A
        # expires. The later real quote may fill B, never resurrect A.
        qs = [quote(1000), quote(MINUTE + 1001), quote(MINUTE + 2000, 121, 122)]
        out = simulate(qs, [candidate("a"), candidate("b", time=T + MINUTE)])
        self.assertEqual([x["status"] for x in out["decisions"]], ["PENDING_ENTRY_EXPIRED", "FILLED"])
        self.assertEqual([x["child_id"] for x in out["trades"]], ["b"])

    def test_break_even_waits_for_later_timestamp_and_old_stop_remains_live(self):
        policy = Policy("be", break_even_after_r=1)
        for same_time_bid, next_quote, expected in [
                (99, quote(2001, 98, 99), 98),
                (89, quote(2001, 105, 106), 89)]:
            with self.subTest(same_time_bid=same_time_bid):
                qs = [quote(1001), quote(2000, 110, 111),
                      quote(2000, same_time_bid, same_time_bid + 1), next_quote]
                trade = simulate(qs, policy=policy)["trades"][0]
                self.assertEqual(trade["exit_price"], expected)
                self.assertEqual(trade["exit_time_ms"], T + (2001 if same_time_bid == 99 else 2000))
                self.assertEqual(len(trade["stop_history"]), 2 if same_time_bid == 99 else 1)

    def test_swing_trail_is_post_entry_known_time_delayed_and_preserves_earlier_stop(self):
        policy = Policy("trail", pivot_trail_after_r=1)
        c = candidate(target=130)
        first = Pivot(T + 2 * MINUTE, "low", 96, T + MINUTE, "pivot-one")
        second = Pivot(T + 3 * MINUTE, "low", 99, T + 2 * MINUTE, "pivot-two")
        qs = [quote(1001), quote(2000, 110, 111), quote(MINUTE, 111, 112),
              quote(2 * MINUTE, 105, 106), quote(3 * MINUTE, 95, 96)]
        trade = simulate(qs, [c], policy, [first, second])["trades"][0]
        self.assertEqual(trade["exit_price"], 95)
        self.assertEqual(trade["final_stop"], 96)
        self.assertEqual(trade["stop_history"][-1]["source_id"], "pivot-one")
        # A pivot occurring before entry but confirmed later is not post-entry.
        stale = replace(first, pivot_time_ms=T, price=109)
        out = simulate(qs, [c], policy, [stale])
        self.assertEqual(out["trades"][0]["status"], "CENSORED")
        self.assertEqual(len(out["trades"][0]["stop_history"]), 1)

    def test_fee_and_slippage_reconcile_both_legs_without_rewriting_quotes(self):
        config = Config(commission_per_million=52.5, slippage_points_per_side=0.25)
        trade = simulate([quote(1001), quote(2000, 121, 122)], config=config)["trades"][0]
        self.assertEqual((trade["entry_price"], trade["exit_price"]), (100, 121))
        self.assertAlmostEqual(trade["commission_proxy_points"], 52.5e-6 * (100 + 121))
        self.assertEqual(trade["slippage_scenario_points"], 0.5)
        self.assertAlmostEqual(trade["net_points"], 21 - 52.5e-6 * 221 - 0.5)
        self.assertAlmostEqual(trade["net_r"], trade["net_points"] / 10)

    def test_mfe_mae_use_liquidation_side_entry_to_exit_only(self):
        qs = [quote(-1, 150, 151), quote(1001), quote(2000, 95, 96),
              quote(3000, 121, 122), quote(4000, 500, 501)]
        trade = simulate(qs)["trades"][0]
        self.assertEqual((trade["mfe_points"], trade["mae_points"]), (21, 5))
        self.assertEqual((trade["mfe_at_ms"], trade["mae_at_ms"]), (T + 3000, T + 2000))
        spread = simulate([quote(1001), quote(2000, 121, 122)])["trades"][0]
        self.assertEqual(spread["mae_points"], 1)

    def test_gap_censors_without_exit_and_halts_later_entry(self):
        qs = [quote(1001), quote(61_002, 121, 122), quote(2 * MINUTE + 1001)]
        out = simulate(qs, [candidate(), candidate("later", time=T + 2 * MINUTE)])
        trade, = out["trades"]
        self.assertEqual(trade["status"], "CENSORED")
        self.assertIsNone(trade["exit_price"])
        self.assertIsNone(trade["net_points"])
        self.assertEqual(trade["mfe_points"], 0)
        self.assertEqual(trade["last_observed_quote"]["timestamp_ms"], T + 1001)
        self.assertTrue(out["summary"]["halted_after_censoring"])
        self.assertFalse(out["summary"]["execution_coverage_eligible"])
        self.assertEqual(out["decisions"][-1]["status"], "HALTED_AFTER_CENSORED_EXPOSURE")

    def test_rollover_has_prespecified_buffer_not_retrospective_last_quote(self):
        t = 19 * HOUR + 58 * MINUTE
        c = candidate(time=t)
        qs = [quote(1001, time=t), quote(MINUTE, 102, 103, time=t),
              quote(MINUTE + 1, 103, 104, time=t), quote(MINUTE + 50_000, 119, 120, time=t)]
        trade = simulate(qs, [c])["trades"][0]
        self.assertEqual(trade["exit_reason"], "DAILY_ROLLOVER_FLATTEN")
        self.assertEqual((trade["exit_time_ms"], trade["exit_price"]), (t + MINUTE + 1, 103))
        missing = simulate([quote(1001, time=t), quote(MINUTE, time=t), quote(2 * MINUTE, 105, 106, time=t)], [c])
        self.assertEqual(missing["trades"][0]["exit_reason"], "DAILY_ROLLOVER_FLATTEN_DEADLINE_MISSED")
        self.assertIsNone(missing["trades"][0]["exit_price"])
        blocked = simulate([quote(1001, time=t + MINUTE)], [candidate(time=t + MINUTE)])
        self.assertEqual(blocked["trades"], [])

    def test_study_end_buffer_and_unclosed_tail_remain_explicit(self):
        config = Config(study_end_ms=T + 3 * MINUTE, commission_per_million=0)
        qs = [quote(1001), quote(MINUTE), quote(2 * MINUTE, 102, 103), quote(2 * MINUTE + 1, 103, 104)]
        trade = simulate(qs, config=config)["trades"][0]
        self.assertEqual(trade["exit_reason"], "STUDY_END_FLATTEN")
        self.assertEqual(trade["exit_time_ms"], T + 2 * MINUTE + 1)
        tail = simulate([quote(1001)])["trades"][0]
        self.assertEqual(tail["status"], "CENSORED")
        self.assertEqual(tail["exit_reason"], "END_OF_QUOTES_WITH_OPEN_EXPOSURE")
        self.assertIsNone(tail["gross_r"])

    def test_max_hold_uses_actual_quote_and_controls_are_not_rule_fit_candidates(self):
        qs = [quote(1001)] + [quote(1001 + n * MINUTE, 100, 101) for n in range(1, 61)]
        trade = simulate(qs, policy=Policy("time", max_hold_minutes=60))["trades"][0]
        self.assertEqual(trade["exit_reason"], "MAX_HOLD_TIME")
        self.assertEqual(trade["holding_ms"], 60 * MINUTE)
        control = simulate([quote(1001), quote(2000, 111, 112)], policy=Policy("control", fixed_r_control=1))
        self.assertEqual(control["trades"][0]["original_target"], 120)
        self.assertEqual(control["trades"][0]["exit_target"], 110)
        self.assertFalse(control["summary"]["rule_preserving_exit_candidate"])

    def test_future_suffix_cannot_change_a_completed_trade(self):
        prefix = [quote(1001), quote(2000, 121, 122)]
        first = simulate(prefix)["trades"]
        appended = simulate(prefix + [quote(3 * MINUTE, 50, 51)],
                            pivots=[Pivot(T + 3 * MINUTE, "low", 110, T + 2 * MINUTE, "future-pivot")])["trades"]
        self.assertEqual(first, appended)

    def test_empty_quotes_retain_every_candidate_without_profit(self):
        out = simulate([], [candidate("a"), candidate("b", time=T + MINUTE)])
        self.assertEqual(out["summary"]["candidate_count"], 2)
        self.assertEqual(out["summary"]["completed_trades"], 0)
        self.assertFalse(out["summary"]["execution_coverage_eligible"])
        self.assertEqual(out["trades"], [])

    def test_malformed_or_backwards_quotes_cannot_produce_partial_profit_result(self):
        bad_streams = [[quote(1001), quote(1000)],
                       [quote(1001, float("nan"), 100)],
                       [quote(1001, 101, 100)],
                       [Quote(T + 1001, 99, 100, "")]]
        for qs in bad_streams:
            with self.subTest(quotes=qs):
                with self.assertRaises(ValueError):
                    simulate(qs)


if __name__ == "__main__":
    unittest.main()
