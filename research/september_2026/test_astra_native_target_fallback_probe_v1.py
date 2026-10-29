"""Synthetic invariant tests; no real-NQ trade or profitability claims."""
import unittest
from datetime import datetime, timezone, timedelta
from dataclasses import replace
from adaptive_market_paths_v1 import MinuteBar, PeriodEvidence, CausalLiquidityBook
from astra_native_target_fallback_probe_v1 import source_period_verified, review_native_return

UTC=timezone.utc
T=datetime(2026,10,28,12,0,tzinfo=UTC)
NAMESPACE="a"*64
DATA="NQ_NATIVE_RAW"
SEG="NQZ6_DATED"


def minute(t, high=100.5, low=99.5, segment=SEG):
    return MinuteBar(t,t+timedelta(minutes=1),100,high,low,100,
                     "NQ:1:"+str(int(t.timestamp())),DATA,segment)


def native(target=None, m1=True, m5=True, reasons=None, t=T):
    sec=int(t.timestamp())
    def state(): return dict(known_at=sec, parent_id="P", contact_id="H", direction="long")
    context=dict(asof=sec, parent=dict(parent_id="P"),contact=dict(contact_id="H"),
       target=target, support_states={60:state() if m1 else None,
                                     300:state() if m5 else None},
       reasons=(["NO_RELEVANT_OPPOSING_TARGET"] if target is None else [])
               if reasons is None else reasons)
    packet=dict(candidate_id="C",parent=dict(parent_id="P",direction="long"),
       contact=dict(contact_id="H"),formation_result=dict(candidate=dict(
       proximal=100,zone_low=98,zone_high=101)))
    event=dict(kind="CANDIDATE_RETURN",candidate_id="C",known_at=sec,
       bar=dict(time=sec-60,known_at=sec,close=100,source_id="NQ:1:"+str(sec-60)))
    return event,context,packet


def run(t=T, target=None, catalog=None, candidate_zone=None,
        context=None,packet=None, **extra):
    ev,ctx,p=native(target=target,t=t)
    return review_native_return(
      source_namespace=NAMESPACE,event=ev,context=ctx if context is None else context,
      packet=p if packet is None else packet,catalog=[] if catalog is None else catalog,
      candidate_zone=candidate_zone or dict(direction="long",low=98,high=101),
      dataset_id=DATA,contract_segment_id=SEG,**extra)


def premarket(t=T):
    start=t-timedelta(minutes=300)
    p=PeriodEvidence(kind="PREMARKET_0300_0800",period_id="2026-10-28",
       start=start,end=t,known_at=t,high=105,low=99.5,
       source_id="PERIOD:SYNTHETIC",coverage_complete=True,
       dataset_id=DATA,contract_segment_id=SEG)
    bars=[minute(start+timedelta(minutes=i),high=105 if i==0 else 100.5)
          for i in range(300)]
    book=CausalLiquidityBook();book.register(p)
    return p,bars,book.levels(t)


