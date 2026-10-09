"""Run a frozen captured study; no market downloads or orders are performed here."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-partial',action='store_true')
    args=parser.parse_args()
    receipt=args.acquisition.resolve();meta=json.loads(receipt.read_bytes())
    source=receipt.parent/meta['csv']['path'];output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    log=output/'PIPELINE.json'
    state={'started_at':datetime.now(timezone.utc).isoformat(),'stages':[],
           'execution_allowed':False,'teacher_fidelity':False,'script_sha256':sha(__file__),
           'acquisition_sha256':sha(receipt),'source_sha256':sha(source)}
    held_tools={name:sha(ROOT/name) for name in ('engine.py','month_replay.py','SPEC.json','IMPLEMENTATION_CLARIFICATIONS.json','verify_causality.py','cold_runtime_worker.py','report.py','SELECTION_PROTOCOL.json')}
    state['stage_source_hashes']=held_tools
    def unchanged_tools():
        if any(sha(ROOT/name)!=value for name,value in held_tools.items()):
            raise RuntimeError('Stage source changed during pipeline')
    def save():log.write_text(json.dumps(state,indent=2)+'\n')
    def run(label,cmd):
        unchanged_tools()
        stage={'name':label,'started_at':datetime.now(timezone.utc).isoformat(),'argv':cmd}
        state['stages'].append(stage);save()
        with (output/(label+'.log')).open('x') as f:
            result=subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,
                env=dict(os.environ,MPLCONFIGDIR=str(output/'matplotlib-cache')))
        stage.update(returncode=result.returncode,ended_at=datetime.now(timezone.utc).isoformat(),log_sha256=sha(output/(label+'.log')));save()
        if result.returncode:raise RuntimeError(label+' failed; inspect preserved log')
        unchanged_tools()
    try:
        runpath=output/'replay'
        cmd=[sys.executable,'-B',str(ROOT/'month_replay.py'),'--source',str(source),'--acquisition',str(receipt),'--engine',str(ROOT/'engine.py'),'--spec',str(ROOT/'SPEC.json'),'--clarifications',str(ROOT/'IMPLEMENTATION_CLARIFICATIONS.json'),'--output',str(runpath)]
        if args.allow_partial:cmd.append('--diagnostic')
        run('replay',cmd)
        replay_manifest=json.loads((runpath/'MANIFEST.json').read_text())
        if (replay_manifest['source_sha256']!=state['source_sha256']
            or replay_manifest['acquisition_sha256']!=state['acquisition_sha256']):
            raise RuntimeError('Initial input and captured replay differ; publication withheld')
        completion_bytes=(runpath/'COMPLETION.json').read_bytes()
        completion_sha=hashlib.sha256(completion_bytes).hexdigest()
        completion=json.loads(completion_bytes)
        captured_paths={k:runpath/v['path'] for k,v in replay_manifest['captures'].items()}
        expected_capture=completion['captured_input_hashes']
        expected_outputs=completion['output_hashes']
        def unchanged_replay():
            if (sha(runpath/'COMPLETION.json')!=completion_sha
                or any(sha(path)!=expected_capture[k] for k,path in captured_paths.items())
                or any(sha(runpath/name)!=value for name,value in expected_outputs.items())):
                raise RuntimeError('Sealed replay changed across stage handoff')
        unchanged_replay()
        files={k:{'path':str(runpath/name),'sha256':sha(runpath/name)} for k,name in {'engine':'captures/engine.py','runner':'captures/month_replay.py','spec':'captures/SPEC.json','source':'captures/source.csv','plan':'sealed-plan.jsonl','events':'events.jsonl','input_audit':'input-audit.jsonl'}.items()}
        def epoch(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
        spec=json.loads((runpath/'captures/SPEC.json').read_text())
        capture=output/'CAUSAL_CAPTURE.json';capture.write_text(json.dumps({'schema':'CAUSAL_AUDIT_CAPTURE_V1','files':files,'start':epoch(spec['scope']['warmup_start']),'end':epoch(spec['scope']['end_exclusive'])},indent=2)+'\n')
        capture_sha=sha(capture)
        run('causality',[sys.executable,'-B',str(ROOT/'verify_causality.py'),'--capture',str(capture),'--out',str(output/'causality')])
        # A failed causal proof does not become a visually approved setup.
        proof_path=output/'causality/RESULT.json'
        proof_bytes=proof_path.read_bytes()
        proof_sha=hashlib.sha256(proof_bytes).hexdigest()
        proof=json.loads(proof_bytes)
        def unchanged_proof():
            if sha(proof_path)!=proof_sha or sha(capture)!=capture_sha:
                raise RuntimeError('Saved causal proof or capture changed across stage handoff')
        unchanged_replay()
        n=proof.get('required_unique_cutoffs')
        if (proof.get('status')!='SURVIVES_PROVISIONAL_CAUSAL_PREFIX_ONLY'
            or proof.get('failures')!=[] or proof.get('capture_sha256')!=capture_sha
            or sha(capture)!=capture_sha or proof.get('file_bindings')!=files
            or type(n) is not int or n<=0 or proof.get('audited_cutoffs')!=n or proof.get('passing_cutoffs')!=n
            or proof.get('full_native_reaggregation_checked') is not True or proof.get('input_audit_checked') is not True
            or any(proof.get('controls',{}).get(k) is not True for k in ('valid','no_op_passed','deliberately_future_reading_detected'))):
            raise RuntimeError('Causal proof/capture contract failed; report publication withheld')
        unchanged_proof()
        run('report',[sys.executable,'-B',str(ROOT/'report.py'),'--run',str(runpath),'--output',str(output/'report')])
        unchanged_replay()
        unchanged_proof()
        rc=json.loads((output/'report/REPORT_COMPLETION.json').read_text())
        rr=json.loads((output/'report/REPORT.json').read_text())
        if (rc.get('status')!='REPORT_WRITTEN' or rc.get('source_completion_sha256')!=completion_sha
            or rr.get('source_completion_sha256')!=completion_sha or rr.get('input_hashes')!=expected_capture
            or rr.get('sealed_output_hashes')!=expected_outputs or not (output/'report/index.html').is_file()
            or not {'index.html','REPORT.json'}.issubset(rc.get('output_hashes',{}))):
            raise RuntimeError('Report is not bound to the verified replay')
        for name,value in rc['output_hashes'].items():
            artifact=(output/'report'/name).resolve()
            if output/'report' not in artifact.parents or sha(artifact)!=value:
                raise RuntimeError('Report output missing or changed')
        unchanged_proof()
        state.update(causal_proof_sha256=proof_sha,report_completion_sha256=sha(output/'report/REPORT_COMPLETION.json'),replay_completion_sha256=completion_sha,causal_capture_sha256=capture_sha)
        state.update(status='RESEARCH_PIPELINE_FINISHED_NOT_DEPLOYMENT',complete_month_claim=False,
                     causal_scope=proof['status'],report=str(output/'report/index.html'))
    except Exception as exc:
        state.update(status='BLOCKED_OR_FAILED',error_type=type(exc).__name__,reason=str(exc));save();raise
    save();print(json.dumps(state))

if __name__=='__main__':main()
