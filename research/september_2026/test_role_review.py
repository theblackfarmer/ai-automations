"""Public Engine synthetic fixtures only; no acquired OHLC or live source reads."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
import role_review as rr

HERE=Path(__file__).resolve().parent
ENGINE=Path(os.environ.get('MONTH_ENGINE_SOURCE',str(HERE/'engine.py')))
module_spec=importlib.util.spec_from_file_location('role_fixture_engine',ENGINE)
eng=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(eng)
RULES=json.loads((HERE/'ROLE_RULES.json').read_text())
SPEC=json.loads((HERE/'SYNTHETIC_SPEC.json').read_text())
SPEC['scope'].update(warmup_start='1970-01-01T00:00:00Z',study_start='1970-01-01T00:00:00Z',end_exclusive='1970-01-04T00:00:00Z')
SPEC['scope']['prior_exposure']='SYNTHETIC PUBLIC FEED FIXTURE ONLY'
HTF=[(10,11,9,10),(10,14,10,13),(13,13,9.5,9.8),(10,11,7,8),(8,12,8,11.5),(11.5,16,11,15),
     (15,15.5,10,10.5),(10.5,11,9,9.5),(9.5,13,9.5,12),(12,15,11.5,14),(14,18,13,17),(17,17.8,16.5,17)]
M1=[(16,16,14,15.5),(15.5,17,15,16.5),(16.5,16.8,14.5,14.8),(14.8,15.5,14,14.2),
    (14.2,16,14.1,15.8),(15.8,17.5,15.5,17.2),(17.2,17.4,15.2,15.3),(15.3,15.7,14.8,15),
    (15,16.5,14.9,16),(16,18,15.8,17.8),(17.8,17.9,15.7,15.75),(15.75,16,15.3,15.5),
    (15.5,17,15.4,16.5),(16.5,17.8,16.2,17.5),(17.5,19,17,18.5),(18.5,18.7,17.6,18),
    (18,19,17.95,18.8),(18.8,19,15,15.5)]


def fixture(count=18):
    def bar(tf,t,p):
        return dict(time=t,known_at=t+tf*60,complete=True,source_id=f'SYNTHETIC:{tf}:{t}',**dict(zip(rr.OHLC,p)))
    records=[(240,bar(240,i*14400,p)) for i,p in enumerate(HTF[:11])]
    records += [(60,bar(60,(32+i)*3600,p)) for i,p in enumerate(HTF)]
    records += [(1,bar(1,44*3600+i*60,p)) for i,p in enumerate(M1[:count])]
    e=eng.Engine(SPEC);e.feed_batch(records)
    grouped=defaultdict(list)
    for tf,b in records:grouped[b['known_at']].append([tf,b])
    plan=[[known,[],sorted(batch,key=lambda x:-x[0])] for known,batch in sorted(grouped.items())]
    return e.events,plan


def sealed_contract_fixture(root,events,plan):
    """Synthetic journal envelope only; native resampling is a separate gate."""
    root=Path(root);root.mkdir();captures=root/'captures';captures.mkdir()
    raw_spec=(json.dumps(SPEC)+'\n').encode();raw_source=b'epoch,open,high,low,close\n'
    raw=dict(spec=raw_spec,source=raw_source,engine=b'# inert synthetic code capture\n',runner=b'# inert synthetic runner\n',
             clarifications=json.dumps({'spec_sha256':rr.digest(raw_spec)}).encode(),
             acquisition=json.dumps({'csv':{'sha256':rr.digest(raw_source)},'synthetic_contract_only':True}).encode())
    names=dict(spec='SPEC.json',source='source.csv',engine='engine.py',runner='month_replay.py',
               clarifications='IMPLEMENTATION_CLARIFICATIONS.json',acquisition='acquisition.json')
    manifest={'captures':{}}
    for key,data in raw.items():
        path='captures/'+names[key];(root/path).write_bytes(data)
        manifest['captures'][key]={'path':path,'sha256':rr.digest(data)};manifest[key+'_sha256']=rr.digest(data)
    inputs=[];head='0'*64
    for known,gaps,batch in plan:
        record=dict(previous_hash=head,known_at=known,input_sha256=rr.digest([gaps,batch]),
                    event_ids=[e['id'] for e in events if e['known_at']==known])
        head=rr.digest(record);record['record_hash']=head;inputs.append(record)
    saved={'MANIFEST.json':json.dumps(manifest),'SNAPSHOT.json':json.dumps(dict(event_count=len(events),event_head=rr.digest(events),spec_hash=rr.digest(SPEC))),
           'RESULT.json':json.dumps(dict(last_input_hash=head,status='SYNTHETIC_PARTIAL',complete_month_claim=False)),
           'sealed-plan.jsonl':''.join(rr.canon(x)+'\n' for x in plan),'events.jsonl':''.join(rr.canon(x)+'\n' for x in events),
           'input-audit.jsonl':''.join(rr.canon(x)+'\n' for x in inputs),'coverage.jsonl':''}
    for name,text in saved.items():(root/name).write_text(text)
    completion=dict(status='SAVED_OUTPUT_RECONCILED',execution_allowed=False,
        output_hashes={n:rr.digest((root/n).read_bytes()) for n in saved},captured_input_hashes={k:rr.digest(v) for k,v in raw.items()})
    (root/'COMPLETION.json').write_text(json.dumps(completion))
    return root


def violations(result):return {v['rule'] for v in result['violations']}


class RoleReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.events,cls.plan=fixture()
    def run_review(self,events=None,plan=None):
        return rr.review(self.events if events is None else events,self.plan if plan is None else plan,SPEC,RULES)
    def mutated(self,kind,patch):
        events=copy.deepcopy(self.events);target=next(e for e in events if e['kind']==kind);patch(target);return events
    def test_public_engine_full_sequence_keeps_unknowns_no_approval(self):
        result=self.run_review()
        self.assertEqual(result['violations'],[])
        kinds={r['kind'] for r in result['rows']}
        for kind in ('MAJOR_BOS','HTF_LINK','HTF_TAP','CHILD_CANDIDATE','RETURN_OBSERVED','RESPECT_OBSERVED','CHILD_ZONE_BREACHED'):
            self.assertIn(kind,kinds)
        self.assertFalse(result['teacher_verified']);self.assertFalse(result['specialist_attestations_created'])
        self.assertIsNone(result['performance'])
        self.assertTrue(any(r['unresolved'] for r in result['rows']))
    def test_missing_actual_h4_bos_cannot_be_replaced_by_link_label(self):
        events=[e for e in self.events if not(e['kind']=='MAJOR_BOS' and e['timeframe']==240)]
        result=self.run_review(events)
        self.assertIn('ACTUAL_4H_AND_1H_MAJOR_AUTHORITIES',violations(result))
        self.assertIn('CHILD_MATCHES_TAPPED_PARENT_AND_LINK',violations(result))
    def test_missing_actual_h1_bos_is_rejected(self):
        events=[e for e in self.events if not(e['kind']=='MAJOR_BOS' and e['timeframe']==60)]
        self.assertIn('ACTUAL_4H_AND_1H_MAJOR_AUTHORITIES',violations(self.run_review(events)))
    def test_tap_cannot_use_context_recognized_at_its_close(self):
        events=copy.deepcopy(self.events)
        tap=next(e for e in events if e['kind']=='HTF_TAP')
        link=next(e for e in events if e['kind']=='HTF_LINK');link['known_at']=tap['known_at']
        events.sort(key=lambda e:e['known_at'])
        self.assertIn('PARENTS_KNOWN_BEFORE_TAP',violations(self.run_review(events)))
    def test_false_progression_label_and_comparator_are_detected(self):
        for patch in [lambda e:e['comparison_chains']['high'][-1].update(label='LH'),
                      lambda e:e['comparison_chains']['low'][-1]['comparator'].update(id='ABSENT')]:
            result=self.run_review(self.mutated('M1_TRANSITION_BOS',patch))
            self.assertIn('TWO_POST_TAP_STRICT_PAIRS_WITH_COMPARATORS',violations(result))
    def test_pretap_pivot_cannot_count_as_posttap_evidence(self):
        tap=next(e for e in self.events if e['kind']=='HTF_TAP')
        events=self.mutated('M1_TRANSITION_BOS',lambda e:e['comparison_chains']['high'][-1].update(time=tap['bar_start']))
        self.assertIn('TWO_POST_TAP_STRICT_PAIRS_WITH_COMPARATORS',violations(self.run_review(events)))
    def test_reference_and_pullback_preserved_not_easy_substitute(self):
        events=self.mutated('M1_TRANSITION_BOS',lambda e:e.update(reference=copy.deepcopy(e['comparison_chains']['high'][0])))
        self.assertIn('LATEST_REFERENCE_AND_INTERVENING_PULLBACK',violations(self.run_review(events)))
    def test_structure_cannot_become_known_on_bos_start(self):
        events=self.mutated('M1_TRANSITION_BOS',lambda e:e.update(structure_known_at=e['bar_start']))
        self.assertIn('STRUCTURE_KNOWN_STRICTLY_BEFORE_BOS',violations(self.run_review(events)))
    def test_zone_needs_whole_wicks_and_specific_bos(self):
        for patch,rule in [(lambda e:e.update(zone_low=e['zone_low']+0.1),'WHOLE_FORMATION_MEMBER_BOUNDS'),
                           (lambda e:e.update(bos_id='ABSENT'),'FORMATION_BOUND_TO_SPECIFIC_BOS')]:
            self.assertIn(rule,violations(self.run_review(self.mutated('CHILD_CANDIDATE',patch))))
    def test_duplicate_same_route_members_rejected_and_cross_route_grouped(self):
        events=copy.deepcopy(self.events);index=next(i for i,e in enumerate(events) if e['kind']=='CHILD_CANDIDATE')
        extra=copy.deepcopy(events[index]);extra['id']='duplicate-proposal';extra['child_id']='duplicate-child';extra['zone_id']='duplicate-child'
        events.insert(index+1,extra)
        self.assertIn('SAME_ROUTE_EQUAL_MEMBERS_DEDUPLICATED',violations(self.run_review(events)))
        result=self.run_review()
        self.assertTrue(all(g['physical_count']==1 for g in result['physical_origin_groups']))
    def test_return_respect_order_and_child_failure_do_not_flip_parent(self):
        events=[e for e in self.events if e['kind']!='RETURN_OBSERVED']
        self.assertIn('SEPARATE_RETURN_THEN_RESPECT',violations(self.run_review(events)))
        result=self.run_review();failed=next(r for r in result['rows'] if r['kind']=='CHILD_ZONE_BREACHED')
        self.assertTrue(failed['evidence']['child_failure_does_not_flip_parent'])
        self.assertFalse(any(e['kind']=='AUTHORITY_REVOKED' for e in self.events))
    def test_child_pivot_does_not_supply_major_authority(self):
        events=self.mutated('MAJOR_BOS',lambda e:e.update(timeframe=1))
        self.assertIn('MAJOR_OWNER_4H_OR_1H',violations(self.run_review(events)))
    def test_prefix_rows_identical_without_later_outcome_repair(self):
        child=next(e for e in self.events if e['kind']=='CHILD_CANDIDATE');cut=child['known_at']
        prefix=self.run_review([e for e in self.events if e['known_at']<=cut],[a for a in self.plan if a[0]<=cut])
        full=self.run_review()
        self.assertEqual(prefix['rows'],[r for r in full['rows'] if r['known_at']<=cut])
    def test_empty_output_does_not_claim_method_proficiency(self):
        result=rr.review([],[],SPEC,RULES)
        self.assertTrue(any('No major BOS' in w for w in result['warnings']))
        self.assertFalse(result['teacher_verified'])
    def test_outputs_are_exclusive_bound_and_both_clocks(self):
        result=self.run_review()
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'review';receipt=rr.write_review(result,out,{'synthetic_only':True},rr.digest(RULES))
            saved=json.loads((out/'REVIEW.json').read_text())
            self.assertIn('CT',saved['rows'][0]['known_clocks']);self.assertIn('ET',saved['rows'][0]['known_clocks'])
            self.assertEqual(receipt['files']['REVIEW.json'],rr.digest((out/'REVIEW.json').read_bytes()))
            self.assertIn('known_CT,known_ET',(out/'chronology.csv').read_text())
            with self.assertRaises(FileExistsError):rr.write_review(result,out,{},rr.digest(RULES))
    def test_existing_hourly_context_can_precede_four_hour_recognition(self):
        row=next(r for r in self.run_review()['rows'] if r['kind']=='HTF_LINK')
        self.assertLess(row['evidence']['h1_known_at'],row['evidence']['h4_known_at'])
        self.assertEqual(row['reasons'],[])
        for row in self.run_review()['rows']:
            if row['kind']=='CHILD_CANDIDATE':
                self.assertTrue(all(a['known_before_tap'] for a in row['evidence']['parent_authorities'].values()))

    def test_invalid_tap_or_parent_propagates_to_child(self):
        for kind,patch in [('HTF_TAP',lambda e:e.update(zone_id='ABSENT')),
                           ('MAJOR_BOS',lambda e:e['reference'].update(price=0.01))]:
            result=self.run_review(self.mutated(kind,patch))
            child_rows=[r for r in result['rows'] if r['kind']=='CHILD_CANDIDATE']
            self.assertTrue(any('CHILD_MATCHES_TAPPED_PARENT_AND_LINK' in r['reasons'] for r in child_rows))

    def test_prior_countertrend_evidence_cannot_be_fabricated(self):
        events=self.mutated('HTF_TAP',lambda e:e['pre_tap_pivots'].append(dict(id='FAKE',time=0,known_at=1,kind='high',label='LH',price=1,source_id='FAKE')))
        self.assertIn('PRE_TAP_PIVOT_IDENTITIES',violations(self.run_review(events)))

    def test_child_failure_and_later_owner_close_remain_distinct(self):
        records=[(tf,b) for known,gaps,batch in self.plan for tf,b in batch]
        b=dict(time=44*3600,known_at=45*3600,complete=True,source_id='SYNTHETIC:60:158400',open=17,high=17,low=8,close=8.5)
        records.append((60,b));engine=eng.Engine(SPEC);engine.feed_batch(records)
        plan=copy.deepcopy(self.plan)+[[b['known_at'],[],[[60,b]]]]
        result=self.run_review(engine.events,plan)
        self.assertEqual(result['violations'],[])
        failures=[r for r in result['rows'] if r['kind']=='CHILD_ZONE_BREACHED']
        revoked=next(r for r in result['rows'] if r['kind']=='AUTHORITY_REVOKED')
        self.assertLess(max(r['known_at'] for r in failures),revoked['known_at'])
        self.assertTrue(next(c['pass_check'] for c in revoked['checks'] if c['rule']=='OWNER_PROTECTED_CLOSE_NOT_CHILD_WICK'))

    def test_cross_parent_route_same_native_origin_counts_once(self):
        groups=self.run_review()['physical_origin_groups']
        shared=[g for g in groups if g['timeframe']==1 and g['route_count']>1]
        self.assertTrue(shared)
        self.assertTrue(all(g['physical_count']==1 for g in shared))

    def test_sealed_envelope_load_and_hash_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=sealed_contract_fixture(Path(temp)/'sealed',self.events,self.plan)
            events,plan,spec,bindings=rr.load_sealed(root)
            self.assertEqual(events,self.events);self.assertEqual(plan,self.plan)
            self.assertEqual(rr.review(events,plan,spec,RULES)['violations'],[])
            self.assertFalse(bindings['complete_month_claim'])
            self.assertEqual(bindings['source_status'],'SYNTHETIC_PARTIAL')
            source=root/'captures/source.csv';source.write_bytes(source.read_bytes()+b'CHANGED')
            with self.assertRaisesRegex(ValueError,'Changed captured source'):rr.load_sealed(root)

    def test_public_rule_catalog_is_self_contained(self):
        catalog,sha=rr.role_sources(HERE/'ROLE_RULES.json')
        self.assertEqual(catalog,RULES);self.assertEqual(len(sha),64)
        self.assertNotIn('sources',catalog);self.assertNotIn('user_quote',SPEC)

    def test_mutated_mechanics_refuse_review(self):
        spec=copy.deepcopy(SPEC);spec['mechanics']['m1_transition']='change the count'
        with self.assertRaisesRegex(ValueError,'Unfrozen mechanics'):rr.review(self.events,self.plan,spec,RULES)

if __name__=='__main__':unittest.main()
