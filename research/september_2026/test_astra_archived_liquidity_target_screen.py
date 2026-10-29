"""Causal evidence guards for source-frozen ASTRA local liquidity screen."""
import unittest
from copy import deepcopy
from astra_archived_liquidity_target_screen import eligible_observer_pools, support


def context(direction='long', at=600, target=None):
    pid='P'; contact='K'
    return {
      'asof':at,'parent':{'parent_id':pid,'direction':direction},
      'contact':{'contact_id':contact},
      'support_states':{60:{'known_at':at,'parent_id':pid,'contact_id':contact,'direction':direction},
                        300:{'known_at':at,'parent_id':pid,'contact_id':contact,'direction':direction}},
      'target':target,
      'liquidity_map':{'known_at':at,'asof':at,'parent_id':pid,'candidate_id':'C',
                       'decision_entry_reference':100,'buy_side':[],'sell_side':[]}}


def observed_pool(at=600,side='long',tf=900,price=None):
    p=105 if side=='long' else 95
    if price is not None:p=price
    return {
      'pool_id':'POOL','price':p,'not_crossed_since_enrollment':True,
      'crossing_status':'NOT_CROSSED_SINCE_ENROLLMENT','known_at':540,
      'latest_enrollment_at':540,'pre_enrollment_freshness':'UNKNOWN',
      'timeframes_seconds':[tf],
      'references':[{'timeframe_seconds':tf,'market_known_at':540,'registered_at':540,
                     'observed_through':at,'history_complete':True,
                     'first_strict_breach':None,'source_id':'NQ:15:0'}]}


def ev(side='long', at=600, tf=900,price=None):
    c=context(side,at)
    p=observed_pool(at,side,tf,price)
    c['liquidity_map']['buy_side' if side=='long' else 'sell_side']=[p]
    return {'kind':'CANDIDATE_RETURN','candidate_id':'C','known_at':at,'context':c,
            'bar':{'time':at-60,'known_at':at,'source_id':'NQ:1:540'}}


class SourceFrozenTargetScreenTests(unittest.TestCase):
    def test_m15_high_known_ahead_observed_since_enrollment(self):
        x,status=eligible_observer_pools(ev('long'))
        self.assertEqual(status,'SINCE_ENROLLMENT_ONLY');self.assertEqual(len(x),1)
        self.assertEqual(x[0]['timeframes_minutes'],[15])
        self.assertEqual(x[0]['pre_enrollment_freshness'],'UNKNOWN')
        self.assertFalse(x[0]['parent_ownership_authenticated'])
    def test_m15_short_low_ahead(self):
        x,_=eligible_observer_pools(ev('short'))
        self.assertEqual(x[0]['price'],95)
    def test_not_ahead_is_not_candidate(self):
        x,_=eligible_observer_pools(ev('long',price=99))
        self.assertEqual(len(x),0)
    def test_future_level_not_admitted(self):
        e=ev();e['context']['liquidity_map']['buy_side'][0]['known_at']=660
        self.assertFalse(eligible_observer_pools(e)[0])
    def test_crossed_level_not_admitted(self):
        e=ev();p=e['context']['liquidity_map']['buy_side'][0]
        p['crossing_status']='OBSERVED_STRICT_EXCURSION_SINCE_ENROLLMENT'
        p['not_crossed_since_enrollment']=False
        self.assertFalse(eligible_observer_pools(e)[0])
    def test_reference_history_unknown_or_later_bar_excluded(self):
        for key,value in [('history_complete',False),('observed_through',660),
                          ('market_known_at',660),('first_strict_breach',{'time':550})]:
            with self.subTest(key=key):
                e=ev();e['context']['liquidity_map']['buy_side'][0]['references'][0][key]=value
                self.assertFalse(eligible_observer_pools(e)[0])
    def test_map_parent_clock_and_candidate_identity_required(self):
        for key,val in [('parent_id','BAD'),('asof',660),('candidate_id','OTHER')]:
            with self.subTest(key=key):
                e=ev();e['context']['liquidity_map'][key]=val
                x,status=eligible_observer_pools(e)
                self.assertFalse(x);self.assertEqual(status,'MAP_CUTOFF_OR_ID_MISMATCH')
    def test_parent_and_contact_structure_identity_distinct(self):
        c=context();self.assertEqual(support(c),('ALIGNED','ALIGNED'))
        c['support_states'][300]['contact_id']='UNRELATED'
        self.assertEqual(support(c),('ALIGNED','LINEAGE_MISMATCH'))
    def test_rejected_unverified_group_membership(self):
        e=ev()
        e['context']['liquidity_map']['buy_side'][0]['timeframes_seconds']=[900,3600]
        self.assertFalse(eligible_observer_pools(e)[0])
    def test_original_zone_target_does_not_change_snapshots(self):
        e=ev();before=deepcopy(e)
        eligible_observer_pools(e)
        self.assertEqual(before,e)

if __name__=='__main__':unittest.main()
