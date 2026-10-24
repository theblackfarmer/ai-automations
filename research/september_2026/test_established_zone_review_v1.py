"""Synthetic controls. Actual saved-case regression is a separate local replay."""
import copy
import unittest
from established_zone_review_v1 import EstablishedZoneReview
from entry_review_adapter_v1 import candidates
S='a'*64

def bar(t,o,h,l,c):
 return dict(time=t,known_at=t+300,open=o,high=h,low=l,close=c,source_id=f'{S}:5:{t}',symbol='TEST',feed='SYNTHETIC',complete=True)
BASE=[bar(0,103,104,100,102),bar(300,102,106,101,105),bar(600,105,105,102,103),bar(900,103,104,98,99)]
TAIL=[bar(1200,99,100,97,98),bar(1500,98,103,97,102),bar(1800,102,107,100,100.5),bar(2100,100.5,104,99,100),bar(2400,100,102,98,99),bar(2700,99,100,97,98)]
KW=dict(direction='short',origin_times=[300],reference_time=0,reference_known_at=300,break_time=900,selection_source='synthetic formation',parent_id='unknown')
def runit(bs=None):
 obs=EstablishedZoneReview(S,'TEST','SYNTHETIC')
 for b in BASE+TAIL if bs is None else bs:
  obs.feed_bar(b)
  if b['time']==900:obs.establish_zone(**KW)
 return obs
def taps(e):return [x for x in e if x['kind']=='ESTABLISHED_ZONE_TAP_OBSERVED']
class EstablishedZoneTests(unittest.TestCase):
 def test_every_overlap_no_new_break_or_trade_promotion(self):
  e=runit().events;t=taps(e)
  self.assertEqual([x['bar_start'] for x in t],[1500,1800,2100,2400])
  self.assertEqual([x['tap_sequence'] for x in t],[1,2,3,4])
  self.assertTrue(all(x['established_break_known_at']==1200 and not x['new_break_required'] for x in t))
  self.assertEqual(candidates(e,{}),([],[]))
  self.assertTrue(all(not x['execution_allowed'] and not x['fill_verified'] for x in e))
 def test_breach_does_not_erase_later_review(self):
  t=taps(runit().events)
  self.assertTrue(t[1]['same_bar_far_edge_crossed']);self.assertTrue(t[2]['any_far_edge_crossing_observed'])
  self.assertFalse(t[2]['entry_validated']);self.assertFalse(t[2]['same_bar_far_edge_crossed'])
 def test_break_bar_not_return(self):
  self.assertEqual([x['kind'] for x in runit(BASE).events],['ESTABLISHED_ZONE_REGISTERED'])
 def test_all_prefixes_and_future_changes(self):
  bs=BASE+TAIL;full=runit(bs).events
  for i,b in enumerate(bs):self.assertEqual(runit(bs[:i+1]).events,[x for x in full if x['known_at']<=b['known_at']])
  changed=copy.deepcopy(bs);changed[-1]['high']=200
  self.assertEqual([e for e in runit(changed).events if e['known_at']<=2700],[e for e in full if e['known_at']<=2700])
 def test_actual_tap_future_leak_control(self):
  def leak(bs):return [x for x in runit(bs).events if x['known_at']<=2100 and (x['kind']!='ESTABLISHED_ZONE_TAP_OBSERVED' or bs[-1]['high']>102)]
  self.assertNotEqual(leak(BASE+TAIL),leak((BASE+TAIL)[:7]))
 def test_gap_blocks_context(self):
  obs=runit(BASE+TAIL[:2]);obs.feed_bar(TAIL[3]);obs.feed_bar(TAIL[4])
  self.assertEqual(len(taps(obs.events)),1);self.assertTrue(any(x.get('reason')=='UNOBSERVED_NATIVE_BAR' for x in obs.events))
 def test_explicit_invalidation(self):
  obs=runit(BASE+TAIL[:2]);z=obs.events[0]['zone_id'];obs.invalidate_zone(z,1800,'owner invalidation');obs.feed_bar(TAIL[2])
  self.assertEqual(len(taps(obs.events)),1)
  with self.assertRaises(ValueError):obs.invalidate_zone(z,1500,'backdate')
 def test_bad_native_inputs(self):
  for k,v in [('known_at',301),('complete',False),('source_id','wrong'),('symbol','OTHER'),('feed','OTHER'),('high',float('nan')),('time',True)]:
   obs=EstablishedZoneReview(S,'TEST','SYNTHETIC');b=copy.deepcopy(BASE[0]);b[k]=v
   with self.assertRaises(ValueError):obs.feed_bar(b)
   self.assertEqual(obs.events,[])
 def test_bad_registration_clocks(self):
  for update in [dict(break_time=600),dict(reference_known_at=1200),dict(origin_times=[1200]),dict(reference_time=1200)]:
   obs=EstablishedZoneReview(S,'TEST','SYNTHETIC')
   for b in BASE:obs.feed_bar(b)
   with self.assertRaises(ValueError):obs.establish_zone(**dict(KW,**update))
 def test_no_break_blocks_registration(self):
  obs=EstablishedZoneReview(S,'TEST','SYNTHETIC');bs=copy.deepcopy(BASE);bs[-1]['close']=102
  for b in bs:obs.feed_bar(b)
  with self.assertRaises(ValueError):obs.establish_zone(**KW)
 def test_duplicate_and_mutation_guards(self):
  obs=runit(BASE);e=obs.events;e[0]['zone_low']=1
  self.assertEqual(obs.events[0]['zone_low'],101)
  with self.assertRaises(ValueError):obs.feed_bar(BASE[-1])
  with self.assertRaises(ValueError):obs.establish_zone(**KW)
 def test_synthetic_mirror(self):
  bs=[dict(b,open=300-b['open'],high=300-b['low'],low=300-b['high'],close=300-b['close']) for b in BASE+TAIL]
  obs=EstablishedZoneReview(S,'TEST','SYNTHETIC')
  for b in bs:
   obs.feed_bar(b)
   if b['time']==900:obs.establish_zone(**dict(KW,direction='long'))
  self.assertEqual([x['known_at'] for x in taps(obs.events)],[x['known_at'] for x in taps(runit().events)])
if __name__=='__main__':unittest.main()
