"""Independent public-API checks for the quote execution research amendment.

Every price below is a deliberately constructed synthetic quote.  These tests
check chronology/accounting; they are not observations of market profitability.
"""
from dataclasses import replace
import math
import unittest

import quote_execution as execution


MINUTE = 60_000
D = 2 * MINUTE


class ExecutionBoundaryTests(unittest.TestCase):
    def candidate(self, **changes):
        base = execution.Candidate(
            child_id="child-a", decision_time_ms=D, bar_start_ms=D - MINUTE,
            direction="long", stop=90.0, target=120.0,
            target_known_at_ms=0, valid_at_decision=True,
            parent_zone_id="parent-a", target_bos_id="parent-bos-a")
        return replace(base, **changes)

    def quote(self, offset, bid, ask, identity=None):
        return execution.Quote(D + offset, bid, ask,
                               identity or "synthetic-quote-" + str(offset))

    def run_model(self, quotes, candidates=None, policy=None, pivots=(), config=None):
        policy = policy or execution.Policy("audit")
        result = execution.simulate_many(
            quotes, [self.candidate()] if candidates is None else candidates,
            pivots=pivots, policies=[policy],
            config=config or execution.Config(commission_per_million=0.0))
        self.assertIs(result["execution_allowed"], False)
        self.assertIs(result["current_native_approval"], False)
        self.assertIs(result["genuine_oos"], False)
        return result["policies"][policy.name]

    def test_entry_requires_strictly_later_quote_than_latency_boundary(self):
        result = self.run_model([
            self.quote(500, 97, 98), self.quote(1000, 98, 99),
            self.quote(1001, 99, 100), self.quote(2000, 121, 122)])
        trade = result["trades"][0]
        self.assertEqual(trade["entry_time_ms"], D + 1001)
        self.assertEqual(trade["entry_price"], 100)
        self.assertEqual(trade["exit_price"], 121)
        self.assertEqual(trade["gross_points"], 21)
        self.assertEqual(trade["entry_quote"]["record_index"], 2)
        self.assertEqual(trade["original_target"], 120)
        self.assertEqual(trade["parent_zone_id"], "parent-a")
        self.assertEqual(trade["target_bos_id"], "parent-bos-a")

    def test_transient_target_reach_during_latency_cancels_permanently(self):
        for direction, stop, target, bid, ask in (
                ("long", 90, 120, 120, 121),
                ("short", 110, 90, 89, 91)):
            with self.subTest(direction=direction):
                result = self.run_model([
                    self.quote(500, bid, ask), self.quote(1001, 99, 100),
                    self.quote(2000, 101, 102)], candidates=[self.candidate(
                        direction=direction, stop=stop, target=target)])
                self.assertEqual(result["trades"], [])
                self.assertEqual(result["decisions"][0]["status"],
                                 "ORIGINAL_TARGET_REACHED_BEFORE_ENTRY")
                self.assertEqual(result["decisions"][0]["recorded_at_ms"], D + 500)

    def test_pending_permission_is_time_gated_not_final_snapshot_filter(self):
        quotes = [self.quote(1001, 99, 100), self.quote(2000, 120, 121)]
        cancelled = self.run_model(quotes, candidates=[self.candidate(
            invalidated_at_ms=D + 1001)])
        self.assertEqual(cancelled["trades"], [])
        self.assertEqual(cancelled["decisions"][0]["status"],
                         "PERMISSION_REVOKED_BEFORE_ENTRY")
        # A later context revocation does not rewrite an earlier valid fill or
        # silently introduce a new liquidation rule into this amendment.
        earlier = self.run_model(quotes, candidates=[self.candidate(
            invalidated_at_ms=D + 1500)])
        self.assertEqual(earlier["trades"][0]["entry_time_ms"], D + 1001)
        self.assertEqual(earlier["trades"][0]["exit_price"], 120)

    def test_target_clock_and_final_cutoff_permission_are_required(self):
        cases = [
            ({"valid_at_decision": False}, "PERMISSION_INVALID_AT_DECISION"),
            ({"target_known_at_ms": D - MINUTE + 1},
             "ORIGINAL_TARGET_NOT_KNOWN_AT_BAR_OPEN"),
            ({"target_already_reached": True}, "ORIGINAL_TARGET_ALREADY_REACHED"),
            ({"target": None, "target_known_at_ms": None}, "ORIGINAL_TARGET_UNKNOWN")]
        for changes, status in cases:
            with self.subTest(status=status):
                result = self.run_model([self.quote(1001, 99, 100)],
                                        candidates=[self.candidate(**changes)])
                self.assertEqual(result["trades"], [])
                self.assertEqual(result["decisions"][0]["status"], status)

    def test_bid_ask_mirror_preserves_execution_economics(self):
        long_quotes = [self.quote(1001, 99, 100), self.quote(2000, 120, 121)]
        short_quotes = [execution.Quote(q.timestamp_ms, 200 - q.ask,
                                       200 - q.bid, q.source_id)
                        for q in long_quotes]
        long = self.run_model(long_quotes)["trades"][0]
        short = self.run_model(short_quotes, candidates=[self.candidate(
            direction="short", stop=110, target=80)])["trades"][0]
        self.assertEqual(long["entry_price"], 100)
        self.assertEqual(short["entry_price"], 100)
        self.assertEqual(long["exit_price"], 120)
        self.assertEqual(short["exit_price"], 80)
        for field in ("gross_points", "gross_r", "mfe_points", "mae_points"):
            self.assertEqual(long[field], short[field], field)

    def test_short_exit_requires_ask_to_reach_original_target(self):
        result = self.run_model([
            self.quote(1001, 100, 101), self.quote(2000, 89, 91),
            self.quote(3000, 88, 90)], candidates=[self.candidate(
                direction="short", stop=110, target=90)])
        trade = result["trades"][0]
        self.assertEqual(trade["exit_time_ms"], D + 3000)
        self.assertEqual(trade["exit_price"], 90)
        self.assertEqual(trade["gross_points"], 10)

    def test_same_millisecond_records_keep_source_order_and_old_stop(self):
        result = self.run_model([
            self.quote(1001, 99, 100, "first"),
            self.quote(2000, 110, 111, "arms-break-even"),
            self.quote(2000, 89, 90, "old-stop-still-live")],
            policy=execution.Policy("be", break_even_after_r=1))
        trade = result["trades"][0]
        self.assertEqual(trade["exit_price"], 89)
        self.assertEqual(trade["final_stop"], 90)
        self.assertEqual(len(trade["stop_history"]), 1)
        self.assertEqual(trade["exit_quote"]["record_index"], 2)
        self.assertEqual(trade["exit_quote"]["source_id"], "old-stop-still-live")

    def test_break_even_does_not_apply_at_arming_timestamp(self):
        result = self.run_model([
            self.quote(1001, 99, 100), self.quote(2000, 110, 111),
            self.quote(2000, 99, 100, "same-ms-no-new-stop"),
            self.quote(2001, 98, 99, "next-ms-stop")],
            policy=execution.Policy("be", break_even_after_r=1))
        trade = result["trades"][0]
        self.assertEqual(trade["exit_time_ms"], D + 2001)
        self.assertEqual(trade["exit_price"], 98)
        self.assertEqual(trade["final_stop"], 100)
        self.assertEqual(trade["gross_points"], -2)

    def test_stop_gap_uses_observed_quote_not_stop_price(self):
        result = self.run_model([
            self.quote(1001, 99, 100), self.quote(2000, 85, 87)])
        trade = result["trades"][0]
        self.assertEqual(trade["exit_price"], 85)
        self.assertEqual(trade["gross_points"], -15)
        self.assertEqual(trade["initial_risk_points"], 10)
        self.assertEqual(trade["gross_r"], -1.5)

    def test_quote_gap_censors_and_permanently_halts_later_entries(self):
        second = self.candidate(child_id="later", decision_time_ms=4 * MINUTE,
                                bar_start_ms=3 * MINUTE)
        result = self.run_model([
            self.quote(1001, 99, 100), self.quote(61002, 130, 131),
            execution.Quote(4 * MINUTE + 1001, 99, 100, "later-quote")],
            candidates=[self.candidate(), second])
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "CENSORED")
        self.assertEqual(trade["exit_reason"], "QUOTE_GAP_WITH_OPEN_EXPOSURE")
        for field in ("exit_price", "exit_quote", "gross_points", "net_points"):
            self.assertIsNone(trade[field])
        self.assertEqual(trade["last_observed_quote"]["timestamp_ms"], D + 1001)
        self.assertTrue(result["summary"]["halted_after_censoring"])
        self.assertFalse(result["summary"]["execution_coverage_eligible"])
        self.assertEqual(result["decisions"][1]["status"],
                         "HALTED_AFTER_CENSORED_EXPOSURE")

    def test_exact_max_gap_boundary_still_uses_observed_quote(self):
        result = self.run_model([
            self.quote(1001, 99, 100), self.quote(61001, 120, 121)])
        self.assertEqual(result["trades"][0]["status"], "CLOSED")
        self.assertEqual(result["trades"][0]["exit_price"], 120)

    def test_expiry_equal_boundary_is_not_a_fill_and_no_quotes_are_counted(self):
        expired = self.run_model([self.quote(60000, 99, 100)])
        self.assertEqual(expired["trades"], [])
        self.assertEqual(expired["decisions"][0]["status"], "PENDING_ENTRY_EXPIRED")
        absent = self.run_model([])
        self.assertEqual(absent["summary"]["candidate_count"], 1)
        self.assertEqual(absent["summary"]["completed_trades"], 0)
        self.assertFalse(absent["summary"]["execution_coverage_eligible"])
        self.assertEqual(absent["decisions"][0]["status"], "NO_POST_DECISION_QUOTES")

    def test_zero_move_roundtrip_loses_spread_and_declared_costs(self):
        config = execution.Config(commission_per_million=52.5,
                                  slippage_points_per_side=0.25)
        result = self.run_model([
            self.quote(1001, 99, 100), self.quote(61001, 99, 100)],
            policy=execution.Policy("time", max_hold_minutes=1), config=config)
        trade = result["trades"][0]
        self.assertEqual(trade["gross_points"], -1)
        self.assertAlmostEqual(trade["commission_proxy_points"], 199 * 52.5 / 1e6)
        self.assertEqual(trade["slippage_scenario_points"], 0.5)
        self.assertAlmostEqual(trade["net_points"], -1 - 199 * 52.5 / 1e6 - 0.5)
        self.assertEqual(trade["entry_price"], 100)
        self.assertEqual(trade["exit_price"], 99)

    def test_mfe_mae_use_liquidation_quotes_only_while_exposure_exists(self):
        result = self.run_model([
            self.quote(1000, 118, 119), self.quote(1001, 99, 100),
            self.quote(2000, 103, 104), self.quote(3000, 90, 91),
            self.quote(4000, 500, 501)])
        trade = result["trades"][0]
        self.assertEqual(trade["mfe_points"], 3)
        self.assertEqual(trade["mae_points"], 10)
        self.assertEqual(trade["mfe_at_ms"], D + 2000)
        self.assertEqual(trade["mae_at_ms"], D + 3000)
        self.assertEqual(trade["mfe_r"], 0.3)
        self.assertEqual(trade["mae_r"], 1)

    def test_initial_spread_is_retained_in_partial_censored_excursion(self):
        result = self.run_model([self.quote(1001, 97, 100)])
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "CENSORED")
        self.assertEqual(trade["mae_points"], 3)
        self.assertEqual(trade["mfe_points"], 0)
        self.assertEqual(trade["last_observed_unrealized_gross_points"], -3)
        self.assertIsNone(trade["net_points"])

    def test_candidate_ties_are_stable_and_do_not_multiply_exposure(self):
        first = self.candidate(child_id="a")
        second = self.candidate(child_id="z")
        quotes = [self.quote(1001, 99, 100), self.quote(2000, 120, 121)]
        left = self.run_model(quotes, candidates=[second, first])
        right = self.run_model(quotes, candidates=[first, second])
        self.assertEqual(left, right)
        self.assertEqual([x["child_id"] for x in left["trades"]], ["a"])
        self.assertEqual(left["summary"]["candidate_count"], 2)
        self.assertEqual(left["decisions"][1]["status"], "EARLIER_PENDING_CANDIDATE")

    def test_future_suffix_and_future_pivots_do_not_rewrite_closed_prefix(self):
        prefix = [self.quote(1001, 99, 100), self.quote(2000, 120, 121)]
        policy = execution.Policy("trail", pivot_trail_after_r=1)
        before = self.run_model(prefix, policy=policy)
        after = self.run_model(
            prefix + [self.quote(10000, 10, 11), self.quote(20000, 500, 501)],
            policy=policy,
            pivots=[execution.Pivot(4 * MINUTE, "low", 115, 3 * MINUTE,
                                    "future-confirmed-pivot")])
        self.assertEqual(before["trades"], after["trades"])
        self.assertEqual(before["decisions"], after["decisions"])

    def test_new_pivot_does_not_discard_earlier_now_effective_stop(self):
        result = self.run_model([
            self.quote(1001, 99, 100), self.quote(2000, 110, 111),
            self.quote(60000, 110, 111), self.quote(120000, 110, 111),
            self.quote(180000, 99, 100)],
            policy=execution.Policy("trail", pivot_trail_after_r=1),
            pivots=[
                execution.Pivot(D + 120000, "low", 100, D + 60000,
                                "earlier-confirmed-low"),
                execution.Pivot(D + 180000, "low", 105, D + 120000,
                                "newer-confirmed-low")])
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_time_ms"], D + 180000)
        self.assertEqual(trade["exit_price"], 99)
        self.assertEqual(trade["final_stop"], 100)
        self.assertEqual(trade["stop_history"][-1]["source_id"],
                         "earlier-confirmed-low")

    def test_late_confirmation_does_not_turn_preentry_pivot_into_trailing_pivot(self):
        result = self.run_model([
            self.quote(1001, 99, 100), self.quote(2000, 110, 111),
            self.quote(60000, 110, 111), self.quote(60001, 95, 96),
            self.quote(61000, 120, 121)],
            policy=execution.Policy("trail", pivot_trail_after_r=1),
            pivots=[execution.Pivot(D + MINUTE, "low", 105, D - MINUTE,
                                    "occurred-before-entry")])
        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "TARGET_MARKET_IF_TOUCHED")
        self.assertEqual(trade["exit_price"], 120)
        self.assertEqual(trade["final_stop"], 90)
        self.assertEqual(len(trade["stop_history"]), 1)

    def test_rollover_flatten_uses_later_quote_never_last_price_backfill(self):
        decision = (19 * 60 + 58) * MINUTE
        candidate = self.candidate(decision_time_ms=decision,
                                   bar_start_ms=decision - MINUTE)
        trigger = (19 * 60 + 59) * MINUTE
        quotes = [execution.Quote(decision + 1001, 99, 100, "entry"),
                  execution.Quote(trigger, 102, 103, "at-trigger"),
                  execution.Quote(trigger + 1, 101, 102, "after-trigger")]
        result = self.run_model(quotes, candidates=[candidate])
        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "DAILY_ROLLOVER_FLATTEN")
        self.assertEqual(trade["exit_time_ms"], trigger + 1)
        self.assertEqual(trade["exit_price"], 101)
        missed = self.run_model(quotes[:2] + [execution.Quote(
            20 * 60 * MINUTE, 130, 131, "too-late")], candidates=[candidate])
        self.assertEqual(missed["trades"][0]["status"], "CENSORED")
        self.assertIsNone(missed["trades"][0]["exit_price"])

    def test_fixed_r_control_still_requires_original_target_admissibility(self):
        result = self.run_model([self.quote(1001, 99, 100)],
            candidates=[self.candidate(target=99.5)],
            policy=execution.Policy("control", fixed_r_control=2))
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["decisions"][0]["status"],
                         "EXECUTABLE_ENTRY_NOT_BETWEEN_STOP_AND_ORIGINAL_TARGET")

    def test_invalid_inputs_fail_instead_of_repairing_or_interpolating(self):
        quote_cases = [
            [self.quote(1001, 101, 100)],
            [self.quote(1001, math.nan, 100)],
            [self.quote(1001, 99, math.inf)],
            [self.quote(2000, 99, 100), self.quote(1001, 99, 100)]]
        for quotes in quote_cases:
            with self.subTest(quotes=quotes), self.assertRaises(ValueError):
                self.run_model(quotes)
        with self.assertRaises(ValueError):
            self.run_model([], candidates=[self.candidate(), self.candidate()])


if __name__ == "__main__":
    unittest.main()
