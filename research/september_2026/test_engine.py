"""Synthetic-only contract tests. No source market file is loaded or executed."""
import copy
import unittest
from engine import Engine, SwingTracker, association, formation_candidates, progressing, digest, validate_bar

SPEC = {'id': 'SEPTEMBER_4H_1H_1M_V1', 'execution_allowed': False, 'test_fixture': 'SYNTHETIC_NOT_MARKET_DATA'}
HTF = [
 (10,11,9,10), (10,14,10,13), (13,13,9.5,9.8), (10,11,7,8),
 (8,12,8,11.5), (11.5,16,11,15), (15,15.5,10,10.5), (10.5,11,9,9.5),
 (9.5,13,9.5,12), (12,15,11.5,14), (14,18,13,17), (17,17.8,16.5,17)]
M1 = [
 (16,16,14,15.5), (15.5,17,15,16.5), (16.5,16.8,14.5,14.8), (14.8,15.5,14,14.2),
 (14.2,16,14.1,15.8), (15.8,17.5,15.5,17.2), (17.2,17.4,15.2,15.3), (15.3,15.7,14.8,15),
 (15,16.5,14.9,16), (16,18,15.8,17.8), (17.8,17.9,15.7,15.75), (15.75,16,15.3,15.5),
 (15.5,17,15.4,16.5), (16.5,17.8,16.2,17.5), (17.5,19,17,18.5),
 (18.5,18.7,17.6,18), (18,19,17.95,18.8)]
TAP_START = 44 * 3600


def bar(tf, t, prices, **extra):
    o,h,l,c=prices
    return dict(time=t,known_at=t+tf*60,open=o,high=h,low=l,close=c,
                complete=True,source_id=f'SYNTHETIC:{tf}:{t}',**extra)


def higher_records():
    result=[(240,bar(240,i*14400,p)) for i,p in enumerate(HTF[:11])]
    result += [(60,bar(60,(32+i)*3600,p)) for i,p in enumerate(HTF)]
    return sorted(result,key=lambda x:(x[1]['known_at'], -x[0]))


def setup_engine(count=17):
    e=Engine(SPEC)
    e.feed_batch(higher_records())
    for i,p in enumerate(M1[:count]):e.feed(1,bar(1,TAP_START+i*60,p))
    return e


def same_cutoff_target_records(minute_count=17):
    """Independent P1 public-feed fixture, retained as a permanent regression.

    All overlapping H1 groups aggregate to their H4 bars. The M1 suffix fits
    its H1 envelope/final close. H1 first recognizes the target at208800.
    No Engine internals or market data are needed to reproduce the fault.
    """
    prices=M1[:minute_count]
    start=58*3600-minute_count*60
    stream=[(240,bar(240,i*14400,p)) for i,p in enumerate(HTF[:11])]
    hourly=[tuple(v+10 for v in p) for p in HTF]
    stream += [(60,bar(60,(44+i)*3600,p)) for i,p in enumerate(hourly)]
    for i in range(0,12,4):
        group=hourly[i:i+4]
        p=(group[0][0],max(x[1] for x in group),min(x[2] for x in group),group[-1][3])
        stream.append((240,bar(240,(44+i)*3600,p)))
    stream += [(60,bar(60,56*3600,(32,36,32,35))),
               (60,bar(60,57*3600,(35,36,19,prices[-1][3]+10)))]
    stream += [(1,bar(1,start+i*60,tuple(v+10 for v in p))) for i,p in enumerate(prices)]
    return stream


def bootstrap_boundary_records(direction='long', scenario='reference_at_open'):
    """Public-feed regression: choose latest pivots before checking availability.

    First high16, higher high18 and a higher low establish a synthetic rising
    sequence. The old engine breaks16 while18 has only just become known. The
    valid neighbor waits until the latest intervening pullback is strictly known
    before crossing18. Short cases are an exact price reflection about20.
    """
    prices=[(10,11,9,10),(10,10.5,7,8),(8,12,8,11.5),(11.5,16,11,15),
            (15,15.5,10,10.5),(10.5,11,9,9.5),(9.5,13,9.5,12),(12,18,11.5,17),
            (17,17.8,10,10.5)]
    if scenario=='reference_at_open':prices += [(10.5,17,10.2,16.5)]
    elif scenario=='progression_at_close':prices += [(10.5,19,10.2,18.5)]
    elif scenario in ('pullback_at_open','valid_after_wait'):
        prices += [(10.5,17,10.2,15.5),(15.5,17.5,14.5,17.2)]
        if scenario=='valid_after_wait':prices += [(17.2,17.8,16.7,17.4)]
        prices += [(prices[-1][3],19,17.1,18.5)]
    else:raise ValueError('Unknown synthetic scenario')
    if direction=='short':prices=[(40-o,40-l,40-h,40-c) for o,h,l,c in prices]
    return [(60,bar(60,i*3600,prices)) for i,prices in enumerate(prices)]


