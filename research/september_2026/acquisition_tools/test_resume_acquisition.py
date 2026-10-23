"""Offline tests using synthetic packets; no acquired market data."""
import contextlib,fcntl,io,json,signal,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import resume_acquisition as w
from synthetic_helpers import START,packet

class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.target=self.root/'target';self.clock=[0.]
        self.protocol=Path(__file__).parent/'FROZEN_MONTH_PROTOCOL.json'
        self.p,self.raw,self.sha,_,_=w.acquisition.load_protocol(self.protocol)
        p=patch.object(w.acquisition,'GLOBAL_RATE_LIMIT_PATH',self.root/'cooldown.json')
        p.start();self.addCleanup(p.stop)
    def packet(self,t):
        attempt,body,rows=packet(t)
        attempt.update(w.acquisition.routing_metadata(w.acquisition.codec.hour_url(t),'direct-https'))
        return attempt,body,rows
    def runit(self,fetcher=None,max_requests=10,wall_seconds=60):
        with contextlib.redirect_stdout(io.StringIO()):
            return w.run_bounded(self.target,self.p,self.raw,self.sha,START,START+7200,
                max_requests,wall_seconds,'direct-https',monotonic=lambda:self.clock[0],
                fetcher=fetcher or self.packet,sleeper=lambda _:None,now=lambda:10000)
    def test_deadline_persists_then_resume_reuses_hour(self):
        calls=[]
        def fetch(t):calls.append(t);self.clock[0]=61;return self.packet(t)
        first=self.runit(fetch)
        self.assertEqual(first['stop_reason'],'INTERRUPTED');self.assertEqual(calls,[START])
        receipt=next((self.target/'hours').glob('*.json'));original=receipt.read_bytes()
        self.assertEqual(json.loads((self.target/'MANIFEST.json').read_bytes()),first)
        calls.clear()
        def resume(t):calls.append(t);return self.packet(t)
        second=self.runit(resume)
        self.assertEqual(calls,[START+3600]);self.assertTrue(second['all_hours_validated_or_explicit_empty_200'])
        self.assertEqual(receipt.read_bytes(),original)
    def test_both_signals_persist_and_restore_handlers(self):
        for sig in (signal.SIGINT,signal.SIGTERM):
            self.target=self.root/str(sig)
            before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
            def fetch(t):signal.getsignal(sig)(sig,None);return self.packet(t)
            result=self.runit(fetch)
            self.assertEqual(result['stop_reason'],'INTERRUPTED')
            self.assertEqual(result['network_requests_this_run'],1)
            self.assertEqual(result['hour_states'],{'OK':1})
            self.assertEqual(before,{s:signal.getsignal(s) for s in before})
    def test_handlers_restore_on_error(self):
        before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
        with patch.object(w.acquisition,'run',side_effect=ValueError('test failure')):
            with self.assertRaisesRegex(ValueError,'test failure'):self.runit()
        self.assertEqual(before,{s:signal.getsignal(s) for s in before})
    def test_zero_and_invalid_budgets(self):
        for requests,seconds in ((0,60),(10,0)):
            result=self.runit(lambda _:self.fail('unexpected network'),requests,seconds)
            self.assertEqual(result['network_requests_this_run'],0)
        for requests,seconds in ((-1,60),(1,-1),(True,1),(1,float('nan')),(1,float('inf'))):
            with self.assertRaises(ValueError):self.runit(max_requests=requests,wall_seconds=seconds)
    def test_request_limit_and_cooldown_preserved(self):
        result=self.runit(max_requests=1)
        self.assertEqual(result['network_requests_this_run'],1)
        self.assertEqual(result['stop_reason'],'REQUEST_BUDGET_REACHED')
        w.acquisition.codec.atomic_json(self.root/'cooldown.json',{'retry_not_before_epoch':10100})
        result=self.runit(lambda _:self.fail('network during cooldown'))
        self.assertEqual(result['stop_reason'],'RETRY_AFTER_PENDING')
        self.assertEqual(result['network_requests_this_run'],0)
    def test_existing_lock_blocks_duplicate_collector(self):
        self.target.mkdir()
        with (self.target/'RUN.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'holds target lock'):
                self.runit(lambda _:self.fail('duplicate collector requested network'))
    def test_corrupted_cache_stops_before_network(self):
        self.runit(max_requests=1)
        receipt=next((self.target/'hours').glob('*.json'))
        data=json.loads(receipt.read_bytes());data['requested_url']='https://example.com/'
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'identity changed'):
            self.runit(lambda _:self.fail('corrupt cache triggered network'))
    def test_cli_errors_have_distinct_non_incomplete_exit(self):
        script=Path(__file__).parent/'resume_acquisition.py'
        malformed=self.root/'bad-protocol.json';malformed.write_text('{}')
        occupied=self.root/'occupied';occupied.mkdir();(occupied/'unexpected').write_text('preserve')
        cases=[['--protocol',str(self.root/'missing.json')],
               ['--protocol',str(malformed)],
               ['--protocol',str(self.protocol),'--max-requests','-1'],
               ['--protocol',str(self.protocol),'--max-seconds','nan'],
               ['--protocol',str(self.protocol),'--target',str(occupied),
                '--max-requests','0','--execute']]
        for extra in cases:
            with self.subTest(args=extra):
                result=subprocess.run([sys.executable,'-B',str(script),'--target',str(self.target),*extra],
                                      capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,1,result.stderr)
                self.assertEqual(json.loads(result.stdout)['state'],'STOPPED')
        self.assertEqual((occupied/'unexpected').read_text(),'preserve')
    def test_cli_parse_errors_are_not_incomplete(self):
        script=Path(__file__).parent/'resume_acquisition.py'
        for extra in ([],['--protocol',str(self.protocol),'--target',str(self.target),
                          '--max-requests','invalid']):
            result=subprocess.run([sys.executable,'-B',str(script),*extra],
                                  capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(self.target.exists())
    def test_cli_valid_incomplete_is_still_two(self):
        script=Path(__file__).parent/'resume_acquisition.py'
        result=subprocess.run([sys.executable,'-B',str(script),'--protocol',str(self.protocol),
            '--target',str(self.target),'--max-requests','0','--execute'],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2,result.stderr)
        final=json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(final['network_requests_this_run'],0)
        self.assertEqual(final['stop_reason'],'REQUEST_BUDGET_REACHED')
    def test_request_timeout_forwarded_without_changing_route(self):
        for value in (20,60,120):
            self.target=self.root/('timeout-'+str(value))
            with patch.object(w.acquisition,'fetch',side_effect=lambda epoch,**kwargs:self.packet(epoch)) as fetch:
                with contextlib.redirect_stdout(io.StringIO()):
                    w.run_bounded(self.target,self.p,self.raw,self.sha,START,START+3600,1,60,
                        'direct-https',request_timeout=value,monotonic=lambda:0,sleeper=lambda _:None)
            fetch.assert_called_once_with(START,timeout=value,route='direct-https')
    def test_invalid_request_timeout_cli_exits_one(self):
        script=Path(__file__).parent/'resume_acquisition.py'
        for value in ('0','-1','nan','inf','121'):
            result=subprocess.run([sys.executable,'-B',str(script),'--protocol',str(self.protocol),
                '--target',str(self.target),'--request-timeout',value],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(self.target.exists())
    def test_dry_run_never_creates_target(self):
        with contextlib.redirect_stdout(io.StringIO()),patch.object(w.acquisition,'run',side_effect=AssertionError('run')):
            self.assertEqual(w.main(['--protocol',str(self.protocol),'--target',str(self.target),'--max-seconds','0']),0)
        self.assertFalse(self.target.exists())

if __name__=='__main__':unittest.main()