class ProbeTests(unittest.TestCase):
 def test_targetless_no_minutes_rejects(self):
    a=run()
    self.assertEqual(a["status"],"NO_VERIFIED_TARGET")
    self.assertFalse(a["execution_allowed"])
    self.assertTrue(a["original_exit_rules_unchanged"])

 def test_canonical_priority_preserved(self):
    z=dict(zone_id="Z",direction="short",low=110,high=112,
       known_at=int(T.timestamp())-60,valid=True,target_valid=True,timeframe_seconds=900)
    target=dict(zone_id="Z",price=110,known_at=z["known_at"],valid=True)
    a=run(target=target,catalog=[z])
    self.assertEqual((a["status"],a["target_id"],a["target_price"]),
                     ("CANONICAL_ZONE_UNCHANGED","Z",110))

 def test_unverified_m5_blocks(self):
    ev,c,p=native();c["support_states"][300]=None
    a=run(context=c)
    self.assertEqual(a["status"],"BLOCKED_NATIVE_ENTRY_GATE")

 def test_unrelated_parent_blocks(self):
    ev,c,p=native();c["parent"]["parent_id"]="OTHER"
    self.assertEqual(run(context=c)["status"],"BLOCKED_NATIVE_ENTRY_GATE")

 def test_extra_native_veto_blocks(self):
    ev,c,p=native(reasons=["NO_RELEVANT_OPPOSING_TARGET","CANDIDATE_INVALIDATED"])
    self.assertEqual(run(context=c)["status"],"BLOCKED_NATIVE_ENTRY_GATE")

 def test_missing_canonical_veto_contradiction(self):
    ev,c,p=native(reasons=[])
    with self.assertRaisesRegex(ValueError,"selector and reason"):
        run(context=c)

 def test_misbound_origin_farwick_rejected(self):
    with self.assertRaisesRegex(ValueError,"far-wick"):
        run(candidate_zone=dict(direction="long",low=96,high=101))

 def test_unknown_candidate_identity_rejected(self):
    ev,c,p=native();p["candidate_id"]="OTHER"
    with self.assertRaisesRegex(ValueError,"Candidate identity"):
        run(packet=p)

 def test_premarket_300_complete_minutes_verified(self):
    p,bars,lv=premarket()
    self.assertTrue(source_period_verified(p,bars))
    a=run(source_periods=(p,),liquidity_levels=tuple(lv),
          raw_period_minutes={(p.kind,p.period_id):tuple(bars)})
    self.assertEqual((a["status"],a["target_price"],a["source_periods_verified"]),
                     ("LIQUIDITY_FALLBACK",105,1))
    self.assertFalse(a["execution_allowed"])

 def test_truncated_premarket_rejected(self):
    p,bars,lv=premarket()
    self.assertFalse(source_period_verified(p,bars[:-1]))
    a=run(source_periods=(p,),liquidity_levels=tuple(lv),
          raw_period_minutes={(p.kind,p.period_id):tuple(bars[:-1])})
    self.assertEqual(a["status"],"NO_VERIFIED_TARGET")

 def test_renamed_wrong_contract_rejected(self):
    p,bars,lv=premarket()
    bars[22]=replace(bars[22],contract_segment_id="NQH6")
    self.assertFalse(source_period_verified(p,bars))

 def test_changed_recorded_high_rejected(self):
    p,bars,lv=premarket()
    bad=replace(p,high=106)
    self.assertFalse(source_period_verified(bad,bars))

 def test_two_minute_named_previous_day_not_authenticated(self):
    p=PeriodEvidence("PD_ETH","D",T-timedelta(minutes=2),T,T,105,99.5,
                     "BAD_NAME_ONLY",True,DATA,SEG)
    b=[minute(T-timedelta(minutes=2),high=105),minute(T-timedelta(minutes=1))]
    self.assertFalse(source_period_verified(p,b))

 def test_missing_after_knowledge_blocks(self):
    p,b,lv=premarket()
    a=run(t=T+timedelta(minutes=1),source_periods=(p,),liquidity_levels=tuple(lv),
          raw_period_minutes={(p.kind,p.period_id):tuple(b)})
    self.assertEqual(a["status"],"NO_VERIFIED_TARGET")

 def test_touch_after_knowledge_blocks(self):
    p,b,lv=premarket()
    a=run(t=T+timedelta(minutes=1),source_periods=(p,),liquidity_levels=tuple(lv),
          raw_period_minutes={(p.kind,p.period_id):tuple(b)},
          known_completed_minutes=(minute(T,high=106),))
    self.assertEqual(a["status"],"NO_VERIFIED_TARGET")

 def test_premarket_future_seal_not_a_target(self):
    p,b,lv=premarket()
    a=run(t=T-timedelta(minutes=1),source_periods=(p,),liquidity_levels=tuple(lv),
          raw_period_minutes={(p.kind,p.period_id):tuple(b)})
    self.assertEqual(a["status"],"NO_VERIFIED_TARGET")


if __name__=="__main__":
 unittest.main(verbosity=2)
