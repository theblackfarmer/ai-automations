import contextlib,copy,fcntl,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import acquire_native_daily_v2 as a
import _native_daily_test_support as base
def old_identity(ref):
    return dict(amendment_sha256=a.digest(base.AMENDMENT.read_bytes()),script_sha256=a.PREVIOUS_DRIVER_SHA,reference=a.reference(ref)[1])

DecoderControls=base.DecoderControls
AdoptionAttacks=base.AdoptionAttacks

class V2Neighbors(unittest.TestCase):
    def test_honest_pinned_old_identity_migration(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=base.fixture(Path(d),lambda day,r:r.update(script_sha256=a.PREVIOUS_DRIVER_SHA))
            identity=dict(amendment_sha256=a.digest(base.AMENDMENT.read_bytes()),script_sha256=a.PREVIOUS_DRIVER_SHA,reference=a.reference(ref)[1])
            old=json.dumps(identity).encode();(target/'IDENTITY.json').write_bytes(old)
            m=base.offline_run(target,ref)
            self.assertTrue(m['all_hours_validated_or_explicit_empty_200'])
            self.assertEqual((target/'identity-history'/(a.digest(old)+'.json')).read_bytes(),old)
            self.assertTrue((target/'CACHE_REVALIDATION.json').exists())

    def test_unknown_old_driver_or_reference_identity_rejected(self):
        for field in ('script_sha256','reference'):
            with tempfile.TemporaryDirectory() as d:
                target,ref=base.fixture(Path(d));identity=dict(amendment_sha256=a.digest(base.AMENDMENT.read_bytes()),script_sha256=a.PREVIOUS_DRIVER_SHA,reference=a.reference(ref)[1])
                identity[field]='wrong';(target/'IDENTITY.json').write_text(json.dumps(identity))
                with self.assertRaises(ValueError):base.offline_run(target,ref)

    def test_corrupt_cache_during_migration_blocks_adoption(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=base.fixture(Path(d),lambda day,r:r.update(script_sha256=a.PREVIOUS_DRIVER_SHA,status=404))
            identity=dict(amendment_sha256=a.digest(base.AMENDMENT.read_bytes()),script_sha256=a.PREVIOUS_DRIVER_SHA,reference=a.reference(ref)[1]);(target/'IDENTITY.json').write_text(json.dumps(identity))
            with self.assertRaises(ValueError):base.offline_run(target,ref)
            self.assertFalse((target/'MANIFEST.json').exists());self.assertFalse((target/'native-bid-1m.csv').exists())

    def test_cache_clock_raw_hash_count_and_type_neighbors_rejected(self):
        mutations=[lambda r:r['day'].update(CT='wrong'),lambda r:r.update(raw_body_sha256='0'*64),
                   lambda r:r.update(observed_minutes=True),lambda r:r.update(body_bytes=False),
                   lambda r:r.update(content_length='abc'),lambda r:r.update(excluded_zero_volume_placeholders=-1)]
        for mutation in mutations:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                target,ref=base.fixture(Path(d),lambda day,r:mutation(r))
                with self.assertRaises(ValueError):base.offline_run(target,ref)

    def test_rate_limit_created_during_retry_backoff_blocks_second_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=base.fixture(Path(d));min((target/'days').glob('*.json')).unlink();cooldown=target/'cooldown.json';calls=[]
            def pacing(seconds):
                if seconds==5:cooldown.write_text(json.dumps({'retry_not_before_epoch':a.time.time()+3600}))
            def first(day):calls.append(day);return dict(state='TRANSPORT_ERROR',error_type='TimeoutError'),None
            with patch.object(a,'ROOT',base.PROJECT),patch.object(a,'COOLDOWN',cooldown),patch.object(a.time,'sleep',side_effect=pacing),patch.object(a,'fetch',side_effect=first),contextlib.redirect_stdout(io.StringIO()):
                m=a.run(target,base.AMENDMENT,ref)
            self.assertEqual(len(calls),1);self.assertEqual(m['stop_reason'],'SHARED_RETRY_AFTER_PENDING')
            self.assertFalse(m['all_hours_validated_or_explicit_empty_200'])

    def test_target_lock_blocks_second_run(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=base.fixture(Path(d))
            with (target/'RUN.lock').open('a+b') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'already running'):base.offline_run(target,ref)

    def test_active_cooldown_does_not_prevent_readonly_valid_cache(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=base.fixture(Path(d));(target/'cooldown.json').write_text(json.dumps({'retry_not_before_epoch':a.time.time()+3600}))
            self.assertTrue(base.offline_run(target,ref)['all_hours_validated_or_explicit_empty_200'])

    def test_http429_retry_after_and_no_body_promotion(self):
        from email.message import Message
        headers=Message();headers['Retry-After']='30'
        error=a.urllib.error.HTTPError(a.url(base.DAY),429,'Rate limited',headers,io.BytesIO(b'not a candle body'))
        class Opener:
            def open(self,*args,**kwargs):raise error
        with patch.object(a.urllib.request,'build_opener',return_value=Opener()),patch.object(a.time,'time',return_value=1000000):
            receipt,body=a.fetch(base.DAY)
        self.assertEqual(receipt['state'],'RATE_LIMITED');self.assertEqual(receipt['retry_not_before_epoch'],1000030);self.assertIsNone(body)

    def test_http200_wrong_url_length_and_bad_body_fail_closed(self):
        class Response:
            status=200
            def __init__(self,body,url,length):self.body=body;self.url=url;self.headers={'Content-Length':length}
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def geturl(self):return self.url
            def read(self,n):return self.body[:n]
        class Opener:
            def __init__(self,response):self.response=response
            def open(self,*args,**kwargs):return self.response
        for data,url,length in [(base.GOOD,'https://example.invalid/','1'),(base.GOOD,a.url(base.DAY),'1'),(b'junk',a.url(base.DAY),'4')]:
            with patch.object(a.urllib.request,'build_opener',return_value=Opener(Response(data,url,length))):receipt,_=a.fetch(base.DAY)
            self.assertEqual(receipt['state'],'SOURCE_ERROR')


class MigrationBoundary(unittest.TestCase):
    def check_orphan_legacy_cache(self, current_identity):
        with tempfile.TemporaryDirectory() as d:
            target, ref = base.fixture(
                Path(d), lambda day, r: r.update(script_sha256=a.PREVIOUS_DRIVER_SHA)
            )
            if current_identity:
                identity = {
                    'amendment_sha256': a.digest(base.AMENDMENT.read_bytes()),
                    'script_sha256': a.digest(Path(a.__file__).read_bytes()),
                    'reference': a.reference(ref)[1],
                }
                (target / 'IDENTITY.json').write_text(json.dumps(identity))
            with self.assertRaises(ValueError):
                base.offline_run(target, ref)

    def test_legacy_cache_without_identity_must_reject(self):
        self.check_orphan_legacy_cache(False)

    def test_legacy_cache_with_current_identity_but_no_history_must_reject(self):
        self.check_orphan_legacy_cache(True)


class Interrupted(Exception):
    pass


class MigrationResume(unittest.TestCase):
    def test_legitimate_interrupted_cache_validation_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            target, ref = base.fixture(Path(d), lambda day, r: r.update(script_sha256=a.PREVIOUS_DRIVER_SHA))
            oldraw = json.dumps(old_identity(ref)).encode()
            (target/'IDENTITY.json').write_bytes(oldraw)
            real = a.validate_cached
            calls = []
            def validate(*args, **kwargs):
                calls.append(args[2])
                if len(calls) == 3:
                    raise Interrupted('Simulated process interruption during cache revalidation')
                return real(*args, **kwargs)
            with patch.object(a, 'validate_cached', side_effect=validate), self.assertRaises(Interrupted):
                base.offline_run(target, ref)
            self.assertFalse((target/'MANIFEST.json').exists())
            history = target/'identity-history'/(a.digest(oldraw)+'.json')
            self.assertEqual(history.read_bytes(), oldraw)
            self.assertEqual(json.loads((target/'IDENTITY.json').read_text())['script_sha256'], a.digest(Path(a.__file__).read_bytes()))
            with patch.object(a, 'validate_cached', wraps=real) as validator:
                result = base.offline_run(target, ref)
            self.assertEqual(validator.call_count, 61)
            self.assertTrue(result['all_hours_validated_or_explicit_empty_200'])
            self.assertEqual(result['network_requests'], 0)
            self.assertEqual(history.read_bytes(), oldraw)

    def test_interrupted_before_new_identity_commit_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            target, ref = base.fixture(Path(d), lambda day, r: r.update(script_sha256=a.PREVIOUS_DRIVER_SHA))
            oldraw = json.dumps(old_identity(ref)).encode()
            (target/'IDENTITY.json').write_bytes(oldraw)
            real = a.save
            def save(path, value):
                if path.name == 'IDENTITY.json':
                    raise Interrupted('Simulated interruption before current identity commit')
                return real(path, value)
            with patch.object(a, 'save', side_effect=save), self.assertRaises(Interrupted):
                base.offline_run(target, ref)
            self.assertEqual((target/'IDENTITY.json').read_bytes(), oldraw)
            self.assertEqual((target/'identity-history'/(a.digest(oldraw)+'.json')).read_bytes(), oldraw)
            result = base.offline_run(target, ref)
            self.assertTrue(result['all_hours_validated_or_explicit_empty_200'])
            self.assertEqual(result['network_requests'], 0)

    def test_bad_history_bindings_do_not_authorize_legacy_receipts(self):
        mutations = {
            'reference': lambda old: old.update(reference={'wrong': True}),
            'amendment': lambda old: old.update(amendment_sha256='0'*64),
            'driver': lambda old: old.update(script_sha256='0'*64),
            'extra_field': lambda old: old.update(unbound_extra_field='not-current-identity'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                target, ref = base.fixture(Path(d), lambda day, r: r.update(script_sha256=a.PREVIOUS_DRIVER_SHA))
                old = old_identity(ref)
                mutate(old)
                raw = json.dumps(old).encode()
                history = target/'identity-history'; history.mkdir()
                (history/(a.digest(raw)+'.json')).write_bytes(raw)
                with self.assertRaises(ValueError):
                    base.offline_run(target, ref)

    def test_history_filename_must_bind_exact_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            target, ref = base.fixture(Path(d), lambda day, r: r.update(script_sha256=a.PREVIOUS_DRIVER_SHA))
            raw = json.dumps(old_identity(ref)).encode()
            history = target/'identity-history'; history.mkdir()
            (history/('0'*64+'.json')).write_bytes(raw)
            with self.assertRaises(ValueError):
                base.offline_run(target, ref)

    def test_preserved_old_identity_is_sufficient_after_interrupted_metadata_save(self):
        with tempfile.TemporaryDirectory() as d:
            target, ref = base.fixture(Path(d), lambda day, r: r.update(script_sha256=a.PREVIOUS_DRIVER_SHA))
            old = old_identity(ref); raw = json.dumps(old).encode()
            current = dict(old, script_sha256=a.digest(Path(a.__file__).read_bytes()))
            (target/'IDENTITY.json').write_text(json.dumps(current))
            history = target/'identity-history'; history.mkdir()
            (history/(a.digest(raw)+'.json')).write_bytes(raw)
            self.assertFalse((target/'CACHE_REVALIDATION.json').exists())
            result = base.offline_run(target, ref)
            self.assertTrue(result['all_hours_validated_or_explicit_empty_200'])
            self.assertEqual(result['network_requests'], 0)


class ModelIdentityGuard(unittest.TestCase):
    def test_changed_frozen_model_blocks_before_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=base.fixture(Path(d))
            wrong=Path(d)/'wrong-model';wrong.mkdir()
            (wrong/'engine.py').write_text('different model')
            (wrong/'SPEC.json').write_bytes((base.PROJECT/'SPEC.json').read_bytes())
            with patch.object(base,'PROJECT',wrong),self.assertRaisesRegex(ValueError,'Frozen model changed'):
                base.offline_run(target,ref)

class PortableCooldown(unittest.TestCase):
    def test_explicit_shared_cooldown_config_survives_fresh_import(self):
        import os,subprocess,sys
        with tempfile.TemporaryDirectory() as d:
            shared=str(Path(d)/'shared-rate-state.json')
            result=subprocess.run([sys.executable,'-B','-c','import acquire_native_daily_v2 as a; print(a.COOLDOWN)'],cwd=Path(__file__).resolve().parent,env=dict(os.environ,SEPTEMBER_RATE_LIMIT_FILE=shared),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),shared)
