"""Causal coverage accounting tests; all prices are synthetic, never fills."""
import copy
import unittest
from engine import Engine
import month_replay as replay
from test_engine import SPEC, bar, bootstrap_boundary_records


class GapRecoveryTests(unittest.TestCase):
    def plan(self,second_gap=False):
        start=replay.epoch('2026-09-08T00:00:00Z')
        end=start+12*3600
        rows={t:dict(time=t,open=100.,high=102.,low=99.,close=101.)
              for t in range(start,end,60)}
        for t in range(start+4*3600,start+5*3600,60):del rows[t]
        if second_gap:del rows[start+7*3600]
        plan,_=replay.assemble(rows,start,end,'synthetic')
        return start,end,plan

    def run_plan(self,plan):
        replay.validate_plan(plan)
        engine=Engine(SPEC)
        for known,gaps,bars in plan:
            for gap in gaps:
                if isinstance(gap,dict):
                    engine.gap(known,'DOCUMENTED_REGULAR_CLOSURE',scheduled=True,schedule_evidence=gap)
                else:engine.gap(known,gap)
            engine.feed_batch(bars)
        return engine

    def test_delayed_h4_preserves_recovered_lower_timeframes(self):
        start,end,plan=self.plan()
        engine=self.run_plan(plan)
        self.assertEqual(len(engine._track[60].bars),7)
        self.assertEqual(len(engine._track[1].bars),420)
        gaps=[e for e in engine.events if e['kind']=='UNCLASSIFIED_COVERAGE_GAP']
        self.assertEqual([(e['known_at'],e['reason']) for e in gaps],[(start+4*3600+60,'MISSING_ACTIVE_MINUTE')])
        self.assertFalse(any(e['execution_allowed'] for e in engine.events))

    def test_second_missing_episode_still_resets(self):
        start,end,plan=self.plan(True)
        engine=self.run_plan(plan)
        gaps=[e for e in engine.events if e['kind']=='UNCLASSIFIED_COVERAGE_GAP']
        self.assertEqual([e['known_at'] for e in gaps],[start+4*3600+60,start+7*3600+60])
        self.assertEqual(engine._track[1].bars[0]['time'],start+7*3600+60)
        self.assertEqual(len(engine._track[60].bars),4)

    def test_legacy_unexplained_gap_retains_failclosed_reset(self):
        start,end,plan=self.plan()
        for _,_,bars in plan:
            for _,b in bars:b.pop('gap_evidence',None)
        engine=self.run_plan(plan)
        self.assertEqual(len(engine._track[60].bars),1)
        self.assertEqual(len(engine._track[1].bars),1)
        self.assertTrue(any(e.get('reason')=='NATIVE_TIMESTAMP_DISCONTINUITY' for e in engine.events))

    def test_prefix_replay_matches_full_before_each_recovery(self):
        _,_,plan=self.plan()
        full=self.run_plan(plan).events
        cutoffs=[known for known,_,bars in plan if any('gap_evidence' in b for _,b in bars)]
        self.assertTrue(cutoffs)
        for cutoff in cutoffs:
            prefix=self.run_plan([a for a in plan if a[0]<=cutoff]).events
            self.assertEqual(prefix,[e for e in full if e['known_at']<=cutoff])

    def test_corrupt_proof_rejected_before_state_mutation(self):
        _,_,plan=self.plan()
        for mutation in ('future','missing','unknown_over_observed','reason','scheduled'):
            mutated=copy.deepcopy(plan)
            target=next(b for _,_,bars in mutated for tf,b in bars if tf==240 and 'gap_evidence' in b)
            proof=target['gap_evidence']
            if mutation=='future':proof['end_exclusive']+=60
            elif mutation=='missing':proof['observed_minutes'].pop()
            elif mutation=='unknown_over_observed':
                proof['unknown_intervals'][0]['end_exclusive']=proof['end_exclusive']
                proof['observed_minutes']=[]
            elif mutation=='reason':proof['unknown_intervals'][0]['reason']='invented'
            else:target['gap_before']='scheduled'
            engine=Engine(SPEC)
            rejected=False
            for known,gaps,bars in mutated:
                for gap in gaps:engine.gap(known,gap)
                for tf,b in bars:
                    before=engine.snapshot()
                    try:engine.feed(tf,b)
                    except ValueError:
                        self.assertEqual(engine.snapshot(),before);rejected=True;break
                if rejected:break
            self.assertTrue(rejected,mutation)
            with self.assertRaises(ValueError):replay.validate_plan(mutated)

    def test_completed_bar_cannot_span_known_missing_minute(self):
        engine=Engine(SPEC);engine.gap(60,'MISSING_ACTIVE_MINUTE')
        before=engine.snapshot()
        with self.assertRaisesRegex(ValueError,'known unavailable'):
            engine.feed(240,bar(240,0,(10,11,9,10)))
        self.assertEqual(engine.snapshot(),before)

    def test_accounted_resume_never_resurrects_authority(self):
        records=bootstrap_boundary_records('long','valid_after_wait')
        engine=Engine(SPEC);engine.feed_batch(records)
        aid=engine.snapshot()['active_authorities'][60]
        self.assertIsNotNone(aid)
        end=records[-1][1]['known_at']
        engine.gap(end+60,'MISSING_ACTIVE_MINUTE')
        resumed=bar(60,end+3600,(18.5,19,18,18.5))
        resumed['gap_evidence']=dict(start=end,end_exclusive=end+3600,observed_minutes=[],scheduled_intervals=[],
            unknown_intervals=[dict(start=end,end_exclusive=end+3600,reason='MISSING_ACTIVE_MINUTE',reset_known_at=end+60)])
        engine.feed(60,resumed)
        snapshot=engine.snapshot()
        self.assertIsNone(snapshot['active_authorities'][60])
        self.assertFalse(snapshot['authorities'][aid]['active'])

    def test_second_gap_cannot_borrow_reset_across_h1_only_recovery(self):
        engine=Engine(SPEC);engine.feed(240,bar(240,0,(10,11,9,10)))
        engine.gap(14460,'MISSING_ACTIVE_MINUTE')
        engine.feed(60,bar(60,18000,(10,11,9,10)))
        resumed=bar(240,28800,(10,11,9,10))
        resumed['gap_evidence']=dict(start=14400,end_exclusive=28800,observed_minutes=[],scheduled_intervals=[],
            unknown_intervals=[dict(start=14400,end_exclusive=28800,reason='MISSING_ACTIVE_MINUTE',reset_known_at=14460)])
        with self.assertRaisesRegex(ValueError,'observed recovery'):engine.feed(240,resumed)

    def test_unknown_span_cannot_swallow_evidenced_closure(self):
        engine=Engine(SPEC);engine.feed(60,bar(60,0,(10,11,9,10)))
        engine.gap(3660,'MISSING_ACTIVE_MINUTE')
        engine.gap(5400,'DOCUMENTED_REGULAR_CLOSURE',scheduled=True,
                   schedule_evidence=dict(start=4200,end_exclusive=5400))
        resumed=bar(60,7200,(10,11,9,10))
        resumed['gap_evidence']=dict(start=3600,end_exclusive=7200,observed_minutes=[],scheduled_intervals=[],
            unknown_intervals=[dict(start=3600,end_exclusive=7200,reason='MISSING_ACTIVE_MINUTE',reset_known_at=3660)])
        with self.assertRaisesRegex(ValueError,'evidenced closure'):engine.feed(60,resumed)

if __name__=='__main__':unittest.main()
