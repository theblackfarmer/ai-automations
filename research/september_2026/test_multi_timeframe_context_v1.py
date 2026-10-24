import unittest
from copy import deepcopy
from multi_timeframe_context_v1 import MultiTimeframeContext
from test_autonomous_zone_review_v1 import BARS,S


def tape():
    fifteen=[dict(b,time=b['time']*3,known_at=b['time']*3+900,
                  source_id=f'{S}:15:{b["time"]*3}') for b in BARS]
    return sorted([(300,b) for b in BARS]+[(900,b) for b in fifteen],
                  key=lambda row:(row[1]['known_at'],row[0]))


def run(rows):
    c=MultiTimeframeContext({300:S,900:S},'TEST','SYNTHETIC')
    for step,b in rows:c.feed_bar(step,b)
    return c


class ContextTests(unittest.TestCase):
    def test_separate_zones_and_clocks(self):
        c=run(tape());s=c.snapshot()
        for step in (300,900):
            self.assertEqual(len(s['timeframes'][step]['local_zone_proposals']),1)
            self.assertTrue(all(p['known_at']<=s['known_at'] for p in s['timeframes'][step]['pivots']))
        self.assertFalse(s['execution_allowed'])
        self.assertEqual(s['major_trend'],'NOT_AUTONOMOUSLY_VERIFIED')
    def test_5m_gap_does_not_clear_15m(self):
        c=run(tape());before=deepcopy(c.snapshot()['timeframes'][900])
        b=dict(BARS[-1],time=9900,known_at=10200,source_id=f'{S}:5:9900')
        c.feed_bar(300,b)
        self.assertEqual(c.snapshot()['timeframes'][900],before)
        self.assertEqual(c.snapshot()['timeframes'][300]['local_zone_proposals'],[])
    def test_replay_prefix_snapshots(self):
        c=MultiTimeframeContext({300:S,900:S},'TEST','SYNTHETIC')
        rows=tape()
        for i,(step,b) in enumerate(rows):
            c.feed_bar(step,b)
            self.assertEqual(c.snapshot(),run(rows[:i+1]).snapshot())
    def test_snapshot_discloses_boundary_and_stream_lag(self):
        s=run(tape()).snapshot()
        self.assertEqual(s['timeframes'][300]['coverage_status'],'LAGGING_OR_MISSING')
        self.assertEqual(s['timeframes'][900]['coverage_status'],'CURRENT_COMPLETE_BAR')
        for step in (300,900):
            zone=s['timeframes'][step]['local_zone_proposals'][0]
            self.assertGreater(zone['activity']['boundary_crossing_count'],0)
            self.assertIsNotNone(zone['activity']['last_overlap'])
            self.assertIsNotNone(zone['activity']['last_boundary_crossing'])
    def test_backdated_other_timeframe_blocked(self):
        c=run(tape());before=c.snapshot()
        with self.assertRaises(ValueError):c.feed_bar(300,BARS[0])
        self.assertEqual(before,c.snapshot())
    def test_snapshot_and_native_source_isolation(self):
        c=run(tape());s=c.snapshot();s['timeframes'][900]['pivots'].clear()
        self.assertTrue(c.snapshot()['timeframes'][900]['pivots'])
        b=dict(BARS[-1],source_id='wrong')
        with self.assertRaises(ValueError):c.feed_bar(900,b)
if __name__=='__main__':unittest.main()
