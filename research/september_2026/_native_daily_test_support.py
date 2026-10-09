"""Independent offline attacks. No network and no project edits."""
import contextlib
import copy
import csv
import io
import json
import lzma
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import acquire_native_daily_v2 as a

# Adoption contracts use their own frozen synthetic model identity. A repair to
# the market engine must not silently retarget a historical acquisition protocol.
# The production amendment remains untouched; mismatch rejection is tested below.
_TEST_MODEL=tempfile.TemporaryDirectory(prefix='native-test-model-')
PROJECT=Path(_TEST_MODEL.name)
(PROJECT/'engine.py').write_text('# Explicit synthetic model identity, never executed.\n')
(PROJECT/'SPEC.json').write_text('{"synthetic_fixture": true}\n')
_fixture_protocol=json.loads(Path(__file__).with_name('NATIVE_DAILY_AMENDMENT.json').read_text())
_fixture_protocol.update(synthetic_fixture=True,engine_sha256=a.digest((PROJECT/'engine.py').read_bytes()),spec_sha256=a.digest((PROJECT/'SPEC.json').read_bytes()))
AMENDMENT=PROJECT/'SYNTHETIC_AMENDMENT.json'
AMENDMENT.write_text(json.dumps(_fixture_protocol,sort_keys=True)+'\n')
DAY=a.epoch('2026-09-01T00:00:00Z')

def body(records):return lzma.compress(b''.join(struct.pack('>IIIIIf',*r) for r in records))
GOOD=body([(0,100000,101000,99000,102000,1.0)])

def fixture(base,receipt_edit=None,reference_day=DAY):
    target=base/'run';(target/'days').mkdir(parents=True);(target/'blobs').mkdir()
    refcsv=base/'reference.csv';refcsv.write_text('epoch,open,high,low,close\n'+str(reference_day)+',100,102,99,101\n')
    ref=base/'reference.json';ref.write_text(json.dumps(dict(csv=dict(path='reference.csv',sha256=a.digest(refcsv.read_bytes()),bytes=refcsv.stat().st_size),feed='Dukascopy USATECHIDXUSD',side='BID',minute_count=1)))
    protocol=json.loads(AMENDMENT.read_text());start=a.epoch(protocol['start_utc']);end=a.epoch(protocol['end_exclusive_utc'])
    for day in range(start,end,86400):
        data=GOOD if day==DAY else b'';h=a.digest(data);(target/'blobs'/(h+'.bi5')).write_bytes(data)
        r=dict(state='OK',status=200,requested_url=a.url(day),final_url=a.url(day),day=a.clocks(day),body_sha256=h,body_bytes=len(data),content_length=str(len(data)),observed_minutes=int(day==DAY),excluded_zero_volume_placeholders=0,amendment_sha256=a.digest(AMENDMENT.read_bytes()),script_sha256=a.digest(Path(a.__file__).read_bytes()))
        if receipt_edit:receipt_edit(day,r)
        (target/'days'/(str(day)+'.json')).write_text(json.dumps(r))
    return target,ref

def offline_run(target,ref):
    with patch.object(a,'ROOT',PROJECT),patch.object(a,'COOLDOWN',target/'cooldown.json'),patch.object(a,'fetch',side_effect=AssertionError('NETWORK MUST NOT RUN')),contextlib.redirect_stdout(io.StringIO()):
        return a.run(target,AMENDMENT,ref)

