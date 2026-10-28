import unittest
from dataclasses import replace
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from adaptive_market_paths_v1 import (
    CausalLiquidityBook, MinuteBar, PeriodEvidence, Premarket0300To0800,
    StructureSetup, evaluate_break, evaluate_next_open,
)

UTC=timezone.utc
NY=ZoneInfo("America/New_York")

def local(day, hour, minute=0):
    return datetime(day.year,day.month,day.day,hour,minute,tzinfo=NY).astimezone(UTC)

def sample_period(kind="PD_ETH", high=121, low=91, known_at=None, complete=True):
    a=datetime(2026,10,26,22,tzinfo=UTC); b=datetime(2026,10,27,21,tzinfo=UTC)
    return PeriodEvidence(kind, "2026-10-27", a,b,known_at or b,high,low,
                          "SOURCE_VERIFIED",complete,"RAW_NQ_MINUTE","NQ_2026_DEC")

def setup(**updates):
    a=datetime(2026,10,28,14,0,tzinfo=UTC)
    o=StructureSetup(
        id="S1",path="CONTINUATION",side="LONG",parent_id="P1",parent_side="LONG",
        parent_state="ACTIVE",parent_known_at=a-timedelta(hours=2),
        correction_id="C1",correction_validated=True,
        correction_known_at=a-timedelta(minutes=15),selected_origin_id="O1",
        origin_known_at=a-timedelta(minutes=20), exact_far_wick_stop=95,
        protected_reference_id="R1",protected_reference_price=105,
        protected_reference_known_at=a-timedelta(minutes=35),
        previous_close=104,previous_close_known_at=a,
        confirmation_tf_minutes=15,break_start=a,break_end=a+timedelta(minutes=15),
        break_close=110,m1_structure="UNKNOWN",m5_structure="UNKNOWN",
        dataset_id="RAW_NQ_MINUTE",contract_segment_id="NQ_2026_DEC")
    return replace(o,**updates)

class LevelTests(unittest.TestCase):
    def test_period_can_only_be_used_when_known(self):
        b=CausalLiquidityBook(); p=sample_period();b.register(p)
        self.assertEqual(b.levels(p.known_at-timedelta(seconds=1)),[])
        self.assertEqual({l.label for l in b.levels(p.known_at)}, {"PD_ETH_H","PD_ETH_L"})

    def test_source_unknown_prevents_target_promotion(self):
        p=sample_period(complete=False);b=CausalLiquidityBook();b.register(p)
        d=evaluate_break(setup(),b.levels(datetime(2026,10,28,14,0,tzinfo=UTC)))
        self.assertEqual(d.status,"WATCH")

    def test_closed_period_immutable_and_future_hidden(self):
        b=CausalLiquidityBook();p=sample_period();b.register(p)
        b.register(p)
        with self.assertRaises(ValueError):b.register(replace(p,high=123))
        self.assertEqual(b.levels(p.known_at-timedelta(seconds=1)),[])

    def test_premarket_300_bars_only_final_at_8(self):
        day=date(2026,10,28);pm=Premarket0300To0800();b=CausalLiquidityBook()
        for i in range(300):
            t=local(day,3)+timedelta(minutes=i)
            bar=MinuteBar(t,t+timedelta(minutes=1),100,110+i/300,90,99,f"{i}")
            pm.feed(bar)
        with self.assertRaises(ValueError):pm.seal(day,local(day,7,59),"id")
        p=pm.seal(day,local(day,8),"id","RAW_NQ_MINUTE","NQ_2026_DEC");b.register(p)
        self.assertTrue(p.coverage_complete)
        self.assertEqual(len(b.levels(local(day,7,59))),0)
        self.assertEqual(len(b.levels(local(day,8))),2)
        self.assertAlmostEqual(p.high,110+299/300)

    def test_missing_premarket_bar_unknown_not_filled(self):
        day=date(2026,10,28);pm=Premarket0300To0800()
        for i in range(300):
            if i==123:continue
            t=local(day,3)+timedelta(minutes=i)
            pm.feed(MinuteBar(t,t+timedelta(minutes=1),100,110,90,99,f"{i}"))
        p=pm.seal(day,local(day,8),"id","RAW_NQ_MINUTE","NQ_2026_DEC")
        self.assertFalse(p.coverage_complete)

    def test_dst_shift_not_hardcoded_utc(self):
        self.assertEqual(local(date(2026,10,28),3).hour,7) # EDT
        self.assertEqual(local(date(2026,11,2),3).hour,8)  # EST

    def test_contract_dataset_mismatch_blocks_cross_segment_target(self):
        p=sample_period();b=CausalLiquidityBook();b.register(p)
        d=evaluate_break(setup(contract_segment_id="NQ_2027_MAR"),
                         b.levels(setup().break_start))
        self.assertEqual(d.status,"WATCH")
        d=evaluate_break(setup(dataset_id="BACK_ADJUSTED_SERIES"),
                         b.levels(setup().break_start))
        self.assertEqual(d.status,"WATCH")

