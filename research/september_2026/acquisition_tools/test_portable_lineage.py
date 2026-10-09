"""Source-only legacy-lineage controls; no historical acquirer execution required."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import acquire_month_v4 as a
from synthetic_helpers import START, packet

ROOT = Path(__file__).parent


class PortableLineage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root/'target'
        self.raw = (ROOT/'FROZEN_MONTH_PROTOCOL.json').read_bytes()
        self.p = json.loads(self.raw)
        self.psha = a.codec.sha(self.raw)
        self.current = a.codec.sha(Path(a.__file__).read_bytes())
        self.cooldown = patch.object(a, 'GLOBAL_RATE_LIMIT_PATH', self.root/'global.json')
        self.cooldown.start()

    def tearDown(self):
        self.cooldown.stop()
        self.temp.cleanup()

    def identity(self, version):
        amendment = ROOT/('preserved-'+version)/('TRANSPORT_AMENDMENT_'+version.upper()+'.json')
        return dict(protocol_sha256=self.psha,
                    script_sha256=getattr(a, version.upper()+'_SCRIPT_SHA'), codec_sha256=a.CODEC_SHA,
                    symbol='USATECHIDXUSD', scale=1000, raw_sides=['BID','ASK'], derived_side='BID',
                    start=START, end_exclusive=START+10800, parallel_requests=1,
                    transport_amendment_sha256=a.codec.sha(amendment.read_bytes()),
                    ordinary_attempts_per_hour_per_run=2)

    def seed_v2_v3(self):
        self.target.mkdir()
        old_identity = self.identity('v2')
        a.codec.atomic_json(self.target/'MONTH_IDENTITY.json', old_identity)
        old_raw = (self.target/'MONTH_IDENTITY.json').read_bytes()
        a.codec.atomic_bytes(self.target/'FROZEN_PROTOCOL.json', self.raw)
        for version in ('v2','v3'):
            name = 'TRANSPORT_AMENDMENT_'+version.upper()+'.json'
            a.codec.atomic_bytes(self.target/name, (ROOT/('preserved-'+version)/name).read_bytes())
        lineage = dict(schema='month-acquisition-code-lineage-v3',prior_identity_sha256=a.codec.sha(old_raw),
                       prior_script_sha256=a.V2_SCRIPT_SHA,prior_amendment_sha256=a.V2_AMENDMENT_SHA,
                       current_identity=self.identity('v3'),does_not_retroactively_clear_v2=True)
        a.codec.atomic_json(self.target/'TRANSPORT_IDENTITY_V3.json', lineage)
        for hour, driver in [(START,a.V2_SCRIPT_SHA),(START+3600,a.V3_SCRIPT_SHA)]:
            attempt,body,rows = packet(hour)
            a.persist_receipt(self.target,hour,attempt,body,rows,self.psha,driver)
        return old_raw

    def runit(self, fetcher, limit=10):
        with contextlib.redirect_stdout(io.StringIO()):
            return a.run(self.target,self.p,self.raw,self.psha,START,START+10800,[],limit,
                         fetcher=fetcher,sleeper=lambda _:None,now=lambda:10000,route='direct-https')

    def test_v2_v3_receipts_migrate_and_resume_without_rewriting_prior_identity(self):
        old_raw = self.seed_v2_v3()
        prior = {p.name:p.read_bytes() for p in (self.target/'hours').glob('*.json')}
        calls = []
        def fetch(t):
            calls.append(t)
            x,b,rows = packet(t)
            x.update(a.routing_metadata(a.codec.hour_url(t),'direct-https'))
            return x,b,rows
        m = self.runit(fetch)
        self.assertEqual(calls,[START+7200])
        self.assertTrue(m['all_hours_validated_or_explicit_empty_200'])
        self.assertEqual((self.target/'MONTH_IDENTITY.json').read_bytes(),old_raw)
        for name,data in prior.items():
            self.assertEqual((self.target/'hours'/name).read_bytes(),data)
        self.assertEqual(set(m['receipt_script_sha256_counts']),{a.V2_SCRIPT_SHA,a.V3_SCRIPT_SHA,self.current})
        m2 = self.runit(lambda t:self.fail('Unexpected resumed fetch'))
        self.assertEqual(m2['network_requests_this_run'],0)

    def test_altered_v3_lineage_rejects_before_fetch(self):
        self.seed_v2_v3()
        path = self.target/'TRANSPORT_IDENTITY_V3.json'
        data = json.loads(path.read_bytes());data['prior_identity_sha256']='0'*64
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'Prior V3 lineage'):
            self.runit(lambda t:self.fail('Unexpected fetch with broken lineage'))

    def test_pinned_historical_sources_are_present(self):
        for version in ('v2','v3'):
            data=(ROOT/('preserved-'+version)/('acquire_month_'+version+'.py')).read_bytes()
            self.assertEqual(a.codec.sha(data),getattr(a,version.upper()+'_SCRIPT_SHA'))


if __name__ == '__main__':
    unittest.main()
