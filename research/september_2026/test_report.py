"""Synthetic-only rendering and artifact-integrity tests. No acquired prices read."""
import copy
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import report

PROJECT = Path(__file__).resolve().parent
RUNNER = Path(os.environ.get('MONTH_RUNNER_SOURCE', str(PROJECT/'month_replay.py')))
ENGINE = Path(os.environ.get('MONTH_ENGINE_SOURCE', str(PROJECT/'engine.py')))
spec = importlib.util.spec_from_file_location('runner_for_synthetic_report_test', RUNNER)
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
HTF = [(10,11,9,10),(10,14,10,13),(13,13,9.5,9.8),(10,11,7,8),
       (8,12,8,11.5),(11.5,16,11,15),(15,15.5,10,10.5),(10.5,11,9,9.5),
       (9.5,13,9.5,12),(12,15,11.5,14),(14,18,13,17),(17,17.8,16.5,17)]
M1 = [(16,16,14,15.5),(15.5,17,15,16.5),(16.5,16.8,14.5,14.8),(14.8,15.5,14,14.2),
      (14.2,16,14.1,15.8),(15.8,17.5,15.5,17.2),(17.2,17.4,15.2,15.3),(15.3,15.7,14.8,15),
      (15,16.5,14.9,16),(16,18,15.8,17.8),(17.8,17.9,15.7,15.75),(15.75,16,15.3,15.5),
      (15.5,17,15.4,16.5),(16.5,17.8,16.2,17.5),(17.5,19,17,18.5),
      (18.5,18.7,17.6,18),(18,19,17.95,18.8)]


def synthetic_fixture(root, linked=True):
    """A real runner invocation over coherent hand-defined native minutes."""
    root=Path(root);root.mkdir()
    start=runner.epoch('2026-08-03T04:00:00Z');end=start+48*3600
    rows={}
    for i,(o,h,l,c) in enumerate(HTF):
        times=[t for t in range(start+i*14400,start+(i+1)*14400,60) if runner.session(t) is True]
        for t in times:rows[t]=dict(epoch=t,open=o,high=o,low=o,close=o)
        rows[times[0]].update(high=h,low=l)
        rows[times[-1]].update(high=max(o,c),low=min(o,c),close=c)
    if linked:
        local=start+44*3600
        # The coherent earlier 1M history has already reached 18. Add another
        # post-tap swing cycle so its pre-tap comparator is not erased.
        suffix=M1+[(18.8,18.9,16.7,17.5),(17.5,18,16.5,17),(17,19,16.8,18.8),
                   (18.8,18.95,18,18.8),(18.8,20,18.5,19.6),(19.6,19.8,18.3,19),
                   (19,20,18.9,19.8),(19.8,20,16,16.2)]
        for i,p in enumerate(suffix):rows[local+i*60]=dict(epoch=local+i*60,**dict(zip(report.OHLC,p)))
        # Deliberately include an adverse later move; never select only respect.
        for t in range(local+len(suffix)*60,end,60):rows[t]=dict(epoch=t,open=16.2,high=16.2,low=16.2,close=16.2)
    source=root/'source.csv'
    with source.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['epoch',*report.OHLC]);w.writeheader();w.writerows(rows.values())
    scope=dict(symbol='NAS100 CFD',provider='Dukascopy USATECHIDXUSD',side='BID',
               warmup_start=report.clocks(start)['UTC'],study_start=report.clocks(start)['UTC'],
               end_exclusive=report.clocks(end)['UTC'],prior_exposure='SYNTHETIC FIXTURE ONLY. No acquired prices.')
    sp=dict(id='SEPTEMBER_4H_1H_1M_V1',execution_allowed=False,scope=scope,synthetic_fixture=True)
    spec_path=root/'SPEC.json';spec_path.write_text(json.dumps(sp))
    clar=root/'clarification.json';clar.write_text(json.dumps(dict(spec_sha256=report.digest(spec_path.read_bytes()))))
    acq=dict(symbol=scope['symbol'],feed=scope['provider'],side='BID',start={'utc':scope['warmup_start']},
             end_exclusive={'utc':scope['end_exclusive']},expected_hours=48,processed_hours=48,
             hour_states={'OK':48},minute_count=len(rows),unresolved_hours=[],unattempted_hours=[],
             all_hours_validated_or_explicit_empty_200=True,
             csv=dict(path='source.csv',sha256=report.digest(source.read_bytes()),bytes=source.stat().st_size,incomplete=False),
             synthetic_fixture=True)
    acq_path=root/'acquisition.json';acq_path.write_text(json.dumps(acq))
    out=root/'sealed'
    subprocess.run([sys.executable,'-B',str(RUNNER),'--source',str(source),'--acquisition',str(acq_path),
                    '--engine',str(ENGINE),'--spec',str(spec_path),'--clarifications',str(clar),'--output',str(out)],
                   check=True,capture_output=True,text=True)
    return out


