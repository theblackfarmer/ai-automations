"""Synthetic checks of evidence preservation, never trade qualification."""
import unittest
from copy import deepcopy
from test_established_zone_review_v1 import bar, S
from autonomous_zone_review_v1 import AutonomousZoneReview
from entry_review_adapter_v1 import candidates

ROWS = [(35,49,21,36),(35,36,2,15),(15,23,1,3),
        (3,20,.5,1),(1,23,.4,.8),(.8,35,.3,30),
        (30,34,16,17),(17,24,12,18),(18,20,3,8)]

def replay(rows=ROWS):
    obj=AutonomousZoneReview(S,'TEST','SYNTHETIC')
    for i,row in enumerate(rows):obj.feed_bar(bar(i*300,*row))
    return obj

class OriginEvidenceTests(unittest.TestCase):
    def test_small_green_retained_without_swing_turn(self):
        d=replay(); records=d.origins.snapshot()
        self.assertEqual([r['origin_time'] for r in records],[0,1200,2100])
        self.assertEqual((records[0]['zone_low'],records[0]['zone_high']),(21,49))
        self.assertEqual(records[0]['known_at'],600)
        self.assertEqual(candidates(d.events,{}),([],[]))
        self.assertTrue(all(e['execution_allowed'] is False for e in d.events))
        self.assertTrue(all(r['structure_verified'] is False for r in records))
    def test_all_overlaps_not_only_visually_large_return(self):
        d=replay(); first=d.origins.snapshot()[0]['origin_id']
        self.assertEqual([e['bar_start'] for e in d.events if
            e['kind']=='ORIGIN_EVIDENCE_OVERLAP' and e['origin_id']==first],
            [600,1200,1500,1800,2100])
    def test_mirror(self):
        d=replay([(100-o,100-l,100-h,100-c) for o,h,l,c in ROWS])
        self.assertEqual([r['direction'] for r in d.origins.snapshot()],['long','short','long'])
        self.assertEqual(d.origins.snapshot()[0]['zone_low'],51)
    def test_prefix_and_future_control(self):
        def check_prefixes(detector):
            full = detector(ROWS)
            for n in range(1,len(ROWS)+1):
                self.assertEqual(detector(ROWS[:n]),
                                 [e for e in full if e['known_at']<=n*300])
        check_prefixes(lambda rows: replay(rows).events)
        # Inject future-dependent suppression of an already-emitted origin.
        def broken(rows):
            events = replay(rows).events
            later = any(e['kind']=='ORIGIN_EVIDENCE_RECORDED' and
                        e['record']['origin_time']==2100 for e in events)
            return [e for e in events if not (later and
                    e['kind']=='ORIGIN_EVIDENCE_RECORDED' and
                    e['record']['origin_time']==0)]
        with self.assertRaises(AssertionError):
            check_prefixes(broken)
    def test_gap_blocks_without_mutating_prior_snapshot(self):
        d=replay(ROWS[:2]); saved=d.origins.snapshot();d.feed_bar(bar(1200,15,23,1,3))
        self.assertEqual(d.origins.snapshot(),[])
        self.assertEqual(saved[0]['origin_time'],0)
        self.assertTrue(any(e['kind']=='ORIGIN_EVIDENCE_BLOCKED' for e in d.events))
    def test_unclosed_or_invalid_bar_cannot_mutate_evidence(self):
        d=replay(ROWS[:2]);old=deepcopy(d.events)
        b=bar(600,15,23,1,3);b['complete']=False
        with self.assertRaises(ValueError):d.feed_bar(b)
        self.assertEqual(d.events,old)
    def test_snapshot_mutation_cannot_replace_origin(self):
        d=replay();s=d.origins.snapshot();s[0]['origin_time']=999
        self.assertEqual(d.origins.snapshot()[0]['origin_time'],0)

if __name__=='__main__':unittest.main()
