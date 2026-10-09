"""Portable bounded run_study regression harness; synthetic fixtures only.

Copies all tools and inputs to a NEW output folder. Actual-tool races remain
distinct from explicitly stubbed subprocess contract checks. No network calls,
order calls, acquired data, source edits or performance calculations.
"""
import argparse,contextlib,csv,hashlib,importlib.util,io,json,shutil,subprocess,sys
from pathlib import Path
from unittest.mock import patch

FINISHED='RESEARCH_PIPELINE_FINISHED_NOT_DEPLOYMENT'
SURVIVES='SURVIVES_PROVISIONAL_CAUSAL_PREFIX_ONLY'
TOOLS=('run_study.py','month_replay.py','engine.py','verify_causality.py',
       'cold_runtime_worker.py','report.py','SELECTION_PROTOCOL.json')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_bytes())
def write(path,data):Path(path).write_text(json.dumps(data,indent=2)+'\n')

class Harness:
    def __init__(self,project,fixture,output):
        self.root=output.resolve();self.root.mkdir(parents=True,exist_ok=False)
        self.fixture=fixture.resolve();self.results=[]
        meta=read(self.fixture/'acquisition.json');spec=read(self.fixture/'SPEC.json')
        if meta.get('synthetic_fixture') is not True or spec.get('synthetic_fixture') is not True:
            raise ValueError('Explicitly labeled synthetic acquisition and spec required')
        self.csv_name=meta['csv']['path']
        if Path(self.csv_name).name!=self.csv_name:raise ValueError('Synthetic CSV path must be one basename')
        if sha(self.fixture/self.csv_name)!=meta['csv']['sha256']:raise ValueError('Synthetic fixture receipt differs')
        held=self.root/'held';held.mkdir()
        for name in TOOLS:(held/name).write_bytes((project/name).read_bytes())
        for src,dst in [('SPEC.json','SPEC.json'),('clarification.json','IMPLEMENTATION_CLARIFICATIONS.json')]:
            (held/dst).write_bytes((self.fixture/src).read_bytes())
        write(self.root/'HELD_FILES.json',{p.name:sha(p) for p in held.iterdir()})
        module_spec=importlib.util.spec_from_file_location('isolated_pipeline_regression',held/'run_study.py')
        self.pipeline=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(self.pipeline)
        self.actual_run=subprocess.run

    def run_case(self,name,handler=None,expected=FINISHED,reason=None,stubbed=False):
        folder=self.root/'cases'/name;folder.mkdir(parents=True,exist_ok=False)
        for item in [self.csv_name,'acquisition.json']:(folder/item).write_bytes((self.fixture/item).read_bytes())
        output=folder/'output';calls=[];error=None
        def dispatch(cmd,**kwargs):
            stage=Path(cmd[2]).name;calls.append(stage)
            return handler(stage,cmd,kwargs,folder) if handler else self.actual_run(cmd,**kwargs)
        with patch.object(self.pipeline.subprocess,'run',side_effect=dispatch),patch.object(sys,'argv',[
            'run_study.py','--acquisition',str(folder/'acquisition.json'),'--output',str(output)]),contextlib.redirect_stdout(io.StringIO()):
            try:self.pipeline.main()
            except Exception as exc:error={'type':type(exc).__name__,'reason':str(exc)}
        state=read(output/'PIPELINE.json')
        passed=state.get('status')==expected and (reason is None or (error and reason in error['reason']))
        if expected==FINISHED:
            proof_path=output/'causality/RESULT.json'
            passed=bool(passed and error is None and state.get('complete_month_claim') is False
                        and state.get('causal_proof_sha256')==sha(proof_path))
        result={'name':name,'passed':bool(passed),'pipeline_status':state.get('status'),
                'expected_status':expected,'error':error,'calls':calls,'stubbed_contract':stubbed,
                'complete_month_claim':state.get('complete_month_claim'),
                'report_index_exists':(output/'report/index.html').is_file()}
        write(folder/'CHECK.json',result);self.results.append(result)
        print(json.dumps(result),flush=True)
        return folder

    def execute(self):
        baseline=self.run_case('honest-actual')
        if not self.results[-1]['passed']:raise RuntimeError('Honest actual pipeline failed; do not count negative cases as success')
        good_output=baseline/'output'
        def input_race(stage,cmd,kwargs,folder):
            if stage=='month_replay.py':
                source=folder/self.csv_name;receipt=folder/'acquisition.json'
                rows=list(csv.DictReader(io.StringIO(source.read_text())))
                for row in rows:
                    for k in ('open','high','low','close'):row[k]=float(row[k])+.25
                buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=['epoch','open','high','low','close']);writer.writeheader();writer.writerows(rows)
                source.write_text(buf.getvalue());meta=read(receipt);meta['csv'].update(sha256=sha(source),bytes=source.stat().st_size);write(receipt,meta)
            return self.actual_run(cmd,**kwargs)
        changed=self.run_case('initial-source-race',input_race,'BLOCKED_OR_FAILED','Initial input and captured replay differ')
        def replay_swap(stage,cmd,kwargs,folder):
            if stage=='report.py':
                saved=folder/'output/replay';saved.rename(folder/'output/replay-before-swap')
                shutil.copytree(changed/'output/replay',saved)
            return self.actual_run(cmd,**kwargs)
        self.run_case('replay-report-swap',replay_swap,'BLOCKED_OR_FAILED','Sealed replay changed across stage handoff')
        for target in ['proof','capture']:
            def result_swap(stage,cmd,kwargs,folder,target=target):
                process=self.actual_run(cmd,**kwargs)
                if stage=='report.py' and process.returncode==0:
                    path=folder/'output'/('causality/RESULT.json' if target=='proof' else 'CAUSAL_CAPTURE.json')
                    (folder/(target+'-before.json')).write_bytes(path.read_bytes())
                    data=read(path)
                    if target=='proof':data.update(status='REFUTED',failures=[{'gate':'EXPLICIT_SYNTHETIC_POST_PROOF_CHANGE'}])
                    else:data['synthetic_post_proof_change']=True
                    write(path,data)
                return process
            self.run_case('saved-'+target+'-during-report',result_swap,'BLOCKED_OR_FAILED','Saved causal proof or capture changed across stage handoff')
        contracts=[('consistent',SURVIVES,0,False,False,False),
                   ('refuted-zero','REFUTED',0,False,False,False),
                   ('survives-one',SURVIVES,1,False,False,False),
                   ('explicit-failure',SURVIVES,0,True,False,False),
                   ('wrong-capture',SURVIVES,0,False,True,False),
                   ('missing-report',SURVIVES,0,False,False,True)]
        for name,status,exitcode,failed,wrong,missing in contracts:
            def stub(stage,cmd,kwargs,folder,status=status,exitcode=exitcode,failed=failed,wrong=wrong,missing=missing):
                output=folder/'output'
                if stage=='month_replay.py':shutil.copytree(good_output/'replay',output/'replay')
                elif stage=='verify_causality.py':
                    capture=read(output/'CAUSAL_CAPTURE.json');proof=read(good_output/'causality/RESULT.json')
                    proof.update(status=status,file_bindings=capture['files'],capture_sha256='0'*64 if wrong else sha(output/'CAUSAL_CAPTURE.json'))
                    if failed:proof['failures']=[{'gate':'EXPLICIT_SYNTHETIC_FAILURE'}]
                    (output/'causality').mkdir();write(output/'causality/RESULT.json',proof)
                    return subprocess.CompletedProcess(cmd,exitcode)
                elif stage=='report.py' and not missing:shutil.copytree(good_output/'report',output/'report')
                return subprocess.CompletedProcess(cmd,0)
            expected=FINISHED if name=='consistent' else 'BLOCKED_OR_FAILED'
            self.run_case('contract-'+name,stub,expected,stubbed=True)
        result={'harness_sha256':sha(__file__),'pipeline_sha256':sha(self.root/'held/run_study.py'),
                'synthetic_only':True,'network_requests':0,'project_edits':False,
                'real_actual_tool_cases':5,'stubbed_contract_cases':6,'tests':self.results,
                'passed':sum(t['passed'] for t in self.results),'total':len(self.results),
                'all_pass':all(t['passed'] for t in self.results),
                'limits':'Bounded handoff regression only; not whole-month, teacher, execution or profitability clearance.'}
        write(self.root/'RESULT.json',result)
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True,type=Path,help='Directory containing run_study and its sibling tools')
    p.add_argument('--fixture',required=True,type=Path,help='Explicit synthetic SPEC/clarification/acquisition/source fixture')
    p.add_argument('--output',required=True,type=Path,help='New exclusive directory; all evidence preserved here')
    a=p.parse_args();result=Harness(a.project.resolve(),a.fixture,a.output).execute()
    print(json.dumps({'all_pass':result['all_pass'],'passed':result['passed'],'total':result['total']}),flush=True)
    return 0 if result['all_pass'] else 1

if __name__=='__main__':raise SystemExit(main())
