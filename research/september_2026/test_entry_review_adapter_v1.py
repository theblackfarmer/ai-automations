import unittest
from entry_review_adapter_v1 import candidates
class AdapterTests(unittest.TestCase):
    def e(self,kind='ENTRY_REVIEW',**extra):
        return dict(id='a',kind=kind,known_at=60,direction='LONG',zone_low=99.,zone_high=100.,**extra)
    def test_next_open(self):
        rows,blocked=candidates([self.e()],{60:(101.,102.,100.,101.)})
        self.assertEqual(rows[0]['entry'],101.)
        self.assertEqual(rows[0]['stop'],99.)
        self.assertFalse(blocked)
    def test_no_next_bar(self):
        rows,blocked=candidates([self.e()],{})
        self.assertEqual(rows,[])
        self.assertEqual(blocked[0]['reason'],'NEXT_OPEN_UNOBSERVED')
    def test_blocked_review_never_trades(self):
        rows,blocked=candidates([self.e('ENTRY_REVIEW_BLOCKED')],{60:(101.,102.,100.,101.)})
        self.assertFalse(rows)
        self.assertEqual(blocked[0]['reason'],'ENGINE_REVIEW_BLOCKED')
    def test_bad_risk_rejected(self):
        rows,blocked=candidates([self.e()],{60:(98.,100.,97.,99.)})
        self.assertFalse(rows)
        self.assertEqual(blocked[0]['reason'],'NONPOSITIVE_INITIAL_RISK')
    def test_real_engine_event_schema(self):
        # Synthetic bars exercise the actual engine emitter, not a hand-built
        # approximation of its lowercase direction / geometry-free review.
        from test_engine import setup_engine, bar, TAP_START, M1
        engine=setup_engine(15)
        for bos in engine._bos.values():
            bos['target']={'price':22,'known_at':TAP_START,'reached':False,'reached_at':None}
        engine.feed(1,bar(1,TAP_START+15*60,M1[15]))
        engine.feed(1,bar(1,TAP_START+16*60,M1[16]))
        reviews=[e for e in engine.events if e['kind']=='ENTRY_REVIEW']
        self.assertTrue(reviews)
        bars={e['known_at']:(20.,) for e in reviews}
        rows,blocked=candidates(engine.events,bars)
        self.assertEqual(len(rows),len(reviews))
        self.assertFalse(blocked)
        for review,row in zip(reviews,rows):
            self.assertEqual(row['stop'],engine._children[review['child_id']]['zone_low'])
            self.assertEqual(row['entry_epoch'],review['known_at'])
            self.assertFalse(review['execution_allowed'])
        # Truncation at every decision preserves every emitted candidate.
        for i,event in enumerate(engine.events):
            if event['kind']=='ENTRY_REVIEW':
                prefix,_=candidates(engine.events[:i+1],bars)
                seen={e['id'] for e in engine.events[:i+1]}
                self.assertEqual(prefix,[r for r in rows if r['id'] in seen])

    def test_geometry_join_is_causal_and_identity_scoped(self):
        review=dict(id='r',kind='ENTRY_REVIEW',child_id='child',direction='short',known_at=120)
        respect=dict(kind='RESPECT_OBSERVED',child_id='child',direction='short',known_at=120,
                     zone_low=99.,zone_high=102.)
        bars={120:(100.,)}
        self.assertEqual(candidates([respect,review],bars)[0][0]['stop'],102.)
        # Positive control: a future join would accept this reversed stream.
        self.assertFalse(candidates([review,respect],bars)[0])
        for changes in ({'known_at':180},{'known_at':60},{'child_id':'other'},{'direction':'long'}):
            bad=dict(respect,**changes)
            self.assertFalse(candidates([bad,review],bars)[0])

    def test_nonfinite_geometry_and_invalid_clocks_blocked(self):
        for changes in ({'zone_high':float('inf')},{'zone_low':float('nan')},
                        {'known_at':61},{'known_at':True},{'known_at':-60}):
            event=self.e();event.update(changes)
            self.assertFalse(candidates([event],{event['known_at']:(101.,)})[0])

    def test_invalid_open_blocked_and_future_candle_values_irrelevant(self):
        for value in (float('nan'),float('inf'),0.,-1.,True,'101'):
            rows,blocked=candidates([self.e()],{60:(value,)})
            self.assertFalse(rows)
            self.assertEqual(blocked[0]['reason'],'INVALID_ENTRY_OPEN')
        expected=candidates([self.e()],{60:(101.,)})
        self.assertEqual(expected,candidates([self.e()],{60:(101.,9999.,0.,-1.),120:(9999.,)}))
        self.assertFalse(candidates([self.e()],{120:(101.,)})[0])

if __name__=='__main__':unittest.main()
