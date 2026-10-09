"""Resumable metadata-quiet raw Dukascopy acquisition; no strategy or prices printed.

Pinned legacy codec/receipt validation is retained byte-identical. This wrapper
replaces its queued retry loop, scopes a NEW protocol, and imports old evidence
with original provenance. V3 also bounds ordinary connection-error retries and
retains unresolved prior receipts when a resume cannot reach them.
Default is dry-run; --execute is required for network.
"""
import argparse
import collections
import email.utils
import errno
import fcntl
import hashlib
import json
import os
import socket
from pathlib import Path
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
import legacy_acquire as codec

CODEC_SHA='3f19fb017d267691cfc98e26874152a642bd59486d43f642bb6036f3f8acb5ed'
V2_SCRIPT_SHA='fcb062902aee5c45f1e6e7438b96f3a7e0fdab1c2152470f088ee1cbaacb4e49'
V2_AMENDMENT_SHA='ccdb231068d442a1ab235f6207d1697ae0654b16343e1ca0a487314be456235c'
GOOD=codec.GOOD_STATES
GLOBAL_RATE_LIMIT_PATH=Path(__file__).parent/'GLOBAL_RATE_LIMIT.json'

class RateLimited(Exception):
    def __init__(self, retry_after, at, final_url):
        self.receipt={'state':'RATE_LIMITED','status':429,'retry_after':retry_after,
                      'at_epoch':at,'retry_not_before_epoch':retry_deadline(retry_after,at),
                      'final_url':final_url,'at':codec.clocks(int(at))}

def retry_deadline(value, now):
    try:
        if value is not None and str(value).isdigit(): return now+max(1,int(value))
        dt=email.utils.parsedate_to_datetime(value)
        return max(now+1,dt.timestamp())
    except (ValueError,TypeError,OverflowError): return now+3600

def note_transport_exception(e, observed, stage):
    cause=e.reason if isinstance(e,urllib.error.URLError) else e
    observed.update(error_type=type(cause).__name__,error_stage=stage)
    code=getattr(cause,'errno',None)
    if isinstance(code,int):observed['error_errno']=code

class ObservedResponse:
    """Record type/errno only; leave codec classification and body checks intact."""
    def __init__(self, inner, observed):self.inner,self.observed=inner,observed
    def __enter__(self):
        self.inner.__enter__();return self
    def __exit__(self,*args):return self.inner.__exit__(*args)
    def __getattr__(self,key):return getattr(self.inner,key)
    def read(self,*args):
        try:return self.inner.read(*args)
        except Exception as e:
            note_transport_exception(e,self.observed,'read');raise

class RespectfulOpener:
    def __init__(self, inner, observed=None):
        self.inner=inner;self.observed=observed if observed is not None else {}
    def open(self, request, timeout):
        try:return ObservedResponse(self.inner.open(request,timeout=timeout),self.observed)
        except urllib.error.HTTPError as e:
            if e.code!=429: raise
            limited=RateLimited(e.headers.get('Retry-After'),time.time(),e.geturl())
            e.close(); raise limited
        except Exception as e:
            note_transport_exception(e,self.observed,'open');raise

def fetch(epoch,timeout=20):
    observed={}
    attempt,body,rows=codec.fetch_attempt(epoch,timeout,opener_factory=lambda original,history:
        RespectfulOpener(urllib.request.build_opener(codec.DocumentedRedirect(original,history)),observed))
    attempt.update(observed)
    return attempt,body,rows

def ordinary_transient(attempt):
    """Explicit network failures only; never make corrupt/partial data reusable."""
    state=attempt.get('state')
    if state=='TIMEOUT':return True
    if state=='HTTP_ERROR':return attempt.get('status') in (408,500,502,503,504)
    if state!='TRANSPORT_ERROR':return False
    kind=attempt.get('error_type')
    if kind in {'RemoteDisconnected','ConnectionResetError','ConnectionAbortedError',
                'ConnectionRefusedError','BrokenPipeError','TimeoutError','SSLEOFError'}:
        return True
    if kind=='gaierror':return attempt.get('error_errno')==socket.EAI_AGAIN
    if kind=='OSError':
        return attempt.get('error_errno') in {errno.ECONNRESET,errno.ECONNABORTED,
            errno.ECONNREFUSED,errno.EPIPE,errno.ETIMEDOUT,errno.ENETUNREACH,
            errno.EHOSTUNREACH,errno.ENETDOWN,errno.ENETRESET}
    return False

