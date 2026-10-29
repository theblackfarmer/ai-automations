import unittest
from native_funnel_capture_v1 import capture_return,NativeFunnelCensus

SRC='a'*64

def mk_zone(id='Z1',direction='short',low=110,high=113,at=480,valid=True,target_valid=True,tf=900):
    return dict(zone_id=id,direction=direction,low=low,high=high,known_at=at,
                valid=valid,target_valid=target_valid,timeframe_seconds=tf)

def mk_packet(direction='long'):
    return dict(parent={'parent_id':'P','direction':direction},contact={'contact_id':'H'},
                formation_result={'candidate':{'proximal':100}})

def mk_event(cid='C',at=600,mark=100):
    return dict(kind='CANDIDATE_RETURN',candidate_id=cid,known_at=at,
                bar=dict(time=at-60,known_at=at,close=mark,source_id=f'NQ:1:{at-60}'))

def mk_ctx(at=600,target=None,m1=True,m5=True,reasons=None,dir='long'):
    def state(tf):return {'known_at':at,'parent_id':'P','contact_id':'H','direction':dir}
    return dict(asof=at,target=target,support_states={60:state(60) if m1 else None,
                300:state(300) if m5 else None},reasons=reasons or [])

def cap(zones=None,actual=None,at=600,dir='long',cid='C',m1=True,m5=True,mark=100):
    return capture_return(source_namespace=SRC,event=mk_event(cid,at,mark),
             context=mk_ctx(at, actual,m1,m5,dir=dir),
             packet=mk_packet(dir), catalog=zones or [])

