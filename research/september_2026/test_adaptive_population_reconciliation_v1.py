"""Synthetic contracts only. Never a historical strategy or profitability test."""
import copy
from datetime import datetime, timezone, timedelta
import unittest

from adaptive_population_reconciliation_v1 import reconcile
from test_adaptive_market_paths_v1 import setup, sample_period

UTC = timezone.utc


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def base(*, has_target=False, parent="P1", obs="R1", side="LONG"):
    at = datetime(2026, 10, 28, 13, tzinfo=UTC)
    return {
        "observation_id": obs, "parent_id": parent, "formation_id": "F1",
        "decision_at": int(at.timestamp()), "side": side,
        "entry_reference": 100.0, "exact_far_wick_stop": 96.0 if side == "LONG" else 104.0,
        "parent_state": "ACTIVE", "coverage": "COMPLETE",
        "m1_state": "ALIGNED", "m5_state": "ALIGNED",
        "canonical_target_available": has_target,
        "knowledge_times": {k: int((at-timedelta(minutes=i)).timestamp()) for i,k in enumerate(
            ("parent", "m1", "m5", "formation", "return"))},
        "candidate_zones": [],
    }


def typed_setup(s):
    return {key: iso(val) if isinstance(val, datetime) else val for key, val in vars(s).items()}


def typed_period(p):
    return {key: iso(val) if isinstance(val, datetime) else val for key, val in vars(p).items()}


def minute(at, *, source_id="BAR", high=115):
    return {
        "start": iso(at), "end": iso(at+timedelta(minutes=1)),
        "open": 111.0, "high": high, "low": 108.0, "close": 110.0,
        "source_id": source_id, "observed_from_source": True, "fill_forward": False,
        "dataset_id": "RAW_NQ_MINUTE", "contract_segment_id": "NQ_2026_DEC",
    }


def event(*, path="CONTINUATION"):
    s = setup()
    if path == "REVERSAL":
        s = setup(path="REVERSAL", parent_id="P2", side="SHORT",
                  parent_side="SHORT", exact_far_wick_stop=115,
                  protected_reference_price=105, previous_close=106, break_close=100,
                  old_parent_invalidated_at=s.break_start-timedelta(minutes=30),
                  new_parent_confirmed_at=s.break_start-timedelta(minutes=20))
    p = sample_period(high=125, low=90)
    return {
        "event_id": s.id, "related_original_observation_id": "R1",
        "previous_parent_id": "P1" if path == "REVERSAL" else None,
        "setup": typed_setup(s), "liquidity_periods": [typed_period(p)],
        "known_completed_minutes": [], "known_coverage_complete": False,
        "next_minute": minute(s.break_end, high=112) if path == "CONTINUATION" else None,
    }