class BreakPathTests(unittest.TestCase):
    def candidate(self,setup_input=None,level_kind="PD_ETH",high=125):
        b=CausalLiquidityBook();b.register(sample_period(level_kind,high=high))
        return evaluate_break(setup_input or setup(),b.levels(datetime(2026,10,28,14,0,tzinfo=UTC)))

    def test_continuation_without_m1_m5_agreement_remains_distinct_nonexecuting(self):
        d=self.candidate()
        self.assertEqual(d.status,"BREAK_CONFIRMED_AWAITING_OPEN")
        self.assertFalse(d.execution_allowed)
        self.assertEqual((d.m1_structure,d.m5_structure),("UNKNOWN","UNKNOWN"))
        self.assertEqual(d.stop,95)
        self.assertEqual(d.target,125)

    def test_next_open_geometry_no_fill_claim(self):
        d=self.candidate();t=d.decision_at
        v=evaluate_next_open(d,111,t)
        self.assertEqual(v.status,"RESEARCH_ENTRY_GEOMETRY_ONLY")
        self.assertFalse(v.execution_allowed)
        self.assertEqual(v.stop,95)
        self.assertEqual(v.target,125)
        self.assertEqual(evaluate_next_open(d,126,t).status,"REJECT")
        self.assertEqual(evaluate_next_open(d,111,t+timedelta(minutes=1)).status,"WAIT")

    def test_reversal_without_new_parent_not_valid(self):
        s=setup(path="REVERSAL")
        self.assertEqual(self.candidate(s).reason,"REVERSAL_REQUIRES_CONFIRMED_NEW_PARENT")
        s=replace(s,old_parent_invalidated_at=s.break_start-timedelta(minutes=15),
                    new_parent_confirmed_at=s.break_start-timedelta(minutes=1))
        self.assertEqual(self.candidate(s).status,"BREAK_CONFIRMED_AWAITING_OPEN")

    def test_correction_required(self):
        self.assertEqual(self.candidate(setup(correction_validated=False)).reason,
                         "CLEAR_CORRECTION_NOT_SOURCE_CONFIRMED")

    def test_prior_known_context_required(self):
        s=setup(correction_known_at=setup().break_end)
        self.assertEqual(self.candidate(s).reason,"REQUIRED_CONTEXT_NOT_KNOWN_BEFORE_BREAK")

    def test_no_chasing_old_break_without_new_structure(self):
        self.assertEqual(self.candidate(setup(previous_close=108)).reason,
                         "NO_NEW_CAUSAL_PROTECTED_STRUCTURE_BREAK")

    def test_zone_through_wick_not_enough(self):
        s=setup(zone_id="Z1",opposing_zone_kind="SUPPLY",zone_far_edge=112,
                zone_known_at=setup().break_start-timedelta(minutes=10))
        self.assertEqual(self.candidate(s).reason,"OPPOSING_ZONE_NOT_BROKEN_BY_COMPLETED_CLOSE")
        self.assertEqual(self.candidate(replace(s,zone_far_edge=107)).status,"BREAK_CONFIRMED_AWAITING_OPEN")

    def test_unknown_parent_not_reversal(self):
        self.assertEqual(self.candidate(setup(parent_state="UNKNOWN")).status,"WAIT")

    def test_target_must_be_ahead_and_known_by_break_start(self):
        self.assertEqual(self.candidate(high=109).status,"WATCH")
        s=setup();b=CausalLiquidityBook()
        b.register(sample_period(known_at=s.break_end,high=140))
        self.assertEqual(evaluate_break(s,b.levels(s.break_end)).status,"WATCH")

    def test_unsupported_confirmation_timeframe_not_accepted(self):
        self.assertEqual(self.candidate(setup(confirmation_tf_minutes=5,
            break_end=setup().break_start+timedelta(minutes=5))).status,"REJECT")

    def test_short_direction_geometry(self):
        s=setup(side="SHORT",parent_side="SHORT",protected_reference_price=105,
                previous_close=106,break_close=100,exact_far_wick_stop=115)
        p=sample_period(high=120,low=90);b=CausalLiquidityBook();b.register(p)
        d=evaluate_break(s,b.levels(s.break_start));self.assertEqual(d.status,"BREAK_CONFIRMED_AWAITING_OPEN")
        self.assertEqual(d.target,90)
        v=evaluate_next_open(d,99,d.decision_at)
        self.assertEqual(v.status,"RESEARCH_ENTRY_GEOMETRY_ONLY")

