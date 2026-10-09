"""16 original V4 cases retained; cross-version fixtures are separate and synthetic."""
import contextlib,io,json,os,signal,tempfile,unittest,urllib.error,urllib.request
from pathlib import Path
from unittest.mock import patch
import acquire_month_v4 as a
from synthetic_helpers import START,packet

def routed(t,route='direct-https',state=None,status=None):
 if state is None:x,b,rows=packet(t)
 else:x,b,rows={'requested_url':a.codec.hour_url(t),'state':state,'status':status},None,[]
 x.update(a.routing_metadata(a.codec.hour_url(t),route));return x,b,rows

class Response:
 def __init__(self,body,url,status=200):self.body,self.url,self.status=body,url,status;self.headers={'Content-Length':str(len(body))}
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,*args):return self.body
 def geturl(self):return self.url
 def getcode(self):return self.status

class V4(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.target=self.root/'target'
  self.p=json.loads((Path(__file__).parent/'FROZEN_MONTH_PROTOCOL.json').read_bytes());self.raw=json.dumps(self.p).encode();self.sha=a.codec.sha(self.raw)
  self.patches=[patch.object(m,'GLOBAL_RATE_LIMIT_PATH',self.root/'shared-cooldown.json') for m in (a,)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in self.patches:p.stop()
  self.tmp.cleanup()
 def runit(self,fetcher=routed,limit=100,hours=3,route='direct-https',stop_requested=lambda:False,module=a):
  with contextlib.redirect_stdout(io.StringIO()):
   kw=dict(fetcher=fetcher,sleeper=lambda _:None,now=lambda:10000)
   if module is a:kw.update(route=route,stop_requested=stop_requested)
   return module.run(self.target,self.p,self.raw,self.sha,START,START+hours*3600,[],limit,**kw)
 def test_actual_direct_wire_and_canonical_receipt_without_fake_redirect(self):
  seen=[];_,body,_=packet(START);wire=a.routing_metadata(a.codec.hour_url(START),'direct-https')['wire_requested_url']
  class Inner:
   def open(self,req,timeout):seen.append(req.full_url);return Response(body,wire)
  with patch.object(a.urllib.request,'build_opener',return_value=Inner()):x,b,rows=a.fetch(START,route='direct-https')
  self.assertEqual(seen,[wire]);self.assertEqual(x['requested_url'],a.codec.hour_url(START))
  self.assertEqual(x['wire_requested_url'],wire);self.assertEqual(x['redirects'],[])
  self.assertEqual(x['documented_route_override']['kind'],'DIRECT_DOCUMENTED_PROVIDER_HTTPS_SAME_PATH')
  self.assertEqual(b,body);self.assertEqual(x['state'],'OK');self.assertEqual(rows,packet(START)[2])
 def test_canonical_option_reports_actual_original_wire(self):
  seen=[];_,body,_=packet(START)
  class Inner:
   def open(self,req,timeout):seen.append(req.full_url);return Response(body,req.full_url)
  with patch.object(a.urllib.request,'build_opener',return_value=Inner()):x,_,_=a.fetch(START)
  self.assertEqual(seen,[a.codec.hour_url(START)]);self.assertIsNone(x['documented_route_override'])
 def test_changed_wire_request_rejected_before_inner_open(self):
  original=a.codec.hour_url(START)
  class Inner:
   def open(self,*args,**kwargs):raise AssertionError('unexpected network')
  for url in [original.replace('www.dukascopy.com','example.com'),a.codec.hour_url(START+3600)]:
   with self.assertRaises(a.codec.AcquisitionError):
    a.RespectfulOpener(Inner(),{},original,'direct-https').open(urllib.request.Request(url),20)
 def test_false_final_host_and_path_never_accepted(self):
  _,body,_=packet(START)
  for url in ['https://example.com/datafeed/x',a.codec.hour_url(START+3600)]:
   class Inner:
    def open(self,req,timeout):return Response(body,url)
   with patch.object(a.urllib.request,'build_opener',return_value=Inner()):x,_,rows=a.fetch(START,route='direct-https')
   self.assertEqual(x['state'],'UNEXPECTED_REDIRECT');self.assertEqual(rows,[])
 def test_false_final_path_503_is_not_transient(self):
  class Inner:
   def open(self,req,timeout):raise urllib.error.HTTPError(a.codec.hour_url(START+3600),503,'bad',{},io.BytesIO(b'bad'))
  with patch.object(a.urllib.request,'build_opener',return_value=Inner()):x,_,_=a.fetch(START,route='direct-https')
  self.assertEqual(x['state'],'UNEXPECTED_REDIRECT');self.assertFalse(a.ordinary_transient(x))
 def test_direct_429_records_real_wire_and_global_stop(self):
  wire=a.routing_metadata(a.codec.hour_url(START),'direct-https')['wire_requested_url'];calls=[]
  class Inner:
   def open(self,req,timeout):calls.append(req.full_url);raise urllib.error.HTTPError(wire,429,'limit',{'Retry-After':'90'},io.BytesIO(b''))
  with patch.object(a.urllib.request,'build_opener',return_value=Inner()),patch.object(a.time,'time',return_value=10000):
   m=self.runit(lambda t:a.fetch(t,route='direct-https'))
  self.assertEqual(calls,[wire]);self.assertEqual(m['stop_reason'],'RATE_LIMITED')
  cooldown=json.loads(a.GLOBAL_RATE_LIMIT_PATH.read_bytes());self.assertEqual(cooldown['retry_not_before_epoch'],10090)
  self.assertEqual(cooldown['wire_requested_url'],wire);self.assertEqual(cooldown['redirects'],[])
 def test_shared_cooldown_blocks_direct_network(self):
  a.codec.atomic_json(a.GLOBAL_RATE_LIMIT_PATH,{'retry_not_before_epoch':10100})
  m=self.runit(lambda t:self.fail('network during cooldown'))
  self.assertEqual(m['network_requests_this_run'],0);self.assertEqual(m['stop_reason'],'RETRY_AFTER_PENDING')
 def test_auth_failure_still_stops(self):
  calls=[]
  def fetch(t):calls.append(t);return routed(t,state='HTTP_ERROR',status=403)
  m=self.runit(fetch);self.assertEqual(calls,[START]);self.assertEqual(m['stop_reason'],'SOURCE_OR_TRANSPORT_ERROR')
 def test_503_attempts_bounded_then_later_hours(self):
  calls=[]
  def fetch(t):calls.append(t);return routed(t,state='HTTP_ERROR',status=503) if t==START else routed(t)
  m=self.runit(fetch);self.assertEqual(calls,[START,START,START+3600,START+7200])
  self.assertEqual(m['hour_states'],{'HTTP_ERROR':1,'OK':2});self.assertIsNone(m['stop_reason'])
 def test_no_wire_evidence_cannot_be_invented_on_resume(self):
  self.runit(hours=1);p=next((self.target/'hours').glob('*.json'));x=json.loads(p.read_bytes())
  del x['attempts'][-1]['wire_requested_url'];p.write_text(json.dumps(x))
  with self.assertRaisesRegex(ValueError,'wire route evidence'):self.runit(hours=1)
 def test_changed_v4_route_is_not_silent(self):
  self.runit(limit=0)
  with self.assertRaisesRegex(ValueError,'identity changed'):self.runit(limit=0,route='canonical')
 def test_checkpoint_publishes_during_run_and_final_manifest_clears_in_progress(self):
  def fetch(t):
   if t==START+24*3600:
    m=json.loads((self.target/'MANIFEST.json').read_bytes());self.assertTrue(m['run_in_progress']);self.assertEqual(m['network_requests_this_run'],24)
   return routed(t)
  m=self.runit(fetch,hours=25)
  self.assertFalse(m['run_in_progress']);self.assertEqual(len(list((self.target/'checkpoint-manifests').glob('*.json'))),1)
  self.assertEqual(len(list((self.target/'run-manifests').glob('*.json'))),1)
 def test_requested_stop_persists_inflight_then_final_manifest(self):
  stopped=[False];calls=[]
  def fetch(t):calls.append(t);stopped[0]=True;return routed(t)
  m=self.runit(fetch,stop_requested=lambda:stopped[0])
  self.assertEqual(calls,[START]);self.assertEqual(m['stop_reason'],'INTERRUPTED');self.assertEqual(m['hour_states'],{'OK':1})
  self.assertFalse(m['run_in_progress']);self.assertEqual(m['unattempted_hours'],[START+3600,START+7200])
 def test_main_sigint_handler_stops_cleanly_and_restores_previous(self):
  p=self.root/'p.json';p.write_bytes(self.raw);previous=signal.getsignal(signal.SIGINT)
  def fetch(t,timeout=20,route='canonical'):
   os.kill(os.getpid(),signal.SIGINT);return routed(t,route=route)
  with patch.object(a,'fetch',side_effect=fetch),contextlib.redirect_stdout(io.StringIO()):
   code=a.main(['--protocol',str(p),'--target',str(self.target),'--route','direct-https','--max-requests','3','--execute'])
  self.assertEqual(code,130);self.assertEqual(signal.getsignal(signal.SIGINT),previous)
  m=json.loads((self.target/'MANIFEST.json').read_bytes());self.assertEqual(m['stop_reason'],'INTERRUPTED');self.assertEqual(m['network_requests_this_run'],1)
 def test_spacing_not_lowered(self):
  sleeps=[]
  with contextlib.redirect_stdout(io.StringIO()):
   a.run(self.target,self.p,self.raw,self.sha,START,START+7200,[],10,fetcher=routed,sleeper=sleeps.append,now=lambda:10000,route='direct-https')
  self.assertIn(2,sleeps)
 def test_codec_bytes_stay_pinned(self):
  a.verify_codec();self.assertEqual(a.codec.sha(Path(a.codec.__file__).read_bytes()),a.CODEC_SHA)

if __name__=='__main__':unittest.main()