class NativeFunnelTests(unittest.TestCase):
    def test_missing_catalog(self):
        self.assertEqual(cap()['stage'],'NO_OPPOSING_ZONE_KNOWN')
    def test_only_same_direction_is_no_opposing(self):
        self.assertEqual(cap([mk_zone(direction='long')])['stage'],'NO_OPPOSING_ZONE_KNOWN')
    def test_invalid_catalog(self):
        r=cap([mk_zone(target_valid=False)]);self.assertEqual(r['stage'],'ALL_KNOWN_OPPOSING_INVALID')
    def test_close_mode_retains_target_after_farwick(self):
        z=mk_zone(valid=False,target_valid=True)
        self.assertEqual(cap([z],{'zone_id':'Z1'})['stage'],'TARGET_AVAILABLE')
    def test_valid_zone_behind(self):
        self.assertEqual(cap([mk_zone(low=96,high=99)])['stage'],'VALID_OPPOSING_NOT_AHEAD')
    def test_equal_proximal_is_not_ahead(self):
        self.assertEqual(cap([mk_zone(low=100,high=103)])['stage'],'VALID_OPPOSING_NOT_AHEAD')
    def test_long_requires_supply_ahead(self):
        r=cap([mk_zone()],{'zone_id':'Z1'});self.assertEqual(r['selected_zone_id'],'Z1')
    def test_short_requires_demand_below(self):
        r=cap([mk_zone(direction='long',low=84,high=90)],{'zone_id':'Z1'},dir='short')
        self.assertEqual(r['stage'],'TARGET_AVAILABLE')
    def test_future_zone_not_considered(self):
        self.assertEqual(cap([mk_zone(at=660)])['stage'],'NO_OPPOSING_ZONE_KNOWN')
    def test_latest_anchor_is_max_proximal_close(self):
        self.assertEqual(cap([mk_zone(low=101,high=104)],mark=102)['stage'],'VALID_OPPOSING_NOT_AHEAD')
    def test_parity_invalid_fails(self):
        with self.assertRaisesRegex(ValueError,'selector parity'):
            cap([mk_zone()],None)
    def test_earlier_clock_fails(self):
        e=mk_event();e['bar']['time']=500
        with self.assertRaisesRegex(ValueError,'not fully closed'):
            capture_return(source_namespace=SRC,event=e,context=mk_ctx(),packet=mk_packet(),catalog=[])
    def test_context_clock_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError,'context'):
            capture_return(source_namespace=SRC,event=mk_event(),context=mk_ctx(at=540),packet=mk_packet(),catalog=[])
    def test_future_structure_fails(self):
        x=mk_ctx();x['support_states'][60]['known_at']=900
        with self.assertRaisesRegex(ValueError,'future-known'):
            capture_return(source_namespace=SRC,event=mk_event(),context=x,packet=mk_packet(),catalog=[])
    def test_lineage_mismatch_does_not_qualify(self):
        x=mk_ctx();x['support_states'][60]['contact_id']='OTHER'
        r=capture_return(source_namespace=SRC,event=mk_event(),context=x,packet=mk_packet(),catalog=[])
        self.assertEqual(r['m1'],'LINEAGE_MISMATCH')
    def test_missing_structure_unknown_not_opposed(self):
        r=cap(m1=False);self.assertEqual(r['m1'],'UNKNOWN')
    def test_target_relevance_stays_unverified(self):
        self.assertEqual(cap([mk_zone()],{'zone_id':'Z1'})['parent_relevance'],'UNVERIFIED_FROM_CATALOG')
    def test_repeated_observations_not_distinct_candidates(self):
        c=NativeFunnelCensus();c.observe(cap());c.observe(cap(at=660))
        v=c.report(expected_total=2,expected_missing=2,expected_targets=0)
        self.assertEqual(v['distinct_candidates'],1)
        self.assertEqual(v['first_return_no_target_candidates'],1)
    def test_first_target_later_after_missing(self):
        c=NativeFunnelCensus();c.observe(cap());c.observe(cap(at=660,zones=[mk_zone()],actual={'zone_id':'Z1'}))
        self.assertEqual(c.report()['first_missing_later_target_candidates'],1)
    def test_duplicate_rejected(self):
        c=NativeFunnelCensus();c.observe(cap())
        with self.assertRaisesRegex(ValueError,'duplicate'):
            c.observe(cap())
    def test_population_mismatch_rejected(self):
        c=NativeFunnelCensus();c.observe(cap())
        with self.assertRaisesRegex(ValueError,'INCOMPLETE'):
            c.report(expected_total=3069)
    def test_different_candidate_same_return_minute_counts_separately(self):
        c=NativeFunnelCensus();c.observe(cap(cid='C1'));c.observe(cap(cid='C2'))
        self.assertEqual(c.report()['distinct_candidates'],2)
    def test_rejects_missing_status(self):
        z=mk_zone();z['target_valid']='no'
        with self.assertRaisesRegex(ValueError,'boolean'):
            cap([z])
    def test_rejects_unsupported_timeframe(self):
        with self.assertRaisesRegex(ValueError,'timeframe'):
            cap([mk_zone(tf=60)])
    def test_includes_all_joint_context_issues(self):
        a=capture_return(source_namespace=SRC,event=mk_event(),context=mk_ctx(reasons=['NO_RELEVANT_OPPOSING_TARGET','M1_STRUCTURE_UNKNOWN']),packet=mk_packet(),catalog=[])
        c=NativeFunnelCensus();c.observe(a)
        self.assertEqual(c.report()['joint_issue_counts']['M1_STRUCTURE_UNKNOWN'],1)
    def test_stage_structure_cross_table_counts(self):
        c=NativeFunnelCensus();c.observe(cap());c.observe(cap(cid='C2',zones=[mk_zone()],actual={'zone_id':'Z1'}))
        r=c.report()
        self.assertEqual(r['stage_by_m1_m5']['NO_OPPOSING_ZONE_KNOWN|ALIGNED|ALIGNED'],1)
        self.assertEqual(r['stage_by_m1_m5']['TARGET_AVAILABLE|ALIGNED|ALIGNED'],1)

    def test_stably_ordered_rank_timeframe(self):
        a=mk_zone(id='A',tf=900);b=mk_zone(id='B',tf=3600)
        self.assertEqual(cap([a,b],{'zone_id':'B'})['selected_zone_id'],'B')
    def test_summary_is_read_only_by_contract(self):
        x=NativeFunnelCensus();x.observe(cap());r=x.report()
        self.assertFalse(r['trade_admission']);self.assertFalse(r['orders_changed'])

if __name__=='__main__':unittest.main()
