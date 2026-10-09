"""Independent synthetic strict-clock and protected-external-reference probes."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('audited_engine', ROOT/'engine.py')
e = importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
SPEC = dict(id='SEPTEMBER_4H_1H_1M_V1',execution_allowed=False,test_fixture='INDEPENDENT_SYNTHETIC')
PRICES = [(100,101,99,100),(100,105,99.5,104),(104,111,103,110),
          (110,110.8,108,108.5),(108.5,109,101,102),(102,102.8,100,101),
          (101,106,100.5,105),(105,107,104,106),(106,116,105,115),
          (115,115.8,113,113.5),(113.5,114,106,107),(107,107.8,105,106),
          (106,111,105.5,110),(110,112,109,111),(111,121,110,120),
          (120,120.8,118,118.5),(118.5,119,112,113),(113,113.8,111,112),
          (112,116,111.5,115),(115,117,114,116),(116,126,115,125)]


def records(tf=60, direction='long', prices=PRICES):
    result=[]
    for i,(op,high,low,close) in enumerate(prices):
        if direction=='short':op,high,low,close=300-op,300-low,300-high,300-close
        result.append((tf,dict(time=i*tf*60,known_at=(i+1)*tf*60,open=op,high=high,
             low=low,close=close,complete=True,source_id=f'INDEPENDENT:{direction}:{tf}:{i}')))
    return result


def replay(rows, module=None):
    module = e if module is None else module
    obj=module.Engine(SPEC);obj.feed_batch(copy.deepcopy(rows));return obj.events


def bos(events):return [x for x in events if x['kind']=='MAJOR_BOS']


def p(kind,time,price,known):
    return dict(id=f'{kind}:{time}',kind=kind,time=time,known_at=known,price=price,
                label='SYNTHETIC',source_id=f's:{time}',comparator=None)


class IndependentBoundaries(unittest.TestCase):
    def test_public_bootstrap_and_external_continuation_survive_on_both_higher_timeframes(self):
        for tf in (60,240):
            for direction in ('long','short'):
                with self.subTest(tf=tf,direction=direction):
                    events=bos(replay(records(tf,direction)))
                    self.assertEqual([x['bos_type'] for x in events],['BOOTSTRAP_OR_REESTABLISHMENT','EXTERNAL_CONTINUATION'])
                    self.assertEqual([x['bar_start'] for x in events],[14*tf*60,20*tf*60])
                    for event in events:
                        self.assertEqual(event['direction'],direction)
                        self.assertLess(event['reference']['known_at'],event['bar_start'])
                        self.assertLess(event['pullback']['known_at'],event['bar_start'])

    def test_public_bootstrap_ref_at_open_and_progression_at_close_cannot_borrow_old_level(self):
        prices=PRICES[:11]+[(107,115,106.5,114.5)]
        for tf in (60,240):
            for direction in ('long','short'):
                with self.subTest(tf=tf,direction=direction):
                    self.assertEqual(bos(replay(records(tf,direction,prices))),[])

    def test_public_pullback_at_open_blocks_bootstrap_but_one_extra_bar_permits(self):
        for tf in (60,240):
            for direction in ('long','short'):
                with self.subTest(tf=tf,direction=direction):
                    prices=PRICES[:13]+[PRICES[14]]
                    self.assertEqual(bos(replay(records(tf,direction,prices))),[])
                    self.assertEqual(len(bos(replay(records(tf,direction,PRICES[:15])))),1)

    def test_external_known_time_and_pullback_known_time_grid(self):
        for direction in ('long','short'):
            kind,opposite=('high','low') if direction=='long' else ('low','high')
            for ref_known in (99,100,101):
                for pull_known in (99,100,101):
                    with self.subTest(direction=direction,ref=ref_known,pull=pull_known):
                        ref=p(kind,10,120 if direction=='long' else 180,ref_known)
                        pull=p(opposite,30,90 if direction=='long' else 210,pull_known)
                        inside=p(kind,50,110 if direction=='long' else 190,90)
                        result=e.association([ref,pull,inside],direction,100,ref,scope_external=True)
                        if ref_known<100 and pull_known<100:
                            self.assertEqual(result[0]['id'],ref['id'])
                            self.assertEqual(result[1]['id'],pull['id'])
                        else:self.assertIsNone(result)

    def test_explicit_old_internal_reference_never_bypasses_newer_directional_pivot(self):
        for direction in ('long','short'):
            kind,opposite=('high','low') if direction=='long' else ('low','high')
            ref=p(kind,10,120 if direction=='long' else 180,20)
            pull=p(opposite,30,90 if direction=='long' else 210,40)
            inside=p(kind,50,110 if direction=='long' else 190,60)
            with self.subTest(direction=direction):
                self.assertIsNone(e.association([ref,pull,inside],direction,100,ref))
                self.assertIsNone(e.association([ref,pull,inside],direction,100))
                self.assertIsNotNone(e.association([ref,pull,inside],direction,100,ref,scope_external=True))

    def test_fresh_engine_cold_replay_preserves_every_event_at_every_bar_cutoff(self):
        for tf in (60,240):
            for direction in ('long','short'):
                rows=records(tf,direction);full=replay(rows)
                self.assertEqual(len(bos(full)),2)
                for count,(_,bar) in enumerate(rows,1):
                    with self.subTest(tf=tf,direction=direction,count=count):
                        expected=[x for x in full if x['known_at']<=bar['known_at']]
                        self.assertEqual(replay(rows[:count]),expected)

    def test_future_reading_positive_control_is_rejected_by_prefix_comparison(self):
        rows=records();cut=15
        def poisoned(data):
            events=replay(data)
            for event in events:
                if event['kind']=='MAJOR_BOS':event['deliberate_future_close']=data[-1][1]['close']
            return events
        full=[x for x in poisoned(rows) if x['known_at']<=rows[cut-1][1]['known_at']]
        truncated=poisoned(rows[:cut])
        self.assertNotEqual(full,truncated)
        self.assertEqual(replay(rows[:cut]),[x for x in replay(rows) if x['known_at']<=rows[cut-1][1]['known_at']])


if __name__=='__main__':unittest.main()
