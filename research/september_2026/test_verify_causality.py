"""Synthetic only. Never reads acquired market OHLC."""
import copy
import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import engine
import month_replay as runner
import verify_causality as audit

SPEC = {'id':'SEPTEMBER_4H_1H_1M_V1','execution_allowed':False,'fixture':'SYNTHETIC_ONLY'}
HTF = [(10,11,9,10),(10,14,10,13),(13,13,9.5,9.8),(10,11,7,8),
       (8,12,8,11.5),(11.5,16,11,15),(15,15.5,10,10.5),(10.5,11,9,9.5),
       (9.5,13,9.5,12),(12,15,11.5,14),(14,18,13,17),(17,17.8,16.5,17)]
M1 = [(16,16,14,15.5),(15.5,17,15,16.5),(16.5,16.8,14.5,14.8),(14.8,15.5,14,14.2),
      (14.2,16,14.1,15.8),(15.8,17.5,15.5,17.2),(17.2,17.4,15.2,15.3),(15.3,15.7,14.8,15),
      (15,16.5,14.9,16),(16,18,15.8,17.8),(17.8,17.9,15.7,15.75),(15.75,16,15.3,15.5),
      (15.5,17,15.4,16.5),(16.5,17.8,16.2,17.5),(17.5,19,17,18.5),
      (18.5,18.7,17.6,18),(18,19,17.95,18.8)]


def bar(tf,t,prices):
    return dict(time=t,known_at=t+tf*60,open=prices[0],high=prices[1],low=prices[2],close=prices[3],complete=True,source_id=f'SYNTHETIC:{tf}:{t}')


def native_plan():
    records = [(240,bar(240,i*14400,p)) for i,p in enumerate(HTF[:11])]
    records += [(60,bar(60,(32+i)*3600,p)) for i,p in enumerate(HTF)]
    records += [(1,bar(1,44*3600+i*60,p)) for i,p in enumerate(M1)]
    groups = defaultdict(list)
    for tf,b in records: groups[b['known_at']].append((tf,b))
    return [(known,[],sorted(bs,key=lambda pair: audit.TFS.index(pair[0]))) for known,bs in sorted(groups.items())]


def synthetic_source():
    start = runner.epoch('2026-08-03T00:00:00Z')
    rows = {}
    for i,(o,h,l,c) in enumerate(HTF):
        times = [t for t in range(start+i*14400,start+(i+1)*14400,60) if runner.session(t) is True]
        for t in times: rows[t] = dict(time=t,open=o,high=o,low=o,close=o)
        rows[times[0]].update(high=h,low=l)
        rows[times[-1]].update(high=max(o,c),low=min(o,c),close=c)
    return start, start+len(HTF)*14400, rows


def input_journal(plan,events):
    head='0'*64; rows=[]
    for known,gaps,bs in plan:
        row=dict(known_at=known,previous_hash=head,input_sha256=audit.digest([gaps,bs]),event_ids=[e['id'] for e in events if e['known_at']==known])
        head=audit.digest(row); row['record_hash']=head; rows.append(row)
    return rows


def contract_replay(engine_type,spec,plan,out=None):
    """Mixed native synthetic fixture: engine boundary only, no raw aggregation claim."""
    if out is not None: raise ValueError('synthetic contract adapter cannot write captures')
    e=engine_type(spec)
    for known,gaps,bars in plan:
        for gap in gaps: e.gap(known,gap)
        if bars: e.feed_batch(bars)
    return e.events,e.snapshot(),'0'*64


class ColdPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=native_plan()
        cls.events,_,_=contract_replay(engine.Engine,SPEC,cls.plan)

    def test_real_engine_every_required_cutoff_cold_reexecuted(self):
        invocations=[]
        def tracked(cls,spec,plan,out=None):
            invocations.append(len(plan)); return contract_replay(cls,spec,plan,out=out)
        result=audit.audit(engine.Engine,tracked,SPEC,self.plan,self.events)
        self.assertEqual(result['failures'],[])
        self.assertEqual(result['audited_cutoffs'],len({e['known_at'] for e in self.events if audit.is_decision(e)}))
        self.assertEqual(len(invocations),1+result['audited_cutoffs'])
        kinds={e['kind'] for e in self.events if audit.is_decision(e)}
        self.assertTrue({'MAJOR_BOS','M1_TRANSITION_BOS','ENTRY_REVIEW_BLOCKED'} <= kinds)

    def test_deliberate_future_reader_fails_noop_passes(self):
        report=audit.run_controls(engine.Engine,contract_replay,SPEC,self.plan)
        self.assertTrue(report['valid'],report)
        self.assertGreater(report['positive_control_mismatching_cutoffs'],0)
        self.assertTrue(all(f['gate']=='COLD_PREFIX' for f in report['deliberately_future_reading']['failures']))

    def test_nondecision_history_change_also_detected(self):
        events=copy.deepcopy(self.events)
        e=next(e for e in events if not audit.is_decision(e)); e['extra']='tampered'; e['id']=audit.event_id(e)
        report=audit.audit(engine.Engine,contract_replay,SPEC,self.plan,events)
        self.assertTrue(any(f['gate']=='COLD_PREFIX' for f in report['failures']))

    def test_deleted_required_decision_cannot_reduce_audit_silently(self):
        events=copy.deepcopy(self.events); events.pop(next(i for i,e in enumerate(events) if e['kind']=='ENTRY_REVIEW_BLOCKED'))
        result=audit.audit(engine.Engine,contract_replay,SPEC,self.plan,events)
        self.assertTrue(any(f['gate']=='RECORDED_FULL_REPLAY' for f in result['failures']))

    def test_no_decisions_not_vacuous_pass(self):
        plan=self.plan[:1]; events,_,_=contract_replay(engine.Engine,SPEC,plan)
        result=audit.audit(engine.Engine,contract_replay,SPEC,plan,events)
        self.assertEqual(result['status'],'REFUTED')
        self.assertEqual(result['failures'][0]['gate'],'NO_REQUIRED_DECISIONS')

    def test_exact_input_hash_chain(self):
        rows=input_journal(self.plan,self.events)
        errors,head=audit.validate_input_audit(self.plan,self.events,rows)
        self.assertEqual(errors,[]); self.assertEqual(head,rows[-1]['record_hash'])
        rows[0]['event_ids']=['invented']
        self.assertTrue(audit.validate_input_audit(self.plan,self.events,rows)[0])

    def test_clocks_identity_direction_and_scale_mutations_rejected(self):
        changes=[('MAJOR_BOS',lambda e:e['reference'].update(known_at=e['bar_start'])),
                 ('MAJOR_BOS',lambda e:e.update(timeframe=1)),
                 ('HTF_LINK',lambda e:e.update(direction='short')),
                 ('CHILD_CANDIDATE',lambda e:e.update(parent_zone_id='missing')),
                 ('CHILD_CANDIDATE',lambda e:e.update(selected_known_at=e['known_at']-60)),
                 ('CHILD_CANDIDATE',lambda e:e['member_times'].append(e['bar_start'])),
                 ('ENTRY_REVIEW_BLOCKED',lambda e:e.update(direction='short')),
                 ('ENTRY_REVIEW_BLOCKED',lambda e:e.update(target_known=True,target={'known_at':e['known_at']}))]
        for kind,change in changes:
            with self.subTest(kind=kind,change=change):
                events=copy.deepcopy(self.events); e=next(e for e in events if e['kind']==kind); change(e); e['id']=audit.event_id(e)
                self.assertTrue(audit.validate_events(events,SPEC,self.plan))

    def test_duplicate_and_payload_unbound_event_ids_rejected(self):
        events=copy.deepcopy(self.events); events.append(copy.deepcopy(events[-1]))
        self.assertTrue(audit.validate_events(events,SPEC,self.plan))
        events=copy.deepcopy(self.events); events[0]['id']='invented'
        self.assertTrue(audit.validate_events(events,SPEC,self.plan))

    def test_native_input_unknown_clock_phase_or_duplicate_rejected(self):
        for change in (lambda p:p[0][2][0][1].update(known_at=0),
                       lambda p:p[0][2][0][1].update(time=1),
                       lambda p:p[0][2].append(copy.deepcopy(p[0][2][0])),
                       lambda p:p[0][2][0][1].update(member_times=[p[0][0]])):
            plan=copy.deepcopy(self.plan); change(plan)
            self.assertTrue(audit.validate_plan(plan))

    def test_cold_native_reaggregation_uses_only_closed_minutes(self):
        start,end,rows=synthetic_source(); source_hash='a'*64
        plan,_=runner.assemble(rows,start,end,source_hash); events,_,_=runner.replay(engine.Engine,SPEC,plan)
        seen=[]
        def tracked(raw,s,e,h):
            seen.append((max(raw),e)); return runner.assemble(raw,s,e,h)
        result=audit.audit(engine.Engine,runner.replay,SPEC,plan,events,source_rows=rows,assemble_fn=tracked,source_hash=source_hash,start=start)
        self.assertEqual(result['failures'],[],result)
        self.assertGreater(len(seen),0)
        self.assertEqual(result['scope'],'RAW_M1_REAGGREGATION_AND_ENGINE')
        self.assertEqual(len(seen),1+len(result['cutoffs']))
        for (last,_),record in zip(seen[1:],result['cutoffs']): self.assertLessEqual(last+60,record['cutoff'])

    def test_reaggregation_disagreement_fails_even_when_candle_events_unchanged(self):
        start,end,rows=synthetic_source(); h='a'*64
        plan,_=runner.assemble(rows,start,end,h); events,_,_=runner.replay(engine.Engine,SPEC,plan)
        def changed(raw,s,e,h):
            p,c=runner.assemble(raw,s,e,h); p[0][2][0][1]['untrusted_extra']='different'; return p,c
        result=audit.audit(engine.Engine,runner.replay,SPEC,plan,events,source_rows=rows,assemble_fn=changed,source_hash=h,start=start)
        self.assertTrue(any(f['gate']=='NATIVE_PREFIX_REAGGREGATION' for f in result['failures']))

    def test_capture_hash_mismatch_blocks_before_code_execution(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); source=path/'bad.py'; source.write_text('raise AssertionError("must not execute")')
            files={k:dict(path=str(source),sha256='a'*64) for k in ('engine','runner','spec','plan','events','input_audit')}
            capture=path/'capture.json'; capture.write_text(json.dumps(dict(schema='CAUSAL_AUDIT_CAPTURE_V1',files=files)))
            with self.assertRaisesRegex(ValueError,'capture bytes changed'): audit.audit_capture(capture)

    def test_bounded_real_run_controls_detect_future_reader(self):
        report=audit.bounded_controls(engine.Engine,contract_replay,SPEC,self.plan,self.events)
        self.assertTrue(report['valid'],report)
        self.assertEqual(report['chosen_cutoff'],min(e['known_at'] for e in self.events if audit.is_decision(e)))

    def test_control_without_future_suffix_cannot_pass(self):
        cutoff=min(e['known_at'] for e in self.events if audit.is_decision(e))
        plan=[r for r in self.plan if r[0]<=cutoff]
        events,_,_=contract_replay(engine.Engine,SPEC,plan)
        self.assertFalse(audit.bounded_controls(engine.Engine,contract_replay,SPEC,plan,events)['valid'])

    def test_bound_source_identity_cannot_be_replaced(self):
        self.assertTrue(audit.validate_plan(self.plan,'0'*64))

    def test_capture_changed_while_audit_runs_rejected(self):
        # Real CLI proof below covers successful capture; local manifest race is
        # tested without source data or changing another agent's files.
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            files={}
            for name,source in [('engine',Path(engine.__file__)),('runner',Path(runner.__file__))]:
                files[name]=dict(path=str(source),sha256=audit.byte_hash(source))
            start,end,rows=synthetic_source(); plan,_=runner.assemble(rows,start,end,'a'*64)
            events,_,_=runner.replay(engine.Engine,SPEC,plan)
            for name,value,jsonl in [('spec',SPEC,False),('plan',plan,True),('events',events,True),('input_audit',input_journal(plan,events),True)]:
                f=path/name; f.write_text(''.join(audit.canonical(r)+'\n' for r in value) if jsonl else audit.canonical(value))
                files[name]=dict(path=str(f),sha256=audit.byte_hash(f))
            capture=path/'capture.json'; capture.write_text(json.dumps(dict(schema='CAUSAL_AUDIT_CAPTURE_V1',files=files)))
            def change(*args):
                c=json.loads(capture.read_text()); c['untrusted_note']='changed'; capture.write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError,'capture manifest changed'): audit.audit_capture(capture,progress=change)

    def test_full_native_tail_after_last_decision_checked(self):
        start,end,rows=synthetic_source();h='a'*64
        plan,_=runner.assemble(rows,start,end,h);events,_,_=runner.replay(engine.Engine,SPEC,plan)
        cutoff=max(e['known_at'] for e in events if audit.is_decision(e))
        altered=copy.deepcopy(plan)
        for known,_,bars in altered:
            if known>cutoff:
                for tf,b in bars: b['unrelated_tail']='wrong'
        result=audit.audit(engine.Engine,runner.replay,SPEC,altered,events,source_rows=rows,assemble_fn=runner.assemble,source_hash=h,start=start,end=end)
        self.assertTrue(any(x['gate']=='FULL_NATIVE_REAGGREGATION' for x in result['failures']))

    def test_caller_supplied_replay_not_certified_as_process_isolated(self):
        result=audit.audit(engine.Engine,contract_replay,SPEC,self.plan,self.events)
        self.assertEqual(result['status'],'SURVIVES_PROVISIONAL_CALLER_REPLAY_ONLY')

    def test_cold_process_blocks_full_source_file_read_at_module_load(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);future=path/'future.csv';future.write_text('future close\n123\n')
            leaking=path/'engine.py';leaking.write_bytes(Path(engine.__file__).read_bytes()+f'\nHIDDEN_FUTURE=open({str(future)!r}).read()\n'.encode())
            files={'engine':dict(path=str(leaking),sha256=audit.byte_hash(leaking)),
                   'runner':dict(path=runner.__file__,sha256=audit.byte_hash(runner.__file__))}
            runtime=audit.FreshCapturedRuntime(files)
            with self.assertRaisesRegex(RuntimeError,'COLD_RUNTIME_DEPENDENCY_DENIED'):
                runtime.replay(None,SPEC,self.plan)

    def test_cold_process_resets_runner_global_future_memo(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);modified=path/'runner.py'
            code='''
_LAST_FULL_CLOCK=None
_original=replay
def replay(engine_type,spec,plan,out=None):
    global _LAST_FULL_CLOCK
    if _LAST_FULL_CLOCK is None: _LAST_FULL_CLOCK=plan[-1][0]
    events,snapshot,head=_original(engine_type,spec,plan,out)
    for e in events:
        if e['kind']=='MAJOR_BOS':
            e['MEMO_LAST_CLOCK']=_LAST_FULL_CLOCK
            payload={k:v for k,v in e.items() if k!='id'}
            e['id']=e['kind'].lower()+':'+hashlib.sha256(canon([payload]).encode()).hexdigest()[:24]
    return events,snapshot,head
'''
            modified.write_bytes(Path(runner.__file__).read_bytes()+code.encode())
            files={'engine':dict(path=engine.__file__,sha256=audit.byte_hash(engine.__file__)),
                   'runner':dict(path=str(modified),sha256=audit.byte_hash(modified))}
            runtime=audit.FreshCapturedRuntime(files)
            start,end,rows=synthetic_source();plan,_=runner.assemble(rows,start,end,'a'*64)
            full,_,_=runtime.replay(None,SPEC,plan)
            result=audit.audit(None,runtime.replay,SPEC,plan,full)
            self.assertTrue(any(x['gate']=='COLD_PREFIX' for x in result['failures']))

    def test_cold_process_denies_native_storage_and_indirect_imports(self):
        snippets=['import sqlite3','import dbm.ndbm','import _dbm','import _gdbm',
                  "__import__('sqlite3')","__import__('dbm.ndbm',fromlist=['open'])",
                  'from importlib import import_module',"import os\nX=os.stat('/private/tmp').st_size",
                  "from pathlib import Path\nX=Path('/private/tmp').stat().st_size",
                  "import os\nos.sys.modules['_frozen_importlib']._gcd_import('_dbm')"]
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            for i,snippet in enumerate(snippets):
                with self.subTest(snippet=snippet):
                    modified=path/f'engine_{i}.py';modified.write_bytes(Path(engine.__file__).read_bytes()+('\n'+snippet+'\n').encode())
                    files={'engine':dict(path=str(modified),sha256=audit.byte_hash(modified)),
                           'runner':dict(path=runner.__file__,sha256=audit.byte_hash(runner.__file__))}
                    runtime=audit.FreshCapturedRuntime(files)
                    with self.assertRaisesRegex(RuntimeError,'COLD_RUNTIME_DEPENDENCY_DENIED'):
                        runtime.replay(None,SPEC,self.plan)


if __name__=='__main__': unittest.main()