class LiquidityStateTests(unittest.TestCase):
    def test_sweep_reclaim_and_future_not_retroactive(self):
        p=sample_period(high=110);b=CausalLiquidityBook();b.register(p)
        t=datetime(2026,10,28,12,tzinfo=UTC)
        b.observe_completed_bar(MinuteBar(t,t+timedelta(minutes=1),109,111,106,108,"C1"))
        older={l.id:l.state for l in b.levels(t)}
        newer={l.id:l.state for l in b.levels(t+timedelta(minutes=1))}
        self.assertEqual(older["PD_ETH:2026-10-27:H"],"AVAILABLE")
        self.assertEqual(newer["PD_ETH:2026-10-27:H"],"SWEPT_RECLAIM")

    def test_completed_close_consumes_level(self):
        p=sample_period(high=110);b=CausalLiquidityBook();b.register(p)
        t=datetime(2026,10,28,12,tzinfo=UTC)
        b.observe_completed_bar(MinuteBar(t,t+timedelta(minutes=1),109,111,106,110.5,"C1"))
        high=next(l for l in b.levels(t+timedelta(minutes=1)) if l.side=="BUY_SIDE")
        self.assertEqual(high.state,"CLOSED_BEYOND")

    def test_unknown_coverage_blocks_old_levels(self):
        b=CausalLiquidityBook();b.register(sample_period())
        t=datetime(2026,10,28,12,tzinfo=UTC)
        b.note_coverage_unknown(t,"GAP_EVENT_1")
        self.assertEqual({l.state for l in b.levels(t)}, {"UNKNOWN_COVERAGE"})
        self.assertEqual({l.state for l in b.levels(t-timedelta(minutes=1))}, {"AVAILABLE"})
        self.assertEqual(evaluate_break(setup(),b.levels(setup().break_start)).status,"WATCH")

    def test_already_swept_level_not_a_fresh_destination(self):
        b=CausalLiquidityBook();b.register(sample_period(high=125))
        t=datetime(2026,10,28,12,tzinfo=UTC)
        b.observe_completed_bar(MinuteBar(t,t+timedelta(minutes=1),124,126,121,123,"SWEEP"))
        self.assertEqual(evaluate_break(setup(),b.levels(setup().break_start)).status,"WATCH")

if __name__=="__main__":
    unittest.main()
