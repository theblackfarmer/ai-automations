"""Synthetic integration checks; saved market examples are separately replayed."""
import unittest
from copy import deepcopy
from autonomous_zone_review_v1 import AutonomousZoneReview
from test_established_zone_review_v1 import bar, S
from entry_review_adapter_v1 import candidates

ROWS=[(110,112,108,111),(111,111,106,107),(107,113,107,112),
      (110,110,105,106),(106,112,106,111),(110,111,106,108),
      (108,109,103,104),(104,107,103,106),(106,114,104,105),
      (105,110,104,106),(106,107,102,103)]
BARS=[bar(i*300,*r) for i,r in enumerate(ROWS)]
def run(bs):
 d=AutonomousZoneReview(S,'TEST','SYNTHETIC')
 for b in bs:d.feed_bar(b)
 return d

def selected(events,kind):return [e for e in events if e['kind']==kind]

class AutomaticTests(unittest.TestCase):
 def test_automatic_origin_reference_and_all_taps(self):
  e=run(BARS).events;z=selected(e,'ESTABLISHED_ZONE_REGISTERED')
  self.assertEqual(len(z),1)
  self.assertEqual((z[0]['origin_times'],z[0]['reference_time'],z[0]['break_time']),([1200],900,1800))
  self.assertEqual((z[0]['zone_low'],z[0]['zone_high']),(106,112))
  self.assertEqual([t['bar_start'] for t in selected(e,'ESTABLISHED_ZONE_TAP_OBSERVED')],[2100,2400,2700,3000])
  self.assertEqual(candidates(e,{}),([],[]))
  self.assertTrue(all(e['execution_allowed'] is False for e in e))
 def test_every_prefix(self):
  full=run(BARS).events
  for i,b in enumerate(BARS):
   self.assertEqual(run(BARS[:i+1]).events,[e for e in full if e['known_at']<=b['known_at']])
 def test_deliberate_signal_leak_fails(self):
  def leak(bs):return [e for e in run(bs).events if e['kind']!='ESTABLISHED_ZONE_TAP_OBSERVED' or bs[-1]['close']>104]
  self.assertNotEqual(leak(BARS[:8]),[e for e in leak(BARS) if e['known_at']<=2400])
 def test_no_teacher_fields_enter_state(self):
  bs=[dict(b,teacher_zone=[1,999],confirmation='B',selected_origin=0) for b in BARS]
  self.assertEqual(run(bs).events,run(BARS).events)
 def test_mirrored_demand(self):
  bs=[dict(b,open=300-b['open'],high=300-b['low'],low=300-b['high'],close=300-b['close']) for b in BARS]
  e=run(bs).events;z=selected(e,'ESTABLISHED_ZONE_REGISTERED')
  self.assertEqual(z[0]['direction'],'long')
  self.assertEqual([x['known_at'] for x in selected(e,'ESTABLISHED_ZONE_TAP_OBSERVED')],[2400,2700,3000,3300])
 def test_missing_bar_blocks_established_zone(self):
  e=run(BARS[:8]+BARS[9:]).events
  self.assertEqual(len(selected(e,'ESTABLISHED_ZONE_TAP_OBSERVED')),1)
  self.assertEqual(len(selected(e,'AUTOMATIC_COVERAGE_RESET')),1)
 def test_duplicate_rejected_without_mutation(self):
  d=run(BARS);before=d.events
  with self.assertRaises(ValueError):d.feed_bar(BARS[-1])
  self.assertEqual(before,d.events)
 def test_same_reference_recross_not_new_zone(self):
  e=run(BARS+[bar(3300,103,107,102,106),bar(3600,106,107,103,104)]).events
  z=selected(e,'ESTABLISHED_ZONE_REGISTERED')
  keys=[(x['direction'],tuple(x['origin_times']),x['reference_time']) for x in z]
  self.assertEqual(len(keys),len(set(keys)))
 def test_registration_identity_matches_published_content(self):
  from engine import ident
  for e in selected(run(BARS).events,"ESTABLISHED_ZONE_REGISTERED"):
   content=dict(e);old=content.pop("id")
   self.assertEqual(old,ident(content["kind"],content))
 def test_incremental_event_copy_is_isolated(self):
  d=run(BARS);before=d.review.events
  rows=d.review.events_since(0);rows[0]['zone_low']=-1
  self.assertEqual(d.review.events,before)
  self.assertEqual(d.review.events_since(len(before)),[])
  with self.assertRaises(ValueError):d.review.events_since(-1)
if __name__=='__main__':unittest.main()