def pivot(kind,t,value,label,known=None,previous=None):
    return dict(id=f'{kind}:{t}',kind=kind,time=t,price=value,label=label,
                known_at=t+1 if known is None else known,comparator=copy.deepcopy(previous),source_id=f'p:{t}')


class StreamingTests(unittest.TestCase):
    def test_public_bootstrap_cannot_fall_back_when_latest_reference_known_at_open(self):
        for direction in ('long','short'):
            with self.subTest(direction=direction):
                records=bootstrap_boundary_records(direction,'reference_at_open')
                e=Engine(SPEC);e.feed_batch(records)
                directional=[x['pivot'] for x in e.events if x['kind']=='PIVOT_CONFIRMED'
                             and x['pivot']['kind']==('high' if direction=='long' else 'low')]
                self.assertEqual(directional[-1]['known_at'],records[-1][1]['time'])
                self.assertFalse(any(x['kind']=='MAJOR_BOS' for x in e.events))
                self.assertFalse(any(x['kind']=='ZONE_CANDIDATE' for x in e.events))

    def test_public_bootstrap_cannot_borrow_progression_confirmed_on_bos_close(self):
        for direction in ('long','short'):
            with self.subTest(direction=direction):
                records=bootstrap_boundary_records(direction,'progression_at_close')
                e=Engine(SPEC);e.feed_batch(records)
                last=next(x for x in reversed(e.events) if x['kind']=='PIVOT_CONFIRMED')
                self.assertEqual(last['known_at'],records[-1][1]['known_at'])
                self.assertFalse(any(x['kind']=='MAJOR_BOS' for x in e.events))

    def test_public_bootstrap_latest_pullback_at_open_is_not_strictly_prior(self):
        for direction in ('long','short'):
            with self.subTest(direction=direction):
                records=bootstrap_boundary_records(direction,'pullback_at_open')
                e=Engine(SPEC);e.feed_batch(records)
                opposite=[x['pivot'] for x in e.events if x['kind']=='PIVOT_CONFIRMED'
                          and x['pivot']['kind']==('low' if direction=='long' else 'high')]
                self.assertEqual(opposite[-1]['known_at'],records[-1][1]['time'])
                self.assertFalse(any(x['kind']=='MAJOR_BOS' for x in e.events))

    def test_public_bootstrap_valid_after_strictly_prior_progression_still_emits(self):
        for direction in ('long','short'):
            with self.subTest(direction=direction):
                records=bootstrap_boundary_records(direction,'valid_after_wait')
                e=Engine(SPEC);e.feed_batch(records)
                events=[x for x in e.events if x['kind']=='MAJOR_BOS']
                self.assertEqual(len(events),1)
                event=events[0];self.assertEqual(event['direction'],direction)
                self.assertEqual(event['reference']['price'],18 if direction=='long' else 22)
                self.assertEqual(event['bar_start'],records[-1][1]['time'])
                self.assertLess(event['reference']['known_at'],event['bar_start'])
                self.assertLess(event['pullback']['known_at'],event['bar_start'])
                self.assertEqual(event['bos_type'],'BOOTSTRAP_OR_REESTABLISHMENT')

    def test_association_latest_reference_clock_neighbors_never_fall_back(self):
        for direction in ('long','short'):
            k='high' if direction=='long' else 'low';o='low' if direction=='long' else 'high'
            old=pivot(k,10,10,'H' if direction=='long' else 'L',known=11)
            for known in (99,100,101):
                with self.subTest(direction=direction,known=known):
                    new=pivot(k,30,15,'HH' if direction=='long' else 'LL',known=known,previous=old)
                    pull=pivot(o,40,8,'HL' if direction=='long' else 'LH',known=70)
                    pair=association([old,new,pull],direction,100)
                    if known<100:self.assertEqual(pair[0]['id'],new['id'])
                    else:self.assertIsNone(pair)

    def test_explicit_internal_reference_cannot_bypass_newer_unpaired_reference(self):
        old=pivot('high',10,20,'H',known=11)
        pull=pivot('low',20,10,'HL',known=21)
        newer=pivot('high',30,22,'HH',known=31,previous=old)
        self.assertIsNone(association([old,pull,newer],'long',50,old))
        # Explicit external scope remains separately defined; it may retain its
        # named harder external reference through a later smaller internal high.
        internal=pivot('high',30,18,'LH',known=31,previous=old)
        self.assertEqual(association([old,pull,internal],'long',50,old,scope_external=True)[0]['id'],old['id'])

    def test_association_external_reference_keeps_scope_but_latest_pullback_must_be_prior(self):
        external=pivot('high',10,20,'HH',known=11)
        oldpull=pivot('low',20,10,'HL',known=21)
        internal=pivot('high',30,18,'LH',known=31)
        for known in (99,100,101):
            latest=pivot('low',40,12,'HL',known=known,previous=oldpull)
            pair=association([external,oldpull,internal,latest],'long',100,external,scope_external=True)
            if known<100:
                self.assertEqual(pair[0]['id'],external['id']);self.assertEqual(pair[1]['id'],latest['id'])
            else:self.assertIsNone(pair)

    def test_public_feed_same_cutoff_htf_target_cannot_authorize_respect_review(self):
        e=Engine(SPEC);e.feed_batch(same_cutoff_target_records())
        targets=[x for x in e.events if x['kind']=='ORIGINAL_TARGET_KNOWN']
        self.assertEqual(len(targets),1)
        self.assertEqual(targets[0]['known_at'],208800)
        respect=[x for x in e.events if x['kind']=='RESPECT_OBSERVED']
        self.assertEqual(len(respect),1)
        self.assertEqual(respect[0]['bar_start'],208740)
        self.assertFalse(respect[0]['target_known'])
        self.assertIsNone(respect[0]['target'])
        self.assertFalse(any(x['kind']=='ENTRY_REVIEW' for x in e.events))
        blocked=[x for x in e.events if x['kind']=='ENTRY_REVIEW_BLOCKED']
        self.assertEqual(len(blocked),1)
        self.assertEqual(blocked[0]['review_status'],'BLOCKED_TARGET_UNKNOWN')
        # Recognition stays truthful and available globally; nothing is backdated.
        bos_id=targets[0]['bos_id']
        self.assertEqual(e.snapshot()['bos'][bos_id]['target']['known_at'],208800)
        # This repair does not invent a second review after the one respect event.
        e.feed(1,bar(1,208800,(28.8,29,28.5,28.9)))
        self.assertEqual(len([x for x in e.events if x['kind']=='ENTRY_REVIEW_BLOCKED']),1)
        self.assertFalse(any(x['kind']=='ENTRY_REVIEW' for x in e.events))

    def test_public_feed_same_cutoff_target_hidden_at_child_selection(self):
        e=Engine(SPEC);e.feed_batch(same_cutoff_target_records(minute_count=15))
        candidates=[x for x in e.events if x['kind']=='CHILD_CANDIDATE']
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['known_at'],208800)
        self.assertEqual(candidates[0]['bar_start'],208740)
        self.assertFalse(candidates[0]['target_known'])
        self.assertIsNone(candidates[0]['target'])
        self.assertTrue(any(x['kind']=='ORIGINAL_TARGET_KNOWN' and x['known_at']==208800 for x in e.events))

    def test_public_feed_shared_cutoff_owner_revocation_preserves_minute_return_and_failure(self):
        stream=[]
        for tf,b in same_cutoff_target_records():
            if tf==60 and b['time']==57*3600:b=bar(60,b['time'],(35,36,18,18.5))
            if tf==1 and b['time']==208680:b=bar(1,b['time'],(28.5,29,28.2,28.8))
            if tf==1 and b['time']==208740:b=bar(1,b['time'],(28.8,29,18,18.5))
            stream.append((tf,b))
        e=Engine(SPEC);e.feed_batch(stream)
        last=[x for x in e.events if x['known_at']==208800]
        self.assertTrue(any(x['kind']=='AUTHORITY_REVOKED' and x['timeframe']==60 for x in last))
        returns=[x for x in last if x['kind']=='RETURN_OBSERVED']
        breaches=[x for x in last if x['kind']=='CHILD_ZONE_BREACHED']
        self.assertEqual(len(returns),1)
        self.assertEqual(len(breaches),1)
        self.assertEqual(returns[0]['htf_context_as_of'],208740)
        self.assertTrue(returns[0]['failed_same_bar'])
        self.assertIsNone(e.snapshot()['active_authorities'][60])
        self.assertTrue(all(not c['active'] for c in e.snapshot()['children'].values()))
        before=digest(e.events)
        e.feed(1,bar(1,208800,(18.5,19,18.2,18.7)))
        self.assertEqual(len([x for x in e.events if x['kind']=='RETURN_OBSERVED']),1)
        self.assertFalse(any(x['kind']=='ENTRY_REVIEW' for x in e.events))

    def test_two_timeframes_link_before_tap_and_full_cycle_entry(self):
        e=setup_engine()
        major=[x for x in e.events if x['kind']=='MAJOR_BOS']
        self.assertEqual([x['timeframe'] for x in major],[60,240])
        links=[x for x in e.events if x['kind']=='HTF_LINK']
        self.assertEqual(len(links),1)
        self.assertEqual(links[0]['known_at'],TAP_START)
        taps=[x for x in e.events if x['kind']=='HTF_TAP']
        self.assertTrue(taps)
        self.assertTrue(all(x['bar_start']>=links[0]['known_at'] for x in taps))
        transitions=[x for x in e.events if x['kind']=='M1_TRANSITION_BOS']
        self.assertTrue(transitions)
        self.assertTrue(all(x['structure_known_at']<x['bar_start'] for x in transitions))
        children=[x for x in e.events if x['kind']=='CHILD_CANDIDATE']
        self.assertTrue(children)
        self.assertTrue(all(x['branch_names']==['A_OPPOSITE_RUN','B_WHOLE_BASE'] for x in children))
        self.assertTrue(all(x['zone_low']==15.3 and x['zone_high']==17.9 for x in children))
        reviews=[x for x in e.events if x['kind']=='ENTRY_REVIEW_BLOCKED']
        self.assertTrue(reviews)
        self.assertTrue(all(x['review_status']=='BLOCKED_TARGET_UNKNOWN' for x in reviews))
        self.assertTrue(all(x['execution_allowed'] is False for x in e.events))

    def test_one_pair_not_two_cycles(self):
        e=setup_engine(10)
        self.assertFalse(any(x['kind']=='M1_TRANSITION_BOS' for x in e.events))

    def test_same_bar_structure_known_cannot_bos(self):
        e=setup_engine(13)
        # Last HL recognized at end of bar12. Bar13 begins at exactly that clock.
        e.feed(1,bar(1,TAP_START+13*60,(16.5,19,16.2,18.5)))
        self.assertFalse(any(x['kind']=='M1_TRANSITION_BOS' for x in e.events))

    def test_return_and_respect_separate(self):
        e=setup_engine(16)
        self.assertTrue(any(x['kind']=='RETURN_OBSERVED' for x in e.events))
        self.assertFalse(any(x['kind']=='RESPECT_OBSERVED' for x in e.events))
        e.feed(1,bar(1,TAP_START+16*60,M1[16]))
        returns={x['child_id']:x for x in e.events if x['kind']=='RETURN_OBSERVED'}
        for x in e.events:
            if x['kind']=='RESPECT_OBSERVED':self.assertGreater(x['known_at'],returns[x['child_id']]['known_at'])

    def test_child_breach_does_not_reverse_parent(self):
        e=setup_engine(15)
        before=e.snapshot()['active_authorities']
        e.feed(1,bar(1,TAP_START+15*60,(18.5,18.7,15.2,17)))
        self.assertTrue(any(x['kind']=='CHILD_ZONE_BREACHED' for x in e.events))
        self.assertTrue(any(x['kind']=='RETURN_OBSERVED' and x.get('failed_same_bar') for x in e.events))
        self.assertFalse(any(x['kind']=='RESPECT_OBSERVED' for x in e.events))
        self.assertEqual(before,e.snapshot()['active_authorities'])
        self.assertFalse(any(x['kind']=='PARENT_ZONE_BREACHED' for x in e.events))

    def test_parent_zone_wick_breach_not_authority_reversal(self):
        e=setup_engine(1)
        before=e.snapshot()['active_authorities']
        e.feed(1,bar(1,TAP_START+60,(15.5,16,8.9,12)))
        self.assertTrue(any(x['kind']=='PARENT_ZONE_BREACHED' for x in e.events))
        self.assertEqual(before,e.snapshot()['active_authorities'])

    def test_exact_edge_contact_not_strict_breach(self):
        e=setup_engine(15)
        e.feed(1,bar(1,TAP_START+15*60,(18.5,18.7,15.3,17)))
        self.assertTrue(any(x['kind']=='CHILD_ZONE_EDGE_CONTACT' for x in e.events))
        self.assertFalse(any(x['kind']=='CHILD_ZONE_BREACHED' for x in e.events))

    def test_structural_bos_independent_of_origin(self):
        import engine
        old=engine.formation_candidates
        engine.formation_candidates=lambda *args:([],[{'branch_names':['TEST'],'reason':'UNRESOLVED'}])
        try:
            e=Engine(SPEC);e.feed_batch(higher_records())
        finally:engine.formation_candidates=old
        self.assertEqual(len([x for x in e.events if x['kind']=='MAJOR_BOS']),2)
        self.assertFalse(e.snapshot()['zones'])
        self.assertTrue(e.snapshot()['links'])

    def test_copies_cannot_rewrite_events_or_geometry(self):
        e=setup_engine(15)
        original=digest(e.snapshot());events=digest(e.events)
        snapshot=e.snapshot();snapshot['zones'].clear()
        copy_events=e.events;copy_events[0]['kind']='FORGED'
        self.assertEqual(original,digest(e.snapshot()))
        self.assertEqual(events,digest(e.events))
        b=bar(1,TAP_START+15*60,M1[15]);new=e.feed(1,b);head=digest(e.events)
        b['low']=1;new.clear()
        self.assertEqual(head,digest(e.events))

    def test_every_prefix_and_future_suffix_invariance(self):
        records=higher_records()+[(1,bar(1,TAP_START+i*60,p)) for i,p in enumerate(M1)]
        records.sort(key=lambda x:(x[1]['known_at'],-x[0]))
        full=Engine(SPEC);heads=[]
        for tf,b in records:
            full.feed(tf,b);heads.append(digest(full.events))
        for n in range(1,len(records)+1):
            short=Engine(SPEC);short.feed_batch(records[:n])
            self.assertEqual(digest(short.events),heads[n-1])
        original=Engine(SPEC);original.feed_batch(records[:-2]);saved=original.events
        altered=Engine(SPEC);altered.feed_batch(records[:-2]);altered.feed(1,bar(1,TAP_START+15*60,(18.5,20,8,12)))
        self.assertEqual(altered.events[:len(saved)],saved)
        # Positive control actually reads a future suffix: the prefix audit must
        # reject this deliberately contaminated wrapper, while the real API passes.
        def leaky_wrapper(all_records, prefix_size):
            leaky=Engine(SPEC);leaky.feed_batch(all_records[:prefix_size])
            contaminated=leaky.events
            contaminated[0]['illegal_future_close']=all_records[-1][1]['close']
            return contaminated
        self.assertNotEqual(digest(leaky_wrapper(records,len(records)-2)),
                            digest(leaky_wrapper(records[:-2],len(records)-2)))

    def test_duplicate_clock_reversed_tf_rejected_without_mutation(self):
        e=Engine(SPEC);e.feed(1,bar(1,3540,(10,11,9,10)))
        head=digest(e.snapshot())
        with self.assertRaises(ValueError):e.feed(60,bar(60,0,(10,11,9,10)))
        self.assertEqual(head,digest(e.snapshot()))
        with self.assertRaises(ValueError):e.feed(1,bar(1,3540,(10,11,9,10)))

    def test_batch_prevalidation_is_atomic(self):
        e=Engine(SPEC);head=digest(e.snapshot())
        with self.assertRaises(ValueError):e.feed_batch([(1,bar(1,0,(10,11,9,10))),(1,bar(1,0,(10,11,9,10)))])
        self.assertEqual(head,digest(e.snapshot()))

    def test_explicit_gap_never_allows_clock_rewind(self):
        e=Engine(SPEC);e.feed(1,bar(1,3540,(10,11,9,10)))
        e.gap(3600,'documented unknown')
        with self.assertRaises(ValueError):e.feed(60,bar(60,0,(10,11,9,10)))

    def test_incomplete_and_bad_phase_and_false_scheduled_rejected(self):
        for mutation in [{'complete':False},{'known_at':61},{'time':1},{'gap_before':'scheduled'},{'low':float('nan')}]:
            b=bar(1,0,(10,11,9,10));b.update(mutation)
            with self.assertRaises(ValueError):validate_bar(1,b)

    def test_unclassified_gap_revokes_authority_not_target_history(self):
        e=setup_engine(15)
        bos=next(iter(e._bos.values()))
        bos['target']={'price':20,'known_at':TAP_START,'reached':True,'reached_at':TAP_START+60}
        e.feed(1,bar(1,TAP_START+17*60,(18,19,17,18)))
        self.assertTrue(any(x['kind']=='UNCLASSIFIED_COVERAGE_GAP' for x in e.events))
        self.assertEqual(e.snapshot()['active_authorities'],{240:None,60:None})
        self.assertTrue(e.snapshot()['bos'][bos['id']]['target']['reached'])

    def test_public_gap_generator_is_not_consumed_by_validation(self):
        e=setup_engine(15)
        known=e.snapshot()['last_order'][0]
        events=e.gap(known,'SYNTHETIC_GENERATOR_GAP',timeframes=iter([240,60,1]))
        record=next(x for x in events if x['kind']=='UNCLASSIFIED_COVERAGE_GAP')
        self.assertEqual(record['timeframes'],[240,60,1])
        self.assertEqual(e.snapshot()['active_authorities'],{240:None,60:None})
        self.assertTrue(all(not c['active'] for c in e.snapshot()['children'].values()))
        self.assertTrue(all(not t['active'] for t in e.snapshot()['taps'].values()))

    def test_invalid_gap_arguments_reject_atomically(self):
        invalid=[{'timeframes':[]},{'timeframes':[240,240]}, {'timeframes':[True]},
                 {'timeframes':[60.0]}, {'timeframes':None}, {'timeframes':[15]},
                 {'scheduled':'false','schedule_evidence':'SYNTHETIC'},
                 {'scheduled':1,'schedule_evidence':'SYNTHETIC'},
                 {'scheduled':None}]
        for args in invalid:
            e=setup_engine(15);before=e.snapshot();head=digest(e.events)
            with self.assertRaises(ValueError):e.gap(before['last_order'][0],'INVALID_GAP',**args)
            self.assertEqual(before,e.snapshot())
            self.assertEqual(head,digest(e.events))

    def test_scheduled_gap_requires_evidence_and_preserves_state(self):
        e=setup_engine(1);active=e.snapshot()['active_authorities']
        e.feed(1,bar(1,TAP_START+180,(15.5,16,14.5,15),gap_before='scheduled',schedule_evidence='SYNTHETIC_CALENDAR_ID'))
        self.assertEqual(active,e.snapshot()['active_authorities'])
        self.assertTrue(any(x['kind']=='SCHEDULED_COVERAGE_BRIDGE' for x in e.events))

    def test_targets_forming_touch_excluded_retouch_exhausts(self):
        e=setup_engine(15)
        z=next(iter(e._zones.values()));bos=e._bos[z['bos_id']]
        bos['target']={'price':18.7,'known_at':TAP_START+15*60,'reached':False,'reached_at':None}
        e.feed(1,bar(1,TAP_START+15*60,M1[15]))
        self.assertTrue(bos['target']['reached'])
        e.feed(1,bar(1,TAP_START+16*60,M1[16]))
        reviews=[x for x in e.events if x['kind']=='ENTRY_REVIEW_BLOCKED' and e._children[x['child_id']]['parent_zone_id']==z['id']]
        self.assertTrue(reviews)
        self.assertTrue(all(x['review_status']=='BLOCKED_TARGET_ALREADY_REACHED' for x in reviews))

    def test_delayed_return_no_expiry_or_origin_replacement(self):
        e=setup_engine(15)
        children=copy.deepcopy(e.snapshot()['children'])
        for i in range(15,40):e.feed(1,bar(1,TAP_START+i*60,(18.5,18.8,18.1,18.5)))
        self.assertTrue(all(c['active'] for c in e.snapshot()['children'].values()))
        for cid,c in children.items():self.assertEqual(c['member_times'],e.snapshot()['children'][cid]['member_times'])
        e.feed(1,bar(1,TAP_START+40*60,M1[15]))
        self.assertEqual(len([x for x in e.events if x['kind']=='RETURN_OBSERVED']),len(children))

    def test_strict_htf_same_cutoff_no_retroactive_tap(self):
        e=Engine(SPEC)
        records=higher_records()+[(1,bar(1,TAP_START-60,(16,16,14,15.5)))]
        e.feed_batch(records)
        self.assertFalse(any(x['kind']=='HTF_TAP' for x in e.events))
        e.feed(1,bar(1,TAP_START,(15.5,16,14.5,15)))
        self.assertTrue(any(x['kind']=='HTF_TAP' for x in e.events))

    def test_only_one_tf_or_opposing_tf_cannot_link(self):
        e=Engine(SPEC);e.feed_batch([x for x in higher_records() if x[0]==240])
        self.assertFalse(e.snapshot()['links'])
        self.assertTrue(e.snapshot()['active_authorities'][240])

    def test_owner_close_revokes_and_child_flip_does_not(self):
        e=setup_engine(15)
        hour=45*3600
        e.feed(60,bar(60,44*3600,(17,18,8,8.5)))
        self.assertIsNone(e.snapshot()['active_authorities'][60])
        self.assertIsNotNone(e.snapshot()['active_authorities'][240])
        self.assertFalse(any(c['active'] for c in e.snapshot()['children'].values()))


