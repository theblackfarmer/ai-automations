"""Real exposed-source regression plus explicitly synthetic mutation controls."""
import copy
import hashlib
import inspect
import json
from pathlib import Path
import unittest
from sourced_correction_v1 import SourcedCorrectionObserver, resolve_outside_reversal, correction_turn
from entry_review_adapter_v1 import candidates

FIXTURE=Path(__file__).parent/'fixtures/accepted_jan20_source.json'


def inputs(fixture):
    refs=fixture['context_reference_bars'];p=refs['1H_C']
    context=dict(direction='long',four_hour=refs['4H_A'],one_hour=refs['1H_rebound'],
        opposing_zone=refs['4H_supply_origin_candidate'],
        parent=dict(p,id=p['source_id'],timeframe=60,zone_low=p['low'],zone_high=p['high']))
    source=fixture['source']['sha256']
    bars=[dict(b,known_at=b['time']+60,complete=True,source_id=f'{source}:1:{b["time"]}')
          for b in fixture['minute_bars']]
    return context,source,bars


class SourcedCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads(FIXTURE.read_text())
        self.context,self.source,self.bars=inputs(self.fixture)

    def runit(self,bars=None,context=None):
        observer=SourcedCorrectionObserver(self.context if context is None else context,self.source)
        for bar in self.bars if bars is None else bars:observer.feed_minute(bar)
        return observer

    def test_real_source_derives_original_reference_origin_and_later_response(self):
        events=self.runit().events;expected=self.fixture['expected']
        flips=[e for e in events if e['kind']=='CORRECTION_FLIP_CANDIDATE']
        self.assertEqual(len(flips),1)
        e=flips[0]
        self.assertEqual(e['reference']['time'],expected['5M_lower_high_reference']['time'])
        self.assertEqual(e['reference']['price'],expected['5M_lower_high_reference']['high'])
        self.assertLessEqual(e['reference']['known_at'],e['bar_start'])
        self.assertEqual(e['origin']['time'],expected['5M_LL_origin']['time'])
        self.assertEqual(e['origin']['price'],expected['5M_LL_origin']['low'])
        self.assertEqual(e['bar_start'],expected['5M_close_break']['time'])
        self.assertEqual(e['known_at'],expected['5M_close_break']['known_at'])
        self.assertEqual(e['zone_low'],expected['5M_LL_origin']['low'])
        self.assertEqual(e['zone_high'],expected['5M_LL_origin']['high'])
        for kind,key in [('SAME_ORIGIN_RETURN_OBSERVED','5M_postbreak_return'),
                         ('SAME_ORIGIN_RESPONSE_OBSERVED','5M_response')]:
            match=[x for x in events if x['kind']==kind]
            self.assertEqual(len(match),1)
            self.assertEqual(match[0]['bar_start'],expected[key]['time'])
            self.assertEqual(match[0]['candidate_id'],e['candidate_id'])
            self.assertEqual((match[0]['zone_low'],match[0]['zone_high']),(e['zone_low'],e['zone_high']))
        self.assertTrue(all(e['execution_allowed'] is False and e['fill_verified'] is False for e in events))
        self.assertEqual(candidates(events,{}),([],[]))

    def test_expected_oracle_never_enters_detector(self):
        changed=copy.deepcopy(self.fixture)
        changed['expected']={'bogus_answer':{'price':999999,'time':1}}
        context,source,bars=inputs(changed)
        self.assertEqual(self.runit(bars,context).events,self.runit().events)
        context['reference']={'price':1}
        with self.assertRaises(ValueError):SourcedCorrectionObserver(context,source)

    def test_flip_prefix_needs_no_second_return_or_future_hour(self):
        cutoff=self.fixture['expected']['5M_close_break']['known_at']
        prefix=self.runit([b for b in self.bars if b['known_at']<=cutoff]).events
        self.assertEqual(sum(e['kind']=='CORRECTION_FLIP_CANDIDATE' for e in prefix),1)
        self.assertFalse(any(e['kind']=='SAME_ORIGIN_RETURN_OBSERVED' for e in prefix))
        bad=copy.deepcopy(self.context);bad['one_hour']['known_at']=cutoff+1
        with self.assertRaises(ValueError):self.runit(context=bad)

    def test_every_event_prefix_and_future_suffix_invariance(self):
        full=self.runit().events
        for cutoff in sorted({e['known_at'] for e in full}):
            self.assertEqual(self.runit([b for b in self.bars if b['known_at']<=cutoff]).events,
                             [e for e in full if e['known_at']<=cutoff])
        cutoff=self.fixture['expected']['5M_close_break']['known_at']
        altered=copy.deepcopy(self.bars)
        for b in altered:
            if b['time']>=cutoff:b['high']+=100
        self.assertEqual([e for e in self.runit(altered).events if e['known_at']<=cutoff],
                         [e for e in full if e['known_at']<=cutoff])

    def test_prefix_control_detects_deliberately_injected_future_dependency(self):
        cutoff=self.fixture['expected']['5M_close_break']['known_at']
        def leaky(bars):
            events=[e for e in self.runit(bars).events if e['known_at']<=cutoff]
            next(e for e in events if e['kind']=='CORRECTION_FLIP_CANDIDATE')['zone_high']=bars[-1]['high']
            return events
        with self.assertRaises(AssertionError):
            self.assertEqual(leaky(self.bars),leaky([b for b in self.bars if b['known_at']<=cutoff]))

    def test_gap_invalidates_context_and_cannot_resume_stale_parent(self):
        bars=[b for b in self.bars if b['time']!=1674209460]
        events=self.runit(bars).events
        self.assertTrue(any(e['kind']=='SUPPLIED_CONTEXT_INVALIDATED' for e in events))
        self.assertFalse(any(e['kind']=='CORRECTION_FLIP_CANDIDATE' for e in events))

    def test_explicit_invalidation_blocks_and_cannot_backdate(self):
        observer=SourcedCorrectionObserver(self.context,self.source)
        for b in self.bars:
            if b['time']==1674209400:observer.invalidate_context(b['time'],'OWNER_AUTHORITY_REVOKED')
            observer.feed_minute(b)
        self.assertFalse(any(e['kind']=='CORRECTION_FLIP_CANDIDATE' for e in observer.events))
        with self.assertRaises(ValueError):observer.invalidate_context(1,'backdated')

    def test_supplied_context_and_event_copies_cannot_mutate_state(self):
        supplied=copy.deepcopy(self.context);observer=SourcedCorrectionObserver(supplied,self.source)
        supplied['parent']['zone_low']=1
        public=observer.context;public['parent']['zone_low']=1
        with self.assertRaises(AttributeError):observer.source='0'*64
        for b in self.bars:observer.feed_minute(b)
        events=observer.events;events.clear()
        self.assertEqual(observer.events,self.runit().events)

    def test_absent_contact_cannot_generate_local_candidate(self):
        context=copy.deepcopy(self.context)
        context['parent'].update(zone_low=1.,zone_high=2.)
        events=self.runit(context=context).events
        self.assertFalse(any(e['kind']=='CORRECTION_FLIP_CANDIDATE' for e in events))

    def test_missing_and_wrong_source_minute_rejected(self):
        observer=SourcedCorrectionObserver(self.context,self.source)
        for field,value in [('source_id','other'),('known_at',self.bars[0]['known_at']+60),('complete',False)]:
            b=dict(self.bars[0]);b[field]=value
            with self.assertRaises(ValueError):observer.feed_minute(b)
        self.assertEqual(observer.events,[])

    def test_outside_order_ambiguous_missing_future_and_mirror_controls(self):
        bar=self.fixture['expected']['5M_LL_origin']
        members=[b for b in self.bars if bar['time']<=b['time']<bar['known_at']]
        before=[b for b in self.bars if bar['time']-300<=b['time']<bar['time']]
        ph,pl=max(b['high'] for b in before),min(b['low'] for b in before)
        def resolve(ms=members,b=bar,leg=-1,high=ph,low=pl,cutoff=None):
            return resolve_outside_reversal(b,ms,high,low,leg,b['known_at'] if cutoff is None else cutoff)
        witness=resolve();self.assertTrue(witness['resolved'])
        self.assertEqual((witness['extreme_time'],witness['crossing_time']),(1674209400,1674209520))
        same=copy.deepcopy(members);same[2]['low']=bar['low']
        self.assertFalse(resolve(same)['resolved'])
        late=copy.deepcopy(members);late[3]['low']=bar['low']-1
        self.assertFalse(resolve(late,dict(bar,low=bar['low']-1))['resolved'])
        self.assertFalse(resolve(members[:-1])['resolved'])
        self.assertFalse(resolve(cutoff=bar['known_at']-1)['resolved'])
        def mirror(b):return dict(b,open=30000-b['open'],high=30000-b['low'],low=30000-b['high'],close=30000-b['close'])
        self.assertTrue(resolve([mirror(b) for b in members],mirror(bar),1,30000-pl,30000-ph)['resolved'])

    def test_entire_synthetic_price_mirror_has_same_clocks(self):
        context=copy.deepcopy(self.context);context['direction']='short'
        context['parent']['zone_low'],context['parent']['zone_high']=30000-context['parent']['zone_high'],30000-context['parent']['zone_low']
        bars=[dict(b,open=30000-b['open'],high=30000-b['low'],low=30000-b['high'],close=30000-b['close']) for b in self.bars]
        long=self.runit().events;short=self.runit(bars,context).events
        self.assertEqual([(e['kind'],e['known_at']) for e in long],[(e['kind'],e['known_at']) for e in short])
        a=next(e for e in long if e['kind']=='CORRECTION_FLIP_CANDIDATE')
        b=next(e for e in short if e['kind']=='CORRECTION_FLIP_CANDIDATE')
        self.assertEqual(b['origin']['price'],30000-a['origin']['price'])

    def test_copied_algorithms_match_source_map(self):
        source_map=json.loads((Path(__file__).parent/'SOURCED_CORRECTION_V1.json').read_text())
        for function in (resolve_outside_reversal,correction_turn):
            actual=inspect.getsource(function).rstrip()+'\n'
            self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(),
                             source_map['copied_functions'][function.__name__])
        self.assertFalse(source_map['execution_allowed'])

if __name__=='__main__':unittest.main()