class AdaptivePopulationReconciliationTests(unittest.TestCase):
    def result(self, rows=None, events=None):
        return reconcile(rows if rows is not None else [base()],
                         events if events is not None else [event()],
                         expected_total=3069, expected_missing=2054)

    def test_targetless_continuation_is_later_nonexecuting_research_only(self):
        r = self.result()
        self.assertEqual(r["summary"]["original_no_target_observations"], 1)
        self.assertFalse(r["summary"]["original_cohort_complete"])
        self.assertEqual(r["summary"]["distinct_original_no_target_observations_linked"], 1)
        d = r["adaptive_events"][0]
        self.assertEqual(d["break_state"], "BREAK_CONFIRMED_AWAITING_OPEN")
        self.assertEqual(d["next_open_status"], "RESEARCH_ENTRY_GEOMETRY_ONLY")
        self.assertEqual(d["level_continuity"], "UNVERIFIED")
        self.assertTrue(d["strictly_later_than_original_decision"])
        self.assertFalse(d["execution_allowed"])
        self.assertFalse(d["historical_pnl_established"])

    def test_empty_new_events_do_not_imply_missing_qualifying_trades(self):
        r = self.result(events=[])
        self.assertEqual(r["summary"]["adaptive_events_supplied"], 0)
        self.assertEqual(r["summary"]["distinct_original_no_target_observations_linked"], 0)

    def test_existing_target_not_reported_as_recovered(self):
        r = self.result(rows=[base(has_target=True)])
        self.assertEqual(r["summary"]["adaptive_events_linked_to_original_no_target"], 0)
        self.assertEqual(r["summary"]["distinct_original_no_target_observations_linked"], 0)

    def test_continuation_parent_side_binding_is_hard_guard(self):
        e = event()
        for change in ({"parent_id": "P2"}, {"side": "SHORT", "parent_side": "SHORT"}):
            with self.subTest(change=change):
                k = copy.deepcopy(e)
                k["setup"].update(change)
                with self.assertRaisesRegex(ValueError, "continuation must retain"):
                    self.result(events=[k])

    def test_reversal_requires_new_parent_and_prior_invalidation(self):
        e = event(path="REVERSAL")
        r = self.result(events=[e])
        self.assertEqual(r["adaptive_events"][0]["path"], "REVERSAL")
        self.assertEqual(r["adaptive_events"][0]["break_state"], "BREAK_CONFIRMED_AWAITING_OPEN")
        k = copy.deepcopy(e)
        k["setup"]["new_parent_confirmed_at"] = None
        r = self.result(events=[k])
        self.assertEqual(r["adaptive_events"][0]["break_state"], "WAIT")
        k = copy.deepcopy(e)
        k["previous_parent_id"] = "WRONG"
        with self.assertRaisesRegex(ValueError, "explicit distinct opposite"):
            self.result(events=[k])

    def test_adaptive_event_cannot_backdate_original_decision(self):
        e = event()
        e["setup"]["break_start"] = "2026-10-28T12:00:00Z"
        e["setup"]["break_end"] = "2026-10-28T12:15:00Z"
        with self.assertRaisesRegex(ValueError, "future-known"):
            self.result(events=[e])

    def test_future_known_period_is_not_rescued(self):
        e = event()
        e["liquidity_periods"][0]["known_at"] = "2026-10-28T14:05:00Z"
        r = self.result(events=[e])
        self.assertEqual(r["adaptive_events"][0]["break_state"], "WATCH")
        self.assertEqual(r["adaptive_events"][0]["next_open_status"], "NOT_EVALUATED")

    def test_next_open_must_match_actual_one_minute_source(self):
        for field,value in (("fill_forward",True),("observed_from_source",False),
                            ("start","2026-10-28T14:16:00Z"),
                            ("contract_segment_id","OTHER_SEGMENT")):
            with self.subTest(field=field):
                e = event()
                e["next_minute"][field] = value
                with self.assertRaises(ValueError):
                    self.result(events=[e])

    def test_unknown_gap_before_break_blocks_known_levels(self):
        e = event()
        e["known_completed_minutes"] = [
            minute(datetime(2026, 10, 28, 12, 0, tzinfo=UTC),source_id="C1"),
            minute(datetime(2026, 10, 28, 12, 2, tzinfo=UTC),source_id="C2"),
        ]
        r = self.result(events=[e])
        self.assertEqual(r["adaptive_events"][0]["break_state"], "WATCH")

    def test_no_future_minute_can_be_in_prebreak_prefix(self):
        e = event()
        e["known_completed_minutes"] = [minute(datetime(2026,10,28,14,0,tzinfo=UTC))]
        with self.assertRaisesRegex(ValueError, "future bar"):
            self.result(events=[e])

    def test_duplicate_event_or_missing_original_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.result(events=[event(), event()])
        e = event()
        e["related_original_observation_id"] = "R2"
        with self.assertRaisesRegex(ValueError, "not supplied"):
            self.result(events=[e])

    def test_missing_identity_or_timezone_cannot_turn_into_trade(self):
        e = event()
        e["setup"]["dataset_id"] = "UNSPECIFIED_DATASET"
        with self.assertRaisesRegex(ValueError, "explicit immutable"):
            self.result(events=[e])
        e = event()
        e["setup"]["break_start"] = "2026-10-28T14:00:00"
        with self.assertRaisesRegex(ValueError, "timezone offset"):
            self.result(events=[e])

    def test_original_cohort_remains_unchanged_and_replay_deterministic(self):
        a = [base()]
        e = [event()]
        orig = copy.deepcopy(a); spec = copy.deepcopy(e)
        self.assertEqual(self.result(a,e), self.result(a,e))
        self.assertEqual(a,orig)
        self.assertEqual(e,spec)

if __name__ == "__main__":
    unittest.main()