class DecoderControls(unittest.TestCase):
    def test_valid_and_zero_volume_filler(self):
        rows,excluded=a.decode(body([(0,100,110,90,120,1),(60,110,110,110,110,0),(120,110,110,110,110,2)]),DAY)
        self.assertEqual(rows,{DAY:(100,120,90,110),DAY+120:(110,110,110,110)})
        self.assertEqual(excluded,[DAY+60])

    def test_invalid_records_rejected(self):
        cases=[[(1,100,110,90,120,1)],[(86400,100,110,90,120,1)],
               [(60,100,110,90,120,1),(0,100,110,90,120,1)],
               [(0,100,110,90,120,1),(0,100,110,90,120,1)],
               [(0,100,110,90,120,float('nan'))],[(0,100,110,90,120,-1)],
               [(0,0,110,0,120,1)],[(0,100,110,105,120,1)],
               [(0,100,110,90,105,1)],[(0,100,110,90,120,0)]]
        for records in cases:
            with self.subTest(records=records),self.assertRaises(ValueError):a.decode(body(records),DAY)

    def test_compression_truncation_trailer_and_partial_record(self):
        for data in [GOOD[:-1],GOOD+b'junk',GOOD+lzma.compress(b''),lzma.compress(b'bad')]:
            with self.subTest(case=len(data)),self.assertRaises((ValueError,lzma.LZMAError)):a.decode(data,DAY)

    def test_actual_sep1_saved_body(self):
        p=Path(__file__).resolve().parent/'fixtures'/'sep1-native'
        receipt=json.loads((p/'RECEIPT.json').read_text());data=(p/(receipt['body_sha256']+'.bi5')).read_bytes()
        self.assertEqual(a.digest(data),receipt['body_sha256'])
        rows,excluded=a.decode(data,DAY)
        self.assertEqual(len(rows),1335);self.assertEqual(len(excluded),105)
        self.assertFalse(set(rows)&set(excluded))

class AdoptionAttacks(unittest.TestCase):
    def test_honest_cached_fixture_control(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d));m=offline_run(target,ref)
            self.assertTrue(m['all_hours_validated_or_explicit_empty_200']);self.assertEqual(m['network_requests'],0)

    def test_bad_http_status_must_block_cache_adoption(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d),lambda day,r:r.update(status=404))
            with self.assertRaises(ValueError):offline_run(target,ref)

    def test_wrong_final_url_must_block_cache_adoption(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d),lambda day,r:r.update(final_url='https://example.invalid/ASK'))
            with self.assertRaises(ValueError):offline_run(target,ref)

    def test_wrong_cached_model_identity_must_block(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d),lambda day,r:r.update(script_sha256='0'*64,amendment_sha256='1'*64))
            with self.assertRaises(ValueError):offline_run(target,ref)

    def test_wrong_cached_body_lengths_must_block(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d),lambda day,r:r.update(body_bytes=999,content_length='999'))
            with self.assertRaises(ValueError):offline_run(target,ref)

    def test_reference_outside_study_must_not_vacuously_adopt(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d),reference_day=a.epoch('2025-09-01T00:00:00Z'))
            with self.assertRaises(ValueError):offline_run(target,ref)

    def test_body_hash_and_reference_hash_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d));(target/'blobs'/(a.digest(GOOD)+'.bi5')).write_bytes(GOOD+b'bad')
            with self.assertRaises(ValueError):offline_run(target,ref)
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d));(Path(d)/'reference.csv').write_text('changed')
            with self.assertRaises(ValueError):offline_run(target,ref)

    def test_reference_price_mismatch_blocks_csv(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d));p=Path(d)/'reference.csv';p.write_text(p.read_text().replace(',101\n',',100\n'))
            r=json.loads(ref.read_text());r['csv'].update(sha256=a.digest(p.read_bytes()),bytes=p.stat().st_size);ref.write_text(json.dumps(r))
            m=offline_run(target,ref)
            self.assertFalse(m['all_hours_validated_or_explicit_empty_200']);self.assertFalse((target/'native-bid-1m.csv').exists())

    def test_shared429_arriving_during_pacing_must_prevent_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            target,ref=fixture(Path(d));first=min((target/'days').glob('*.json'));first.unlink()
            cooldown=target/'cooldown.json';calls=[]
            def during_sleep(_):cooldown.write_text(json.dumps({'retry_not_before_epoch':a.time.time()+3600}))
            def forbidden_fetch(day):
                calls.append(day)
                return dict(state='RATE_LIMITED',status=429,retry_not_before_epoch=a.time.time()+3600),None
            with patch.object(a,'ROOT',PROJECT),patch.object(a,'COOLDOWN',cooldown),patch.object(a.time,'sleep',side_effect=during_sleep),patch.object(a,'fetch',side_effect=forbidden_fetch),contextlib.redirect_stdout(io.StringIO()):
                a.run(target,AMENDMENT,ref)
            self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
