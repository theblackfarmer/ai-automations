"""Reproducible frozen research: manifest -> data -> causal gates -> execution."""
from __future__ import annotations
import argparse,hashlib,json,os,platform,subprocess,time
from pathlib import Path
import numpy as np
import pandas as pd
from . import data,icc,vincent,causality
from .acquire import sha256_file
from .execution import simulate
from .metrics import summarize,verdict
from .nulls import random_entry,holm,condition_slices
ROOT=Path(__file__).resolve().parents[1]
FREEZE=ROOT/'research/astra6/FREEZE_2026-10-02.json'


def write(path,obj):
    path.write_text(json.dumps(obj,indent=2,default=str,allow_nan=False)+'\n')


def configurations():
    configs=[]
    bases=[('icc_1h_15m',60,15),('icc_1h_5m',60,5),('icc_4h_15m',240,15)]
    for name,h,l in bases:
        p=dict(htf_minutes=h,ltf_minutes=l,strategy=name)
        configs.append((name,'icc',p,True))
        for suffix,changes in [('pivot1',dict(left=1,right=1)),('pivot3',dict(left=3,right=3)),('body40',dict(body_fraction=.4)),('body60',dict(body_fraction=.6)),('expiry72',dict(horizon=72)),('expiry120',dict(horizon=120))]:
            new=name+'_'+suffix;configs.append((new,'icc',dict(p,**changes,strategy=new) if 'strategy' not in changes else dict(p,**changes),False))
    configs.append(('vincent_2m','vincent',dict(ltf_minutes=2,tolerance_ticks=1,strategy='vincent_2m'),True))
    for name,m,t in [('vincent_touch0',2,0),('vincent_touch2',2,2),('vincent_3m',3,1)]:
        configs.append((name,'vincent',dict(ltf_minutes=m,tolerance_ticks=t,strategy=name),False))
    return configs


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--data-dir',type=Path,default=Path('data_cache'));ap.add_argument('--output',type=Path,default=Path('results/astra6'));ap.add_argument('--base-only',action='store_true',help='Diagnostic partial run; cannot support full final verdict');ap.add_argument('--causal-only',action='store_true',help='Generate populations and causal evidence without performance');args=ap.parse_args(argv)
    args.output.mkdir(parents=True,exist_ok=True)
    spec=json.loads(FREEZE.read_text());cfg=configurations()
    if args.base_only:cfg=[x for x in cfg if x[3]]
    manifest={'freeze_id':spec['id'],'freeze_sha256':sha256_file(FREEZE),'diagnostics_sha256':sha256_file(ROOT/'research/astra6/EXECUTION_DIAGNOSTICS_2026-10-02.json'),'seed':spec['gates']['seed'],
      'binding':'PROSPECTIVELY_BOUND','started_utc':str(pd.Timestamp.now(tz='UTC')),
      'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
      'source_hashes':{str(p.relative_to(ROOT)):sha256_file(p) for p in sorted((ROOT/'astra_research').glob('*.py'))},
      'test_hashes':{str(p.relative_to(ROOT)):sha256_file(p) for p in sorted((ROOT/'tests').glob('test_research*.py'))},
      'environment':{'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__},
      'inputs':{},'configurations':[{'name':n,'kind':k,'parameters':p,'base':b} for n,k,p,b in cfg],
      'partial_run':args.base_only or args.causal_only,'source_limitations':['vendor timestamp/roll provenance not established','Vincent four-market confirmation not available; core-pattern interpretation only']}
    for kind in ['historical','forward']:
        file=args.data_dir/spec['data'][kind+'_file'];actual=sha256_file(file)
        if actual!=spec['data'][kind+'_sha256']:raise ValueError('frozen input hash mismatch: '+kind)
        manifest['inputs'][kind]={'path':str(file),'sha256':actual}
    # Record installed dependency code hashes, including native components.
    import importlib.metadata
    for package in ['pandas','numpy','pyarrow','scipy']:
        dist=importlib.metadata.distribution(package);digest=hashlib.sha256()
        for f in sorted(dist.files or [],key=str):
            if str(f).endswith(('.py','.so','.pyd')):
                q=dist.locate_file(f)
                if q.is_file():digest.update(str(f).encode());digest.update(q.read_bytes())
        manifest.setdefault('dependency_hashes',{})[package]={'version':dist.version,'code_sha256':digest.hexdigest()}
    write(args.output/'manifest.json',manifest) # Must exist before gates/execution.
    import zipfile
    with zipfile.ZipFile(args.output/'source_snapshot.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for p in list((ROOT/'astra_research').glob('*.py'))+list((ROOT/'research/astra6').glob('*.json'))+list((ROOT/'tests').glob('test_research*.py'))+[ROOT/'requirements-astra.txt']:
            archive.write(p,str(p.relative_to(ROOT)))
    sources={k:data.load_csv(args.data_dir/spec['data'][k+'_file'],forward=k=='forward') for k in ['historical','forward']}
    quality={k:{'rows':len(v),'first':str(v.timestamp.min()),'last':str(v.timestamp.max()),'gaps_gt1min':int((v.timestamp.diff()>pd.Timedelta(minutes=1)).sum())} for k,v in sources.items()}
    write(args.output/'data_quality.json',quality)
    caches={k:{} for k in sources};populations={};audits={}
    for name,kind,params,base in cfg:
        for dataset,raw in sources.items():
            print('CAUSAL',name,dataset,flush=True)
            c=caches[dataset]
            for tf in ([params['htf_minutes'],params['ltf_minutes']] if kind=='icc' else [params['ltf_minutes']]):
                if tf not in c:c[tf]=data.resample(raw,tf)
            events=icc.signals_from_bars(c[params['htf_minutes']],c[params['ltf_minutes']],**params) if kind=='icc' else vincent.signals_from_bars(raw,c[params['ltf_minutes']],**params)
            populations[name,dataset]=events
            events.to_parquet(args.output/f'{name}_{dataset}_events.parquet',index=False)
            result=causality.audit(raw,events,kind,params,cache=c)
            audits[name+'_'+dataset]=result;write(args.output/'causality.json',audits)
            print(name,dataset,'events',len(events),'causal',result['pass'],flush=True)
            if not result['pass']:raise AssertionError('causality gate failed: '+name+' '+dataset)
    if args.causal_only:return
    results={};diagnostics={};alltrades={};null_results={};conditions={}
    for name,kind,params,base in cfg:
        summaries={};diagnostics[name]={}
        for part,(start,end) in spec['data']['partitions'].items():
            source='forward' if part=='forward_oos' else 'historical';raw=sources[source]
            a=pd.Timestamp(start,tz='America/New_York').tz_convert('UTC');b=(pd.Timestamp(end,tz='America/New_York')+pd.Timedelta(days=1)).tz_convert('UTC')
            x=raw[(raw.timestamp>=a)&(raw.timestamp<b)]
            e=populations[name,source];e=e[(e.decision_time>=a)&(e.decision_time<b)]
            piv=icc.pivots(caches[source][params['ltf_minutes']],params['ltf_minutes'],params.get('left',2),params.get('right',2))
            summaries[part]={};diagnostics[name][part]={}
            for instrument in ['NQ','MNQ']:
                for stress in [False,True]:
                    key=instrument+('_stress' if stress else '_base')
                    tr,di=simulate(x,e,instrument=instrument,slippage_ticks=2 if stress else 1,fee_multiplier=2 if stress else 1,pivots=piv,end=b)
                    summaries[part][key]=summarize(tr,seed=spec['gates']['seed']);diagnostics[name][part][key]=di
                    if instrument=='MNQ' and not stress:
                        tr.to_csv(args.output/f'{name}_{part}_trades.csv',index=False);alltrades[name,part]=tr
            print('EXECUTION',name,part,'n',summaries[part]['MNQ_base']['n'],flush=True)
        if base:
            null_results[name]={};conditions[name]={}
            for part in ['validation','forward_oos']:
                src='forward' if part=='forward_oos' else 'historical';r=sources[src];start,end=spec['data']['partitions'][part]
                a=pd.Timestamp(start,tz='America/New_York').tz_convert('UTC');b=(pd.Timestamp(end,tz='America/New_York')+pd.Timedelta(days=1)).tz_convert('UTC')
                x=r[(r.timestamp>=a)&(r.timestamp<b)]
                piv=icc.pivots(caches[src][params['ltf_minutes']],params['ltf_minutes'],params.get('left',2),params.get('right',2))
                null_results[name][part]=random_entry(x,alltrades[name,part],piv,kind=kind,seed=spec['gates']['seed'])
                conditions[name][part]=condition_slices(r,alltrades[name,part])
            write(args.output/'random_entry_null.json',null_results);write(args.output/'condition_diagnostics.json',conditions)
        results[name]={'base_variant':base,'summaries':summaries,'verdict':verdict(summaries,source_gaps=True)}
        write(args.output/'results.json',results);write(args.output/'execution_diagnostics.json',diagnostics)
    # All predeclared neighbors reported, never selected as winners.
    family=[n for n,k,p,b in cfg if b]
    adjusted=holm({n:null_results[n]['forward_oos'] for n in family})
    for n in family:
        neighbors=[v['summaries']['forward_oos']['MNQ_base'].get('expectancy_R') for k,v in results.items() if not v['base_variant'] and ((n.startswith('icc') and k.startswith(n+'_')) or (n=='vincent_2m' and k.startswith('vincent')))]
        results[n]['verdict']=verdict(results[n]['summaries'],source_gaps=True,neighbors=neighbors if neighbors else None,causal_pass=all(v['pass'] for k,v in audits.items() if k.startswith(n+'_')),null_evidence=adjusted[n],execution_diagnostics=diagnostics[n])
    write(args.output/'results.json',results);write(args.output/'random_entry_null.json',null_results)
    final={'freeze':spec['id'],'partial_run':args.base_only,'model_verdicts':{n:results[n]['verdict'] for n in family},
           'causal_candidates':sum(x['events'] for x in audits.values()),'causal_prefix_compared':sum(x['events_prefix_compared'] for x in audits.values()),
           'positive_controls':sum(x['positive_control_detected'] for x in audits.values()),
           'all_causal_pass':all(x['pass'] for x in audits.values()),'hypotheses_tested':len(cfg),
           'data_source_limitations':manifest['source_limitations'],
           'interpretation':'NO_GO for deployment; estimates apply only to frozen ASTRA interpretations, not full discretionary educator methods.',
           'not_completed':['Forward paper execution','Calibrated random-entry null significance (descriptive benchmark provided)','Full four-market Vincent fidelity','Verified vendor contract and timestamp provenance'],
           'finished_utc':str(pd.Timestamp.now(tz='UTC'))}
    write(args.output/'VERDICT.json',final)
    print(json.dumps(final,indent=2))

if __name__=='__main__':main()
