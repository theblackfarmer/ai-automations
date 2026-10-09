"""Native daily BID acquisition. No strategy selection, interpolation or orders."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import lzma
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
PREVIOUS_DRIVER_SHA='9123ff24bfff067b91fef27ba91330e273ab57495d8a2f52619cf0c684e06f33'
# Preserve the existing Mac shared cooldown; use a writable OS temp root on Linux.
_RATE_ROOT=Path('/private/tmp') if sys.platform=='darwin' else Path(tempfile.gettempdir())
COOLDOWN=Path(os.environ.get('SEPTEMBER_RATE_LIMIT_FILE',str(_RATE_ROOT/'september-month-data/GLOBAL_RATE_LIMIT.json')))

def digest(b):return hashlib.sha256(b).hexdigest()
def clocks(t):
    dt=datetime.fromtimestamp(t,timezone.utc)
    return {'utc':dt.isoformat().replace('+00:00','Z'),'CT':dt.astimezone(ZoneInfo('America/Chicago')).isoformat(),'ET':dt.astimezone(ZoneInfo('America/New_York')).isoformat()}
def epoch(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def atomic(path,b):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
    tmp.replace(path)
def save(path,value):atomic(path,(json.dumps(value,indent=2,sort_keys=True)+'\n').encode())
def url(day):
    d=datetime.fromtimestamp(day,timezone.utc)
    return f'https://datafeed.dukascopy.com/datafeed/USATECHIDXUSD/{d.year}/{d.month-1:02d}/{d.day:02d}/BID_candles_min_1.bi5'

def decode(body,day):
    if not body:return {},[]
    d=lzma.LZMADecompressor();raw=d.decompress(body,max_length=4_000_001)
    if len(raw)>4_000_000 or not d.eof or d.unused_data or len(raw)%24:raise ValueError('Invalid compressed record stream')
    observed={};excluded=[];last=-1
    for sec,o,c,l,h,v in struct.iter_unpack('>IIIIIf',raw):
        if sec%60 or not last<sec<86400:raise ValueError('Invalid minute clock')
        last=sec
        if not math.isfinite(v) or v<0:raise ValueError('Invalid volume')
        if v==0:
            if not o==c==l==h:raise ValueError('Zero-volume nonflat record is unresolved')
            excluded.append(day+sec);continue
        if min(o,c,l,h)<=0 or not l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid observed OHLC')
        observed[day+sec]=(o,h,l,c)
    return observed,excluded

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Unexpected redirect; source not adopted')

def retry_deadline(value,now):
    try:
        if value and value.isdigit():return now+max(1,int(value))
        return max(now+1,parsedate_to_datetime(value).timestamp())
    except (ValueError,TypeError,OverflowError):return now+3600

def fetch(day):
    receipt={'requested_url':url(day),'day':clocks(day),'started_at':clocks(int(time.time()))};body=None
    try:
        req=urllib.request.Request(url(day),headers={'User-Agent':'IsaacResearchDataAcquisition/1.0','Accept-Encoding':'identity'})
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as r:
            receipt.update(status=r.status,final_url=r.geturl(),content_length=r.headers.get('Content-Length'))
            body=r.read(4_000_001)
        if receipt['status']!=200 or receipt['final_url']!=url(day) or len(body)>4_000_000:raise ValueError('Unexpected source response')
        length=receipt['content_length']
        if length is not None and (not length.isdigit() or int(length)!=len(body)):raise ValueError('Content-Length mismatch')
        rows,excluded=decode(body,day)
        receipt.update(state='OK',body_sha256=digest(body),body_bytes=len(body),observed_minutes=len(rows),excluded_zero_volume_placeholders=len(excluded))
    except urllib.error.HTTPError as e:
        receipt.update(state='HTTP_ERROR',status=e.code,final_url=e.geturl())
        if e.code==429:
            receipt.update(state='RATE_LIMITED',retry_not_before_epoch=retry_deadline(e.headers.get('Retry-After'),time.time()))
        e.close()
    except (TimeoutError,urllib.error.URLError,OSError) as e:receipt.update(state='TRANSPORT_ERROR',error_type=type(e).__name__)
    except (ValueError,lzma.LZMAError) as e:receipt.update(state='SOURCE_ERROR',reason=str(e))
    receipt['finished_at']=clocks(int(time.time()))
    return receipt,body

def reference(path):
    raw=path.read_bytes();r=json.loads(raw);data=(path.parent/r['csv']['path']).read_bytes()
    if digest(data)!=r['csv']['sha256'] or len(data)!=r['csv']['bytes']:raise ValueError('Reference bytes changed')
    if r['feed']!='Dukascopy USATECHIDXUSD' or r['side']!='BID':raise ValueError('Wrong reference feed')
    result={}
    for row in csv.DictReader(io.StringIO(data.decode())):
        t=int(row['epoch'])
        if t in result:raise ValueError('Duplicate reference minute')
        values=[]
        for key in ('open','high','low','close'):
            n=Decimal(row[key])*1000
            if n!=int(n):raise ValueError('Reference scale mismatch')
            values.append(int(n))
        result[t]=tuple(values)
    if len(result)!=r['minute_count']:raise ValueError('Reference count mismatch')
    return result,{'manifest_sha256':digest(raw),'csv_sha256':digest(data),'minutes':len(result)}

def parity(rows,ref,covered_days):
    eligible={t:v for t,v in ref.items() if t//86400*86400 in covered_days}
    missing=sorted(eligible.keys()-rows.keys());mismatch=sorted(t for t in eligible.keys()&rows.keys() if eligible[t]!=rows[t])
    return {'compared_minutes':len(eligible),'exact_matches':len(eligible)-len(missing)-len(mismatch),'missing_reference_minutes':missing,'mismatch_minutes':mismatch,'passes':not missing and not mismatch}

def validate_cached(receipt,body,day,identity,legacy_verified=False):
    if (receipt.get('state')!='OK' or type(receipt.get('status')) is not int or receipt['status']!=200
        or receipt.get('requested_url')!=url(day) or receipt.get('final_url')!=url(day)
        or receipt.get('day')!=clocks(day) or receipt.get('amendment_sha256')!=identity['amendment_sha256']
        or receipt.get('script_sha256') not in ({identity['script_sha256'],PREVIOUS_DRIVER_SHA} if legacy_verified else {identity['script_sha256']})
        or receipt.get('body_sha256')!=digest(body) or type(receipt.get('body_bytes')) is not int
        or receipt['body_bytes']!=len(body)):
        raise ValueError('Cached source receipt contradicts source identity')
    if receipt.get('raw_body_sha256',digest(body))!=digest(body):raise ValueError('Cached raw hash differs')
    length=receipt.get('content_length')
    if length is not None and (not isinstance(length,str) or not length.isdigit() or int(length)!=len(body)):
        raise ValueError('Cached content length differs')
    rows,excluded=decode(body,day)
    for key,actual in [('observed_minutes',len(rows)),('excluded_zero_volume_placeholders',len(excluded))]:
        if type(receipt.get(key)) is not int or receipt[key]!=actual:raise ValueError('Cached minute counts differ')


def run(target,amendment,refpath):
    target.mkdir(parents=True,exist_ok=True)
    with (target/'RUN.lock').open('a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Native acquisition already running') from None
        return run_locked(target,amendment,refpath)


def run_locked(target,amendment,refpath):
    raw=amendment.read_bytes();protocol=json.loads(raw)
    if protocol['id']!='NATIVE_DAILY_BID_TRANSPORT_2026_10_09':raise ValueError('Wrong amendment')
    start=epoch(protocol['start_utc']);end=epoch(protocol['end_exclusive_utc'])
    if start%86400 or end%86400 or end-start!=61*86400:raise ValueError('Wrong fixed study window')
    if digest((ROOT/'engine.py').read_bytes())!=protocol['engine_sha256'] or digest((ROOT/'SPEC.json').read_bytes())!=protocol['spec_sha256']:raise ValueError('Frozen model changed')
    target.mkdir(parents=True,exist_ok=True)
    identity={'amendment_sha256':digest(raw),'script_sha256':digest(Path(__file__).read_bytes()),'reference':reference(refpath)[1]}
    ip=target/'IDENTITY.json'
    if ip.exists() and json.loads(ip.read_bytes())!=identity:
        previous_raw=ip.read_bytes();previous=json.loads(previous_raw)
        if dict(previous,script_sha256=identity['script_sha256'])!=identity or previous['script_sha256']!=PREVIOUS_DRIVER_SHA:
            raise ValueError('Identity changed')
        atomic(target/'identity-history'/(digest(previous_raw)+'.json'),previous_raw)
        save(target/'CACHE_REVALIDATION.json',{'previous_script_sha256':PREVIOUS_DRIVER_SHA,'current_script_sha256':identity['script_sha256'],'require_every_cache_receipt_revalidation':True})
    legacy_verified=False
    for held in (target/'identity-history').glob('*.json'):
        oldbytes=held.read_bytes();old=json.loads(oldbytes)
        if (held.stem==digest(oldbytes) and old.get('script_sha256')==PREVIOUS_DRIVER_SHA
            and dict(old,script_sha256=identity['script_sha256'])==identity):
            legacy_verified=True
    save(ip,identity);atomic(target/'NATIVE_DAILY_AMENDMENT.json',raw)
    ref,refmeta=reference(refpath)
    if not ref or any(type(t) is not int or t%60 or not start<=t<end for t in ref):
        raise ValueError('Reference must contain observed minutes within the study window')
    results={};allrows={};excluded=[];requests=0;last_request=0;stop=None
    for day in range(start,end,86400):
        rp=target/'days'/(str(day)+'.json');receipt=None
        if rp.exists():
            prior=json.loads(rp.read_bytes())
            if prior.get('state')=='OK':
                b=(target/'blobs'/(prior['body_sha256']+'.bi5')).read_bytes()
                validate_cached(prior,b,day,identity,legacy_verified)
                receipt=prior
        if receipt is None:
            for attempt in (1,2):
                if COOLDOWN.exists() and time.time()<json.loads(COOLDOWN.read_bytes())['retry_not_before_epoch']:
                    stop='SHARED_RETRY_AFTER_PENDING';break
                if requests>=protocol['network']['maximum_network_requests']:stop='REQUEST_LIMIT';break
                time.sleep(max(0,2-(time.time()-last_request)))
                if attempt==2:time.sleep(5)
                if COOLDOWN.exists() and time.time()<json.loads(COOLDOWN.read_bytes())['retry_not_before_epoch']:
                    stop='SHARED_RETRY_AFTER_PENDING';break
                last_request=time.time();requests+=1;r,b=fetch(day)
                r.update(attempt=attempt,amendment_sha256=identity['amendment_sha256'],script_sha256=identity['script_sha256'])
                if b is not None:
                    r['raw_body_sha256']=digest(b);atomic(target/'blobs'/(digest(b)+'.bi5'),b)
                save(target/'attempts'/(str(day)+'-'+str(time.time_ns())+'.json'),r);save(rp,r);receipt=r
                if r['state']=='RATE_LIMITED':save(COOLDOWN,r);stop='RATE_LIMITED';break
                if r['state']=='SOURCE_ERROR' or r.get('status') in (401,403):stop='SOURCE_OR_AUTH_ERROR';break
                if r['state']=='OK' or r.get('status')==404:break
            if stop:break
        results[day]=receipt
        if receipt['state']=='OK':
            body=(target/'blobs'/(receipt['body_sha256']+'.bi5')).read_bytes();rows,omitted=decode(body,day)
            if len(rows)!=receipt['observed_minutes'] or len(omitted)!=receipt['excluded_zero_volume_placeholders']:raise ValueError('Cached counts differ')
            allrows.update(rows);excluded.extend(omitted)
        print(json.dumps({'day':clocks(day)['utc'],'state':receipt['state'],'days_processed':len(results),'network_requests':requests}),flush=True)
    covered={day for day,r in results.items() if r['state']=='OK'};proof=parity(allrows,ref,covered)
    save(target/'PARITY.json',dict(proof,reference=refmeta))
    # Preserve all input evidence even when adoption is blocked. Never emit a
    # ready CSV after a reference mismatch.
    complete=len(covered)==(end-start)//86400
    counts={'OK':0,'EMPTY_200':0,'UNRESOLVED_NATIVE_DAY':0}
    for day in range(start,end,86400):
        for h in range(day,day+86400,3600):
            key='UNRESOLVED_NATIVE_DAY' if day not in covered else ('OK' if any(t in allrows for t in range(h,h+3600,60)) else 'EMPTY_200')
            counts[key]+=1
    manifest={'schema':'NATIVE_DAILY_BID_V1','symbol':'NAS100 CFD','feed':'Dukascopy USATECHIDXUSD','side':'BID','start':clocks(start),'end_exclusive':clocks(end),'expected_hours':(end-start)//3600,'processed_hours':len(covered)*24,'hour_states':counts,'hour_status_basis':'Hours classified from validated dailyHTTP200 files, not individual hourlytickrequests. Empty means zero observed native minutes, not an asserted closure.','all_hours_validated_or_explicit_empty_200':complete and proof['passes'],'minute_count':len(allrows),'native_days_validated':len(covered),'native_days_expected':61,'excluded_source_placeholder_minutes':len(excluded),'network_requests':requests,'parity':proof,'identity':identity,'stop_reason':stop,'complete_month_claim':False,'teacher_fidelity':False,'execution_allowed':False,'performance':None}
    save(target/'EXCLUDED_SOURCE_PLACEHOLDERS.json',{'reason':'Zero-volume allOHLCequal source records; not used as observed candles','timestamps':excluded})
    if proof['passes']:
        f=io.StringIO();w=csv.writer(f,lineterminator='\n');w.writerow(['epoch','open','high','low','close'])
        for t,values in sorted(allrows.items()):w.writerow([t]+[str(Decimal(v)/1000) for v in values])
        b=f.getvalue().encode();atomic(target/'native-bid-1m.csv',b)
        manifest['csv']={'path':'native-bid-1m.csv','sha256':digest(b),'bytes':len(b),'incomplete':not complete}
    else:manifest['stop_reason']='REFERENCE_PARITY_FAILED'
    manifest['run_finished_at']=clocks(int(time.time()));save(target/'MANIFEST.json',manifest)
    print(json.dumps({k:manifest[k] for k in ('native_days_validated','minute_count','excluded_source_placeholder_minutes','all_hours_validated_or_explicit_empty_200','stop_reason')}))
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--target',required=True,type=Path);p.add_argument('--reference',required=True,type=Path);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if not a.execute:raise SystemExit('Read frozen NATIVE_DAILY_AMENDMENT.json; --execute required for bounded network acquisition')
    result=run(a.target,ROOT/'NATIVE_DAILY_AMENDMENT.json',a.reference)
    raise SystemExit(0 if result['all_hours_validated_or_explicit_empty_200'] else 2)