def target_contract_fixture(later_selection=False, touch_at_respect=False):
    """Public Engine feed; synthetic HTF target recognition at 208800 only.

    This is a semantic event/source contract fixture, not a native resampling
    fixture. The separate sealed-run fixture exercises that full pipeline.
    """
    module_spec=importlib.util.spec_from_file_location('report_target_contract_engine', ENGINE)
    engine=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(engine)
    def bar(tf,t,p):
        return dict(time=t,known_at=t+tf*60,complete=True,source_id=f'SYNTHETIC:{tf}:{t}',
                    **dict(zip(report.OHLC,p)))
    count=17 if later_selection else 15
    stream=[(240,bar(240,i*14400,p)) for i,p in enumerate(HTF[:11])]
    hourly=[tuple(v+10 for v in p) for p in HTF]
    stream += [(60,bar(60,(44+i)*3600,p)) for i,p in enumerate(hourly)]
    for i in range(0,12,4):
        group=hourly[i:i+4]
        stream.append((240,bar(240,(44+i)*3600,(group[0][0],max(p[1] for p in group),
                                                    min(p[2] for p in group),group[-1][3]))))
    stream += [(60,bar(60,56*3600,(32,36,32,35))),
               (60,bar(60,57*3600,(35,36,19,M1[count-1][3]+10)))]
    start=208800 if later_selection else 208800-count*60
    stream += [(1,bar(1,start+i*60,tuple(v+10 for v in p))) for i,p in enumerate(M1[:count])]
    if not later_selection:
        stream += [(1,bar(1,208800,(28.5,28.7,27.6,28))),
                   (1,bar(1,208860,(28,36.1 if touch_at_respect else 29,27.95,28.8))),
                   (1,bar(1,208920,(28.8,36.1,28.5,35.9)))]
    e=engine.Engine(dict(id='SEPTEMBER_4H_1H_1M_V1',execution_allowed=False,test_fixture='SYNTHETIC'))
    e.feed_batch(stream)
    return e.events,{b['source_id']:b for tf,b in stream},{b['source_id']:tf for tf,b in stream}