def parse_hour(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None or dt.utcoffset().total_seconds()!=0 or dt.minute or dt.second or dt.microsecond:
        raise ValueError('Protocol requires explicit UTC whole-hour boundaries')
    return int(dt.timestamp())

def load_protocol(path):
    raw=path.read_bytes(); p=json.loads(raw)
    for key,value in {'schema':'dukascopy-month-data-v1','symbol':'USATECHIDXUSD','price_scale':1000,
                      'feed':'Dukascopy','native_input':'raw_bid_ask_ticks','fill_missing':False,
                      'performance':False,'execution_allowed':False,'parallel_requests':1}.items():
        if p.get(key)!=value: raise ValueError('Unsupported protocol field: '+key)
    a,b=parse_hour(p['start_utc']),parse_hour(p['end_exclusive_utc'])
    if not a<b or b-a>93*86400: raise ValueError('Invalid bounded acquisition dates')
    if p.get('minimum_request_spacing_seconds',0)<2: raise ValueError('Minimum request spacing must be >=2 seconds')
    return p,raw,codec.sha(raw),a,b

def verify_codec():
    if codec.sha(Path(codec.__file__).read_bytes())!=CODEC_SHA: raise ValueError('Pinned codec changed')

def imported(base,epoch):
    """Return original immutable receipts, after body/metadata/source validation."""
    path=base/'hours'/(codec.hour_key(epoch)+'.json')
    if not path.exists(): return None
    original=path.read_bytes(); r=json.loads(original)
    if r.get('state') not in GOOD:return None
    identity_raw=(base/'ACQUISITION_IDENTITY.json').read_bytes(); identity=json.loads(identity_raw)
    if identity.get('symbol')!='USATECHIDXUSD' or identity.get('scale')!=1000 or identity.get('side')!='BID':
        raise ValueError('Imported cache has wrong feed identity')
    if codec.sha((base/'FROZEN_PROTOCOL.json').read_bytes())!=identity['protocol_sha256']:
        raise ValueError('Imported protocol changed')
    if identity.get('script_sha256')!=CODEC_SHA:raise ValueError('Unsupported legacy cache source code')
    rows=codec.validated_cache(base,r,epoch,identity['protocol_sha256'],identity['script_sha256'])
    body=(base/r['attempts'][-1]['body_file']).read_bytes()
    proof={'source_cache':str(base.resolve()),'original_receipt':r,
           'original_receipt_sha256':codec.sha(original),'original_receipt_bytes_hex':original.hex(),
           'original_identity':identity,'original_identity_sha256':codec.sha(identity_raw),
           'original_identity_bytes_hex':identity_raw.hex(),'original_protocol_sha256':identity['protocol_sha256']}
    return body,rows,proof

def persist_receipt(target,epoch,attempt,body,rows,protocol_sha,script_sha,proof=None):
    if body is not None:attempt['body_file']=codec.store_body(target,body)
    r={'hour_start':epoch,'hour_end_exclusive':epoch+3600,'clocks':codec.clocks(epoch),
       'requested_url':codec.hour_url(epoch),'protocol_sha256':protocol_sha,'script_sha256':script_sha,
       'state':attempt['state'],'attempts':[attempt],'updated_at':codec.now(),
       'minute_count':len(rows),'missing_minutes':60-len(rows),
       'missing_minute_ranges':codec.missing_ranges(epoch,epoch+3600,{x[0] for x in rows})}
    if proof:r['import_provenance']=proof
    path=target/'hours'/(codec.hour_key(epoch)+'.json')
    if path.exists():
        old=path.read_bytes();codec.atomic_bytes(target/'history'/(codec.sha(old)+'.json'),old)
    codec.atomic_json(path,r);return r

def cooldown(target,at):
    path=target/'RATE_LIMIT.json'
    if not path.exists(): return None
    r=json.loads(path.read_bytes())
    return r if at<r['retry_not_before_epoch'] else None

def verify_import_proof(r):
    if 'import_provenance' not in r:return
    p=r['import_provenance']
    for kind in ['receipt','identity']:
        raw=bytes.fromhex(p['original_'+kind+'_bytes_hex'])
        if codec.sha(raw)!=p['original_'+kind+'_sha256'] or json.loads(raw)!=p['original_'+kind]:
            raise ValueError('Imported provenance altered')
    old=p['original_receipt'];identity=p['original_identity']
    if (old['hour_start']!=r['hour_start'] or old['attempts'][-1]!=r['attempts'][-1]
        or old['protocol_sha256']!=identity['protocol_sha256'] or old['script_sha256']!=CODEC_SHA):
        raise ValueError('Imported provenance does not bind current body/hour')

def shared_cooldown(target,at):
    paths=[target/'RATE_LIMIT.json',GLOBAL_RATE_LIMIT_PATH,
           Path(__file__).parent/'acquisition'/'RATE_LIMIT.json']
    active=[]
    for path in paths:
        if path.exists():
            r=json.loads(path.read_bytes())
            if at<r['retry_not_before_epoch']:active.append(r)
    return max(active,key=lambda r:r['retry_not_before_epoch']) if active else None

def prepare_identity(target,raw,psha,script_sha,amendment,identity):
    """Append a code amendment for V2 targets; never relabel their old receipts."""
    ip=target/'MONTH_IDENTITY.json'
    if not ip.exists():
        if any(x.name!='RUN.lock' for x in target.iterdir()):raise ValueError('New target must be empty')
        codec.atomic_bytes(target/'FROZEN_PROTOCOL.json',raw)
        codec.atomic_json(ip,identity)
        codec.atomic_bytes(target/'TRANSPORT_AMENDMENT_V3.json',amendment.read_bytes())
        return {script_sha}
    old_raw=ip.read_bytes();old=json.loads(old_raw)
    if codec.sha((target/'FROZEN_PROTOCOL.json').read_bytes())!=psha:
        raise ValueError('Existing target protocol changed')
    if old==identity:
        if (target/'TRANSPORT_AMENDMENT_V3.json').read_bytes()!=amendment.read_bytes():
            raise ValueError('Existing V3 transport amendment changed')
        return {script_sha}
    expected_v2=dict(identity,script_sha256=V2_SCRIPT_SHA,
                     transport_amendment_sha256=V2_AMENDMENT_SHA)
    if old!=expected_v2:raise ValueError('Existing target identity changed')
    backup=Path(__file__).parent/'preserved-v2'/'acquire_month_v2.py'
    if codec.sha(backup.read_bytes())!=V2_SCRIPT_SHA:
        raise ValueError('Preserved V2 driver changed')
    if codec.sha((target/'TRANSPORT_AMENDMENT_V2.json').read_bytes())!=V2_AMENDMENT_SHA:
        raise ValueError('Original V2 amendment changed')
    lineage={'schema':'month-acquisition-code-lineage-v3','prior_identity_sha256':codec.sha(old_raw),
             'prior_script_sha256':V2_SCRIPT_SHA,'prior_amendment_sha256':V2_AMENDMENT_SHA,
             'current_identity':identity,'does_not_retroactively_clear_v2':True}
    lp=target/'TRANSPORT_IDENTITY_V3.json';ap=target/'TRANSPORT_AMENDMENT_V3.json'
    if lp.exists():
        if json.loads(lp.read_bytes())!=lineage or ap.read_bytes()!=amendment.read_bytes():
            raise ValueError('V3 code lineage or amendment changed')
    else:
        if ap.exists() and ap.read_bytes()!=amendment.read_bytes():
            raise ValueError('V3 amendment collision')
        codec.atomic_bytes(ap,amendment.read_bytes());codec.atomic_json(lp,lineage)
    return {V2_SCRIPT_SHA,script_sha}

def validate_prior_receipt(target,r,epoch,psha,allowed_scripts):
    if (r.get('hour_start')!=epoch or r.get('hour_end_exclusive')!=epoch+3600
        or r.get('protocol_sha256')!=psha or r.get('script_sha256') not in allowed_scripts
        or r.get('requested_url')!=codec.hour_url(epoch)):
        raise ValueError('Prior receipt identity changed')
    attempts=r.get('attempts',[])
    if not attempts or attempts[-1].get('state')!=r.get('state'):
        raise ValueError('Prior receipt state lacks attempt evidence')
    last=attempts[-1]
    if last.get('requested_url')!=codec.hour_url(epoch):
        raise ValueError('Prior attempt hour changed')
    verify_import_proof(r)
    if r['state'] in GOOD:
        return codec.validated_cache(target,r,epoch,psha,r['script_sha256'])
    if last.get('body_file'):
        digest=last.get('body_sha256','')
        if (len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)
            or last['body_file']!='blobs/'+digest+'.bi5'):
            raise ValueError('Prior failed body identity changed')
        body=(target/last['body_file']).read_bytes()
        if codec.sha(body)!=digest or len(body)!=last.get('body_bytes'):
            raise ValueError('Prior failed body changed')
    return []