class MechanicalTests(unittest.TestCase):
    def test_three_pivots_each_two_labels_and_pre_tap_baseline(self):
        h0=pivot('high',10,10,'H');l0=pivot('low',20,5,'L')
        h1=pivot('high',40,11,'HH',previous=h0);l1=pivot('low',50,6,'HL',previous=l0)
        h2=pivot('high',60,12,'HH',previous=h1);l2=pivot('low',70,7,'HL',previous=l1)
        pivots=[h0,l0,h1,l1,h2,l2]
        self.assertIsNotNone(progressing(pivots,'long',2,after=30))
        self.assertIsNone(progressing(pivots,'long',2,after=40))
        self.assertIsNone(progressing(pivots[:-2],'long',2,after=30))
        bad=copy.deepcopy(pivots);bad[-1]['price']=6
        self.assertIsNone(progressing(bad,'long',2,after=30))

    def test_reference_no_older_fallback_and_strict_known_time(self):
        h0=pivot('high',10,10,'H',11);l0=pivot('low',20,5,'L',21)
        h1=pivot('high',30,11,'HH',31,h0);l1=pivot('low',40,6,'HL',50,l0)
        pivots=[h0,l0,h1,l1]
        self.assertIsNone(association(pivots,'long',50))
        self.assertIsNotNone(association(pivots,'long',51))
        self.assertIsNone(association(pivots,'long',51,h0))

    def test_major_external_reference_survives_internal_lower_high(self):
        external=pivot('high',10,20,'HH',11)
        l0=pivot('low',20,10,'HL',21)
        internal=pivot('high',30,18,'LH',31,external)
        pullback=pivot('low',40,12,'HL',41,l0)
        pivots=[external,l0,internal,pullback]
        self.assertEqual(association(pivots,'long',50)[0]['id'],internal['id'])
        self.assertEqual(association(pivots,'long',50,external,scope_external=True)[0]['id'],external['id'])
        self.assertIsNone(association(pivots,'long',50,external))

    def test_full_envelope_departure_waits_before_return(self):
        e=setup_engine(15)
        # This synthetic seam isolates a base with a higher earlier wick than
        # its pivot envelope, which the frozen clarification explicitly allows.
        for child in e._children.values():
            child['zone_high']=19.2
            child['departed_at']=None
            child['status']='AWAIT_DEPARTURE'
        e.feed(1,bar(1,TAP_START+15*60,(18.5,19,17.6,18.9)))
        self.assertFalse(any(x['kind']=='RETURN_OBSERVED' for x in e.events))
        e.feed(1,bar(1,TAP_START+16*60,(18.9,19.8,18.5,19.5)))
        self.assertTrue(any(x['kind']=='CHILD_DEPARTURE_OBSERVED' for x in e.events))
        self.assertFalse(any(x['kind']=='RETURN_OBSERVED' for x in e.events))
        e.feed(1,bar(1,TAP_START+17*60,(19.5,19.8,19.1,19.4)))
        self.assertTrue(any(x['kind']=='RETURN_OBSERVED' for x in e.events))

    def test_known_unreached_target_allows_only_research_review(self):
        e=setup_engine(15)
        for bos in e._bos.values():
            bos['target']={'price':22,'known_at':TAP_START,'reached':False,'reached_at':None}
        e.feed(1,bar(1,TAP_START+15*60,M1[15]))
        e.feed(1,bar(1,TAP_START+16*60,M1[16]))
        reviews=[x for x in e.events if x['kind']=='ENTRY_REVIEW']
        self.assertTrue(reviews)
        self.assertTrue(all(x['review_status']=='RESEARCH_REVIEW_ONLY' and not x['execution_allowed'] for x in reviews))

    def test_rebreak_same_major_reference_does_not_create_new_bos(self):
        e=Engine(SPEC)
        for i,p in enumerate(HTF[:11]):e.feed(240,bar(240,i*14400,p))
        original=len([x for x in e.events if x['kind']=='MAJOR_BOS'])
        # No newly confirmed external high; simply cross the old broken level.
        e.feed(240,bar(240,11*14400,(17,17.8,15,15.5)))
        e.feed(240,bar(240,12*14400,(15.5,17.9,15.2,17)))
        self.assertEqual(original,len([x for x in e.events if x['kind']=='MAJOR_BOS']))

    def test_owner_internal_counter_break_does_not_reverse_major(self):
        e=Engine(SPEC)
        for i,p in enumerate(HTF[:11]):e.feed(240,bar(240,i*14400,p))
        aid=e.snapshot()['active_authorities'][240]
        self.assertIsNotNone(aid)
        # Close through the preceding candle low, but still above protected9.
        e.feed(240,bar(240,11*14400,(17,17.8,10,10.5)))
        self.assertEqual(aid,e.snapshot()['active_authorities'][240])
        self.assertEqual(e.snapshot()['authorities'][aid]['direction'],'long')

    def test_two_formations_same_members_deduplicate(self):
        bars=[bar(1,i*60,p) for i,p in enumerate(M1[:15])]
        p=pivot('low',11*60,15.3,'HL',13*60)
        ref=pivot('high',9*60,18,'HH',11*60)
        zones,rejected=formation_candidates(bars,p,ref,'long',bars[-1])
        self.assertEqual(len(zones),1)
        self.assertEqual(zones[0]['member_times'],[600,660])
        self.assertEqual(zones[0]['branch_names'],['A_OPPOSITE_RUN','B_WHOLE_BASE'])
        self.assertFalse(rejected)

    def test_whole_base_includes_reversal_green_and_never_bos(self):
        ps=[(18,19,17,18),(17,18,15.5,16),(16,16.5,15,15.2),(15.2,16.2,14.9,15.5),(15.5,18.5,15.4,18),(18,20,17.5,19.5)]
        bs=[bar(1,i*60,p) for i,p in enumerate(ps)]
        p=pivot('low',180,14.9,'HL',300);r=pivot('high',0,19,'HH',60)
        zones,rejected=formation_candidates(bs,p,r,'long',bs[-1])
        self.assertEqual(len(zones),1)
        self.assertEqual(zones[0]['branch_names'],['B_WHOLE_BASE'])
        self.assertEqual(zones[0]['member_times'],[60,120,180])
        self.assertTrue(any(x['reason']=='FAR_EDGE_BREACH_BEFORE_RECOGNITION' for x in rejected))
        self.assertNotIn(bs[-1]['time'],zones[0]['member_times'])

    def test_different_members_same_bounds_retained(self):
        ps=[(18,19,17,18),(17,18,15,16),(16,16.5,15,15.5),(15.5,16,15.2,15.8),(15.8,20,15.5,19.5)]
        bs=[bar(1,i*60,p) for i,p in enumerate(ps)]
        p=pivot('low',120,15,'HL',240);r=pivot('high',0,19,'HH',60)
        zones,_=formation_candidates(bs,p,r,'long',bs[-1])
        self.assertEqual(len(zones),2)
        self.assertEqual({(x['zone_low'],x['zone_high']) for x in zones},{(15,18)})
        self.assertNotEqual(zones[0]['member_times'],zones[1]['member_times'])

    def test_formation_never_uses_future_suffix(self):
        bs=[bar(1,i*60,p) for i,p in enumerate(M1)]
        p=pivot('low',660,15.3,'HL',780);r=pivot('high',540,18,'HH',660)
        self.assertEqual(formation_candidates(bs,p,r,'long',bs[14]),formation_candidates(bs[:15],p,r,'long',bs[14]))

    def test_bearish_mirror(self):
        mirror=lambda p:(30-p[0],30-p[2],30-p[1],30-p[3])
        e=Engine(SPEC)
        records=[(tf,bar(tf,b['time'],mirror((b['open'],b['high'],b['low'],b['close'])))) for tf,b in higher_records()]
        e.feed_batch(records)
        for i,p in enumerate(M1):e.feed(1,bar(1,TAP_START+i*60,mirror(p)))
        self.assertTrue(any(x['kind']=='M1_TRANSITION_BOS' and x['direction']=='short' for x in e.events))
        self.assertTrue(any(x['kind']=='RESPECT_OBSERVED' for x in e.events))

    def test_pivot_known_time_and_ambiguous_outside_reset(self):
        t=SwingTracker(60)
        events=[]
        for i,p in enumerate(HTF[:3]):events+=t.push(bar(1,i*60,p))
        points=[x['pivot'] for x in events if x['kind']=='PIVOT_CONFIRMED']
        self.assertEqual(points[0]['time'],60)
        self.assertEqual(points[0]['known_at'],180)
        t=SwingTracker(60)
        t.push(bar(1,0,(10,11,9,10)));t.push(bar(1,60,(10,14,10,13)))
        events=t.push(bar(1,120,(13,15,8,9)))
        self.assertEqual(events[0]['kind'],'AMBIGUOUS_TURN')
        self.assertFalse(t.pivots)


if __name__=='__main__':unittest.main()