def reseal_events(folder, events):
    """Hold all source and input bindings fixed while testing event semantics."""
    (folder/'events.jsonl').write_text(''.join(report.canon(e)+'\n' for e in events))
    snap=json.loads((folder/'SNAPSHOT.json').read_text());snap['event_head']=report.digest(events)
    (folder/'SNAPSHOT.json').write_text(report.canon(snap)+'\n')
    completion=json.loads((folder/'COMPLETION.json').read_text())
    for name in ('events.jsonl','SNAPSHOT.json'):
        completion['output_hashes'][name]=report.digest((folder/name).read_bytes())
    (folder/'COMPLETION.json').write_text(report.canon(completion)+'\n')


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='month-report-tests-')
        cls.root=Path(cls.temp.name)
        cls.sealed=synthetic_fixture(cls.root/'fixture')
        cls.loaded=report.load_run(cls.sealed)
        cls.cases=report.select_cases(cls.loaded['events'],cls.loaded['start'],cls.loaded['end'])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_sealed_runner_case_has_child_return_respect_and_later_failure(self):
        kinds=Counter(e['kind'] for e in self.loaded['events'])
        for k in ('MAJOR_BOS','HTF_LINK','HTF_TAP','M1_TRANSITION_BOS','CHILD_CANDIDATE',
                  'RETURN_OBSERVED','RESPECT_OBSERVED','CHILD_ZONE_BREACHED'):
            self.assertGreater(kinds[k],0,k)
        self.assertTrue(self.cases)

    def test_every_bos_indexed_even_unresolved_formation(self):
        run=copy.deepcopy(self.loaded)
        first=next(e for e in run['events'] if e['kind']=='MAJOR_BOS')
        run['events']=[e for e in run['events'] if not (e.get('bos_id')==first['bos_id'] and e['kind']=='ZONE_CANDIDATE')]
        rows=report.bos_index(run)
        expected=[e for e in run['events'] if e['kind'] in ('MAJOR_BOS','M1_TRANSITION_BOS')]
        self.assertEqual([e['event_id'] for e in rows],[e['id'] for e in expected])
        self.assertEqual(next(e for e in rows if e['event_id']==first['id'])['status'],'NO_ORIGIN_RESOLVED')

    def test_context_selection_earliest_ten_no_outcome_condition(self):
        links=[dict(id=f'e{i}',kind='HTF_LINK',link_id=f'l{i}',known_at=i+10,
                    direction='short' if i%2 else 'long',h4_authority_id=f'4h{i}',h1_authority_id=f'1h{i}') for i in range(12)]
        late=dict(id='later_winning_child',kind='CHILD_CANDIDATE',link_id='l11',known_at=90)
        result=report.select_cases(links+[late],0,100)
        self.assertEqual([x['link_id'] for x in result],[f'l{i}' for i in range(10)])
        self.assertTrue(all('no tap' in x['cutoff_basis'] for x in result))

    def test_warmup_link_only_included_when_still_active(self):
        event=dict(id='e',kind='HTF_LINK',link_id='l',known_at=5,direction='long',h4_authority_id='a',h1_authority_id='b')
        self.assertEqual(report.select_cases([event],10,20)[0]['decision_cutoff'],10)
        self.assertEqual(report.select_cases([event,dict(id='r',kind='LINK_REVOKED',link_id='l',known_at=9)],10,20),[])

    def test_before_return_cutoff_and_pivots_known_not_origin_only(self):
        case=self.cases[0];view=report.decision_view(self.loaded,case)
        self.assertTrue(all(e['known_at']<=view['cutoff'] for e in view['events']))
        self.assertTrue(all(b['known_at']<=view['cutoff'] for b in view['native']))
        self.assertFalse(any(e['kind']=='RETURN_OBSERVED' for e in view['events']))
        self.assertTrue(view['pivots'])
        self.assertTrue(all(p['known_at']<=view['cutoff'] for p in view['pivots']))
        future=copy.deepcopy(view['events'][-1]);future['known_at']=view['cutoff']+60
        future['reference']=dict(id='FUTURE',price=1000,time=view['cutoff']-600,known_at=view['cutoff']+60)
        self.assertFalse(any(p['id']=='FUTURE' for p in report.known_pivots(view['events']+[future],view['cutoff'])))

    def test_full_htf_prefix_retained(self):
        view=report.decision_view(self.loaded,self.cases[0])
        for tf in (240,60):
            self.assertEqual(view['bars'][tf][0]['time'],self.loaded['bars'][tf][0]['time'])
            self.assertGreater(len(view['bars'][tf]),5)

    def test_future_prices_do_not_change_decision_view(self):
        case=self.cases[0];before=report.decision_view(self.loaded,case)
        changed=copy.deepcopy(self.loaded)
        for b in changed['native'].values():
            if b['known_at']>case['decision_cutoff']:
                for key in report.OHLC:b[key]*=2
        for tf,bars in changed['bars'].items():
            for b in bars:
                if b['known_at']>case['decision_cutoff']:
                    for key in report.OHLC:b[key]*=2
        self.assertEqual(before,report.decision_view(changed,case))
        a=self.root/'prefix-before.png';b=self.root/'prefix-after.png'
        report.plot_case(self.loaded,case,a);report.plot_case(changed,case,b)
        self.assertEqual(report.digest(a.read_bytes()),report.digest(b.read_bytes()))

    def test_future_coverage_cannot_change_before_decision_image(self):
        case=self.cases[0];changed=copy.deepcopy(self.loaded);cutoff=case['decision_cutoff']
        future=[]
        for c in changed['coverage']:
            if c['timeframe'] in (60,240) and c['time']<cutoff<c['known_at']:
                c['status']='INCOMPLETE';future.append(c)
        self.assertTrue(future)
        for tf in (240,60):
            self.assertEqual(report.merged_spans(self.loaded['coverage'],tf,self.loaded['warm'],cutoff),
                             report.merged_spans(changed['coverage'],tf,changed['warm'],cutoff))
        a=self.root/'coverage-before.png';b=self.root/'coverage-after.png'
        report.plot_case(self.loaded,case,a);report.plot_case(changed,case,b)
        self.assertEqual(report.digest(a.read_bytes()),report.digest(b.read_bytes()))

    def test_shared_parent_does_not_import_other_link_children(self):
        case=dict(h4_authority_id='shared-H4',h1_authority_id='new-H1',link_id='new')
        events=[dict(id='parent',kind='ZONE_CANDIDATE',zone_id='parent',authority_id='shared-H4'),
                dict(id='old-tap',kind='HTF_TAP',zone_id='parent',tap_id='old-tap',link_id='old'),
                dict(id='old-child',kind='CHILD_CANDIDATE',zone_id='old-child',child_id='old-child',parent_zone_id='parent',tap_id='old-tap',link_id='old'),
                dict(id='old-return',kind='RETURN_OBSERVED',zone_id='old-child',child_id='old-child'),
                dict(id='old-reject',kind='CHILD_FORMATION_REJECTED',parent_zone_id='parent',tap_id='old-tap'),
                dict(id='new-tap',kind='HTF_TAP',zone_id='parent',tap_id='new-tap',link_id='new'),
                dict(id='new-child',kind='CHILD_CANDIDATE',zone_id='new-child',child_id='new-child',parent_zone_id='parent',tap_id='new-tap',link_id='new'),
                dict(id='new-return',kind='RETURN_OBSERVED',zone_id='new-child',child_id='new-child'),
                dict(id='parent-breach',kind='PARENT_ZONE_BREACHED',zone_id='parent',authority_id='shared-H4')]
        self.assertEqual([e['id'] for e in report.case_events(events,case)],
                         ['parent','new-tap','new-child','new-return','parent-breach'])

    def test_changed_capture_or_journal_blocks_report(self):
        for name in ('captures/source.csv','events.jsonl','sealed-plan.jsonl'):
            with self.subTest(name=name):
                d=self.root/('tamper-'+name.replace('/','-'))
                shutil.copytree(self.sealed,d)
                p=d/name;p.write_bytes(p.read_bytes()+b'\n')
                with self.assertRaises(ValueError):report.load_run(d)

    def test_resealed_future_member_still_rejected(self):
        d=self.root/'future-member';shutil.copytree(self.sealed,d)
        path=d/'sealed-plan.jsonl';plan=report.jsonlines(path.read_bytes())
        plan[-1][2][0][1]['member_times'].append(plan[-1][0])
        path.write_text(''.join(report.canon(p)+'\n' for p in plan))
        # Rebind the entire synthetic chain: rejection must come from semantic
        # future-membership validation, not a stale digest in the test fixture.
        inputs=report.jsonlines((d/'input-audit.jsonl').read_bytes());head='0'*64
        for record,(known,gaps,bars) in zip(inputs,plan):
            record['previous_hash']=head;record['input_sha256']=report.digest([gaps,bars])
            record.pop('record_hash');head=report.digest(record);record['record_hash']=head
        (d/'input-audit.jsonl').write_text(''.join(report.canon(r)+'\n' for r in inputs))
        result=json.loads((d/'RESULT.json').read_text());result['last_input_hash']=head
        (d/'RESULT.json').write_text(json.dumps(result))
        completion=json.loads((d/'COMPLETION.json').read_text())
        for name in ('sealed-plan.jsonl','input-audit.jsonl','RESULT.json'):
            completion['output_hashes'][name]=report.digest((d/name).read_bytes())
        (d/'COMPLETION.json').write_text(json.dumps(completion))
        with self.assertRaisesRegex(ValueError,'Invalid/future native membership'):report.load_run(d)

    def test_report_real_linked_fixture_and_exclusive_output(self):
        out=self.root/'report'
        metadata=report.write_report(self.loaded,out)
        self.assertGreater(len(metadata['cases']),0)
        for name in ('index.html','all-bos.json','REPORT_COMPLETION.json','case-01-decision.png',
                     'case-01-followthrough.png','case-01-return-detail.png','contact-sheet.png'):
            self.assertTrue((out/name).stat().st_size>100,name)
        ledger=json.loads((out/'case-01.json').read_text())
        self.assertTrue(all(e['known_at']<=self.cases[0]['decision_cutoff'] for e in self.loaded['events']
                            if e['id'] in ledger['decision_event_ids']))
        self.assertIn('SYNTHETIC TEST DATA',(out/'index.html').read_text())
        self.assertIn('CHILD_ZONE_BREACHED',(out/'case-01.html').read_text())
        with self.assertRaises(FileExistsError):report.write_report(self.loaded,out)

    def test_source_path_live_mutation_does_not_change_loaded_capture(self):
        before=report.digest(self.loaded['native'])
        live=self.sealed.parent/'source.csv';live.write_text('mutated original outside captures')
        self.assertEqual(report.digest(report.load_run(self.sealed)['native']),before)

    def test_resealed_future_origin_clock_rejected(self):
        d=self.root/'future-origin';shutil.copytree(self.sealed,d)
        events=report.jsonlines((d/'events.jsonl').read_bytes())
        origin=next(e for e in events if e['kind']=='CHILD_CANDIDATE')
        origin['selected_known_at']=origin['known_at']-60
        (d/'events.jsonl').write_text(''.join(report.canon(e)+'\n' for e in events))
        snapshot=json.loads((d/'SNAPSHOT.json').read_text());snapshot['event_head']=report.digest(events)
        (d/'SNAPSHOT.json').write_text(json.dumps(snapshot))
        completion=json.loads((d/'COMPLETION.json').read_text())
        for name in ('events.jsonl','SNAPSHOT.json'):
            completion['output_hashes'][name]=report.digest((d/name).read_bytes())
        (d/'COMPLETION.json').write_text(json.dumps(completion))
        with self.assertRaisesRegex(ValueError,'Future/backdated frozen origin'):report.load_run(d)

    def test_resealed_marker_cannot_repoint_or_change_native_timeframe(self):
        cutoff=self.cases[0]['decision_cutoff']
        future=next(t for t in sorted(self.loaded['native']) if t>cutoff)
        for name,patch in [('future',dict(bar_start=future)),('past',dict(bar_start=-1)),
                           ('wrong-tf',dict(timeframe=60))]:
            with self.subTest(name=name):
                folder=self.root/('marker-'+name);shutil.copytree(self.sealed,folder)
                events=copy.deepcopy(self.loaded['events'])
                tap=next(e for e in events if e['kind']=='HTF_TAP')
                if name=='past':patch=dict(bar_start=tap['bar_start']-60)
                tap.update(patch);reseal_events(folder,events)
                with self.assertRaisesRegex(ValueError,'source candle'):report.load_run(folder)

    def test_resealed_target_and_opposing_zone_require_actual_prior_lineage(self):
        cutoff=self.cases[0]['decision_cutoff']
        for name in ('future','backdated','opposing-only'):
            with self.subTest(name=name):
                folder=self.root/('target-'+name);shutil.copytree(self.sealed,folder)
                events=copy.deepcopy(self.loaded['events'])
                child=next(e for e in events if e['kind']=='CHILD_CANDIDATE')
                known=cutoff+60 if name=='future' else cutoff-60
                if name!='opposing-only':
                    child.update(target_known=True,target=dict(price=987654.125,known_at=known,
                                                                time=known-60,source_id='ABSENT'))
                if name!='backdated':
                    child['opposing_known_zones']=[dict(zone_id='ABSENT',timeframe=240,
                                                        zone_low=987650.,zone_high=987660.,known_at=known)]
                reseal_events(folder,events)
                with self.assertRaisesRegex(ValueError,'Target flags|Opposing zone'):report.load_run(folder)

    def test_direct_renderer_does_not_read_future_marker_candle(self):
        case=self.cases[0];run=copy.deepcopy(self.loaded)
        future=next(t for t in sorted(run['native']) if t>case['decision_cutoff'])
        tap=next(e for e in run['events'] if e['kind']=='HTF_TAP');tap['bar_start']=future
        self.assertIsNone(report.marker_candle(run,tap,case['decision_cutoff']))
        a=self.root/'direct-marker-before.png';b=self.root/'direct-marker-after.png'
        report.plot_case(run,case,a)
        for key in report.OHLC:run['native'][future][key]=1000000.
        report.plot_case(run,case,b)
        self.assertEqual(a.read_bytes(),b.read_bytes())

    def test_public_feed_target_recognition_same_close_is_hidden_then_available(self):
        events,sources,tfs=target_contract_fixture()
        report.validate_target_lineage(events,sources,tfs)
        child=next(e for e in events if e['kind']=='CHILD_CANDIDATE')
        recognized=next(e for e in events if e['kind']=='ORIGINAL_TARGET_KNOWN')
        respect=next(e for e in events if e['kind']=='RESPECT_OBSERVED')
        self.assertEqual(child['known_at'],recognized['known_at'])
        self.assertFalse(child['target_known']);self.assertTrue(respect['target_known'])
        self.assertFalse(respect['target_already_reached'])
        bad=copy.deepcopy(events)
        proposal=next(e for e in bad if e['kind']=='CHILD_CANDIDATE')
        proposal.update(target_known=True,target=copy.deepcopy(recognized['target']))
        with self.assertRaisesRegex(ValueError,'Target flags'):
            report.validate_target_lineage(bad,sources,tfs)

    def test_public_feed_prior_target_valid_at_selection_and_current_retouch(self):
        events,sources,tfs=target_contract_fixture(later_selection=True)
        child=next(e for e in events if e['kind']=='CHILD_CANDIDATE')
        self.assertTrue(child['target_known']);self.assertFalse(child['target_already_reached'])
        report.validate_target_lineage(events,sources,tfs)
        events,sources,tfs=target_contract_fixture(touch_at_respect=True)
        respect=next(e for e in events if e['kind']=='RESPECT_OBSERVED')
        self.assertTrue(respect['target_already_reached'])
        self.assertEqual(respect['target']['reached_at'],respect['known_at'])
        self.assertGreater(respect['target']['reached_at'],respect['bar_start'])
        report.validate_target_lineage(events,sources,tfs)
        bad=[e for e in events if e['kind']!='ORIGINAL_TARGET_REACHED']
        with self.assertRaisesRegex(ValueError,'Target flags'):
            report.validate_target_lineage(bad,sources,tfs)

    def test_opposing_zone_schema_requires_real_source_bound_known_geometry(self):
        # Deliberately synthetic schema control: the validator proves ancestry,
        # not whether a strategy would have emitted this opposing formation.
        run=copy.deepcopy(self.loaded);events=run['events']
        parent=copy.deepcopy(next(e for e in events if e['kind']=='ZONE_CANDIDATE'))
        bos=copy.deepcopy(next(e for e in events if e['kind']=='MAJOR_BOS' and e['bos_id']==parent['bos_id']))
        bos.update(id='synthetic-opposite-bos',bos_id='synthetic-opposite-bos',
                   direction='short',authority_id='synthetic-opposite-owner')
        parent.update(id='synthetic-opposite-zone',zone_id='synthetic-opposite-zone',
                      bos_id=bos['bos_id'],direction='short',authority_id=bos['authority_id'])
        index=next(i for i,e in enumerate(events) if e['kind']=='CHILD_CANDIDATE')
        # Preserve chronological journal order at the owner's recognition cutoff.
        insert=next(i for i,e in enumerate(events) if e['known_at']>parent['known_at'])
        events[insert:insert]=[bos,parent]
        child=next(e for e in events if e['kind']=='CHILD_CANDIDATE')
        child['opposing_known_zones']=[{k:parent[k] for k in
            ('zone_id','timeframe','zone_low','zone_high','known_at')}]
        report.validate_target_lineage(events,run['sources'],run['source_timeframes'])
        child['opposing_known_zones'][0]['zone_high']+=1
        with self.assertRaisesRegex(ValueError,'Opposing zone'):
            report.validate_target_lineage(events,run['sources'],run['source_timeframes'])

    def test_direct_html_rechecks_target_lineage(self):
        run=copy.deepcopy(self.loaded);child=next(e for e in run['events'] if e['kind']=='CHILD_CANDIDATE')
        child.update(target_known=True,target=dict(price=987654.125,known_at=child['bar_start']-60))
        with self.assertRaisesRegex(ValueError,'Target flags'):
            report.make_case_html(run,self.cases[0],report.case_events(run['events'],self.cases[0]),
                                  self.root/'invalid-direct.html')

    def test_clocks_have_both_zones_at_dst_boundary(self):
        t=report.epoch('2026-09-01T00:00:00Z')
        self.assertEqual(report.clocks(t)['CT'],'2026-08-31T19:00:00-05:00')
        self.assertEqual(report.clocks(t)['ET'],'2026-08-31T20:00:00-04:00')


if __name__=='__main__':
    unittest.main()