def run(target,p,raw,psha,start,end,cache_roots,limit,fetcher=fetch,sleeper=time.sleep,now=time.time):
    verify_codec();script_sha=codec.sha(Path(__file__).read_bytes())
    amendment=Path(__file__).parent/'TRANSPORT_AMENDMENT_V3.json'
    amendment_sha=codec.sha(amendment.read_bytes())
    target.mkdir(parents=True,exist_ok=True)
    with (target/'RUN.lock').open('a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another acquisition holds target lock') from None
        identity={'protocol_sha256':psha,'script_sha256':script_sha,'codec_sha256':CODEC_SHA,
                  'symbol':'USATECHIDXUSD','scale':1000,'raw_sides':['BID','ASK'],'derived_side':'BID',
                  'start':start,'end_exclusive':end,'parallel_requests':1,
                  'transport_amendment_sha256':amendment_sha,'ordinary_attempts_per_hour_per_run':2}
        allowed_scripts=prepare_identity(target,raw,psha,script_sha,amendment,identity)
        run_started=codec.now();results={};requests=0;stop=None;last_request=None
        # PRELOAD every valid cached hour before any network attempt. Neither an
        # early missing hour nor a pending cooldown can hide later cached coverage.
        for epoch in range(start,end,3600):
            rp=target/'hours'/(codec.hour_key(epoch)+'.json')
            if rp.exists():
                r=json.loads(rp.read_bytes())
                rows=validate_prior_receipt(target,r,epoch,psha,allowed_scripts)
                results[epoch]=(r,rows,r['state'] in GOOD)
                if r['state'] in GOOD:
                    continue
            candidate=None
            for base in cache_roots:
                found=imported(base,epoch)
                if found is None:continue
                if candidate and codec.sha(candidate[0])!=codec.sha(found[0]):raise ValueError('Contradictory cache bodies for same source hour')
                candidate=found
            if candidate:
                body,rows,proof=candidate;attempt=dict(proof['original_receipt']['attempts'][-1])
                r=persist_receipt(target,epoch,attempt,body,rows,psha,script_sha,proof)
                results[epoch]=(r,rows,True)
        print(json.dumps({'state':'CACHE_PRELOAD_COMPLETE',
                          'cached_hours':sum(x[0]['state'] in GOOD for x in results.values()),
                          'prior_unresolved_hours':sum(x[0]['state'] not in GOOD for x in results.values()),
                          'network_requests':0,'prices_printed':False}),flush=True)
        for epoch in range(start,end,3600):
            if epoch in results and results[epoch][0]['state'] in GOOD:continue
            for ordinal in (1,2):
                if requests>=limit:stop='REQUEST_BUDGET_REACHED';break
                if shared_cooldown(target,now()):stop='RETRY_AFTER_PENDING';break
                if last_request is not None:
                    sleeper(max(0,p['minimum_request_spacing_seconds']-(now()-last_request)))
                if ordinal==2:sleeper(5)
                # A separate same-feed probe may receive 429 during our backoff.
                if shared_cooldown(target,now()):stop='RETRY_AFTER_PENDING';break
                requests+=1;last_request=now()
                try:attempt,body,rows=fetcher(epoch)
                except RateLimited as e:
                    codec.atomic_json(target/'RATE_LIMIT.json',e.receipt)
                    codec.atomic_json(GLOBAL_RATE_LIMIT_PATH,e.receipt)
                    event=dict(e.receipt,requested_url=codec.hour_url(epoch),hour_start=epoch)
                    codec.atomic_json(target/'rate-limit-events'/(str(time.time_ns())+'.json'),event)
                    stop='RATE_LIMITED';break
                attempt['attempt_number_this_run']=ordinal
                attempt['ordinary_transient_retry_eligible']=ordinary_transient(attempt)
                r=persist_receipt(target,epoch,attempt,body,rows,psha,script_sha)
                results[epoch]=(r,rows,False)
                if r['state'] in GOOD|{'HTTP_404'}:break
                if not ordinary_transient(attempt):stop='SOURCE_OR_TRANSPORT_ERROR';break
                # Second ordinary failure remains unresolved; advance to next hour.
            if requests and requests%24==0:
                print(json.dumps({'network_requests_this_run':requests,'processed_hours':len(results),
                                  'unresolved_attempted_hours':sum(x[0]['state'] not in GOOD for x in results.values()),
                                  'prices_printed':False}),flush=True)
            if stop:break
        manifest,rows=codec.metadata_manifest(list(results.values()),start,end,psha,script_sha,run_started,False)
        manifest.update(network_requests_this_run=requests,stop_reason=stop,raw_sides=['BID','ASK'],
                        codec_sha256=CODEC_SHA,cache_import_count=sum('import_provenance' in x[0] for x in results.values()),
                        absent_hours_not_assumed_closed=True,transport_amendment_sha256=amendment_sha,
                        receipt_script_sha256_counts=dict(collections.Counter(x[0]['script_sha256'] for x in results.values())),
                        unattempted_hours=[t for t in range(start,end,3600) if t not in results],
                        ordinary_attempts_per_hour_per_run=2)
        body=codec.csv_bytes(rows)
        name='native-bid-1m.csv' if manifest['all_hours_validated_or_explicit_empty_200'] else 'native-bid-1m.partial.csv'
        codec.atomic_bytes(target/name,body)
        manifest['csv']={'path':name,'sha256':codec.sha(body),'bytes':len(body),
                         'incomplete':not manifest['all_hours_validated_or_explicit_empty_200']}
        codec.atomic_json(target/'MANIFEST.json',manifest)
        codec.atomic_json(target/'run-manifests'/(str(time.time_ns())+'.json'),manifest)
        return manifest

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--protocol',required=True,type=Path);ap.add_argument('--target',required=True,type=Path)
    ap.add_argument('--cache',action='append',default=[],type=Path)
    ap.add_argument('--max-requests',default=24,type=int)
    ap.add_argument('--execute',action='store_true')
    a=ap.parse_args(argv);verify_codec();p,raw,psha,start,end=load_protocol(a.protocol)
    if a.max_requests<0:raise ValueError('Request limit must be nonnegative')
    if not a.execute:
        print(json.dumps({'dry_run':True,'network_requests':0,'protocol_sha256':psha,
                          'start':codec.clocks(start),'end_exclusive':codec.clocks(end),
                          'requested_hours':(end-start)//3600,'cache_roots':[str(x) for x in a.cache],
                          'max_requests_per_run':a.max_requests,'csv_side':'BID','raw_sides':['BID','ASK']}));return 0
    m=run(a.target,p,raw,psha,start,end,a.cache,a.max_requests)
    print(json.dumps({k:m[k] for k in ['network_requests_this_run','stop_reason','processed_hours','expected_hours','hour_states','minute_count','missing_minutes','all_hours_validated_or_explicit_empty_200']}))
    return 0 if m['all_hours_validated_or_explicit_empty_200'] else 2

if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError) as e:
        print(json.dumps({'state':'STOPPED','error_type':type(e).__name__,'reason':str(e)}));raise SystemExit(2)
