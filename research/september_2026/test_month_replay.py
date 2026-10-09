import csv
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import month_replay as m


def candles(start, end):
    return {t:dict(time=t,open=100.,high=102.,low=99.,close=101.)
            for t in range(start,end,60) if m.session(t) is True}


class CoverageTests(unittest.TestCase):
    def test_regular_break_both_edges(self):
        self.assertTrue(m.session(m.epoch('2026-09-08T20:14:00Z')))
        self.assertFalse(m.session(m.epoch('2026-09-08T20:15:00Z')))
        self.assertFalse(m.session(m.epoch('2026-09-08T21:59:00Z')))
        self.assertTrue(m.session(m.epoch('2026-09-08T22:00:00Z')))

    def test_weekend_and_unknown_holiday(self):
        self.assertFalse(m.session(m.epoch('2026-09-05T14:00:00Z')))
        self.assertFalse(m.session(m.epoch('2026-09-06T21:59:00Z')))
        self.assertTrue(m.session(m.epoch('2026-09-06T22:00:00Z')))
        self.assertIsNone(m.session(m.epoch('2026-09-07T14:00:00Z')))

    def test_known_scheduled_partial_bucket_is_not_filled(self):
        start=m.epoch('2026-09-08T20:00:00Z');end=start+14400
        rows=candles(start,end); plan,coverage=m.assemble(rows,start,end,'sha')
        h4=[b for _,_,bs in plan for tf,b in bs if tf==240]
        self.assertEqual(len(h4),1);self.assertEqual(h4[0]['observed_minutes'],135)
        self.assertEqual(h4[0]['known_at'],end)
        self.assertEqual(len(h4[0]['member_times']),135)
        gaps=[g for _,gs,_ in plan for g in gs]
        self.assertEqual(len(gaps),1)
        self.assertEqual(gaps[0]['kind'],'SCHEDULED_COVERAGE_BRIDGE')
        self.assertEqual(gaps[0]['end_exclusive'],m.epoch('2026-09-08T22:00:00Z'))

    def test_one_missing_minute_blocks_higher_bucket(self):
        start=m.epoch('2026-09-08T00:00:00Z');end=start+14400
        rows=candles(start,end);del rows[start+600]
        plan,coverage=m.assemble(rows,start,end,'sha')
        self.assertFalse(any(tf==240 for _,_,bs in plan for tf,b in bs))
        self.assertTrue(any(k==start+660 and g for k,g,_ in plan))

    def test_closure_quote_is_not_silently_dropped(self):
        start=m.epoch('2026-09-08T20:00:00Z');end=start+14400
        rows=candles(start,end);t=start+1800;rows[t]=dict(time=t,open=100,high=101,low=99,close=100)
        plan,coverage=m.assemble(rows,start,end,'sha')
        self.assertTrue(any('QUOTE_DURING_DECLARED_CLOSURE' in g for _,g,_ in plan))
        self.assertTrue(any(c['timeframe']==240 and c['status']=='SCHEDULE_CONTRADICTION' for c in coverage))

    def test_aggregate_members_and_ohlc(self):
        start=m.epoch('2026-09-08T00:00:00Z');end=start+14400
        rows=candles(start,end);rows[start+120]['high']=105.;rows[end-60]['close']=100.5
        plan,_=m.assemble(rows,start,end,'sha')
        b=[b for _,_,bs in plan for tf,b in bs if tf==240][0]
        self.assertEqual((b['open'],b['high'],b['low'],b['close']),(100,105,99,100.5))
        self.assertEqual(b['member_times'],list(range(start,end,60)))

    def test_future_suffix_cannot_change_completed_inputs(self):
        start=m.epoch('2026-09-08T00:00:00Z');cut=start+14400;end=cut+14400
        rows=candles(start,end)
        full,_=m.assemble(rows,start,end,'sha');prefix,_=m.assemble({t:b for t,b in rows.items() if t<cut},start,cut,'sha')
        self.assertEqual([r for r in full if r[0]<=cut],prefix)
        for t in list(rows):
            if t>=cut:rows[t]=dict(time=t,open=500,high=501,low=499,close=500)
        mutated,_=m.assemble(rows,start,end,'sha')
        self.assertEqual([r for r in mutated if r[0]<=cut],prefix)

    def test_hierarchy_feed_order(self):
        start=m.epoch('2026-09-08T00:00:00Z');end=start+14400
        plan,_=m.assemble(candles(start,end),start,end,'sha')
        self.assertEqual([tf for tf,_ in plan[-1][2]],[240,60,1])

    def test_bad_source_rows_rejected(self):
        for text in ['epoch,open,high,low,close\n60,100,99,98,100\n',
                     'epoch,open,high,low,close\n60,100,101,99,100\n60,100,101,99,100\n',
                     'epoch,open,high,low,close\n61,100,101,99,100\n',
                     'epoch,open,high,low,close\n60,nan,101,99,100\n']:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'bars.csv';p.write_text(text)
                with self.assertRaises(ValueError):m.read_csv(p)



class IntegrityTests(unittest.TestCase):
    def plan(self):
        start=m.epoch('2026-09-08T00:00:00Z');end=start+14400
        return m.assemble(candles(start,end),start,end,'synthetic')[0]

    def test_reject_future_and_duplicate_members(self):
        for mutation in ('future','duplicate','absent','wrong_ohlc','wrong_source'):
            plan=self.plan();b=plan[-1][2][0][1]
            if mutation=='future':b['member_times']=[b['known_at']+60]
            elif mutation=='duplicate':b['member_times'][-1]=b['member_times'][0]
            elif mutation=='absent':b['member_times'].pop()
            elif mutation=='wrong_ohlc':b['high']+=1
            else:b['source_id']='different:240:'+str(b['time'])
            with self.assertRaises(ValueError):m.validate_plan(plan)

    def test_unclassified_gap_cannot_be_bridged(self):
        plan=self.plan();known=plan[1][0]
        plan[1][1].append(dict(kind='SCHEDULED_COVERAGE_BRIDGE',id=m.CALENDAR_ID,url=m.CALENDAR_URL,start=known-60,end_exclusive=known))
        with self.assertRaises(ValueError):m.validate_plan(plan)

    def test_csv_snapshot_not_mutated_by_path_change(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bars.csv';p.write_text('epoch,open,high,low,close\n60,100,101,99,100\n')
            captured=p.read_bytes();p.write_text('epoch,open,high,low,close\n60,200,201,199,200\n')
            self.assertEqual(m.parse_csv(captured)[60]['open'],100)
            self.assertEqual(m.read_csv(p)[60]['open'],200)

    def binding(self,d):
        scope=dict(symbol='NAS100 CFD',provider='Dukascopy USATECHIDXUSD',side='BID',warmup_start='2026-09-08T00:00:00Z',end_exclusive='2026-09-08T04:00:00Z')
        sp=json.dumps(dict(scope=scope)).encode();data=b'captured'
        acq=dict(symbol=scope['symbol'],feed=scope['provider'],side='BID',start={'utc':scope['warmup_start']},end_exclusive={'utc':scope['end_exclusive']},csv=dict(path='source.csv',bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),incomplete=False),all_hours_validated_or_explicit_empty_200=True,expected_hours=4,processed_hours=4,hour_states={'OK':4})
        clar={'spec_sha256':hashlib.sha256(sp).hexdigest()}
        return dict(spec=sp,source=data,acquisition=json.dumps(acq).encode(),clarifications=json.dumps(clar).encode()),dict(source=str(Path(d)/'source.csv'),acquisition=str(Path(d)/'receipt.json'))

    def test_exact_receipt_and_spec_bindings(self):
        with tempfile.TemporaryDirectory() as d:
            captured,paths=self.binding(d);m.validate_bindings(captured,paths)
            for key in ('digest','path','bytes','symbol','feed','side','window','falsefull','spec'):
                c=copy.deepcopy(captured);a=json.loads(c['acquisition'])
                if key=='digest':a['csv']['sha256']='0'*64
                elif key=='path':a['csv']['path']='other.csv'
                elif key=='bytes':a['csv']['bytes']=0
                elif key in ('symbol','feed','side'):a[key]='wrong'
                elif key=='window':a['start']['utc']='2026-09-07T00:00:00Z'
                elif key=='falsefull':a['hour_states']={'OK':3,'TIMEOUT':1}
                else:c['clarifications']=b'{"spec_sha256":"wrong"}'
                c['acquisition']=json.dumps(a).encode()
                with self.assertRaises(ValueError):m.validate_bindings(c,paths)

    def test_event_and_input_disk_corruption_rejected(self):
        cls,_=m.load_engine(Path(__file__).with_name('engine.py'))
        spec=json.loads(Path(__file__).with_name('SPEC.json').read_text())
        for name in ('events.jsonl','input-audit.jsonl'):
            with tempfile.TemporaryDirectory() as d:
                out=Path(d);plan=self.plan();events,snap,head=m.replay(cls,spec,plan,out)
                m.verify_saved(out,events,snap,head,plan)
                target=out/name;lines=target.read_text().splitlines()
                if name=='events.jsonl':target.write_text('{"id":"invented"}\n')
                else:target.write_text('\n'.join(lines[1:])+'\n')
                with self.assertRaises(ValueError):m.verify_saved(out,events,snap,head,plan)

    def test_reopening_updates_all_owners_without_synthetic_candles(self):
        cls,_=m.load_engine(Path(__file__).with_name('engine.py'))
        spec=json.loads(Path(__file__).with_name('SPEC.json').read_text())
        start=m.epoch('2026-09-08T20:00:00Z');end=start+14400;reopen=start+7200
        plan,_=m.assemble(candles(start,end),start,end,'synthetic')
        events,state,_=m.replay(cls,spec,[r for r in plan if r[0]<=reopen])
        self.assertTrue(all(state['coverage_through'][tf]>=reopen for tf in m.TF))
        bridges=[e for e in events if e['kind']=='SCHEDULED_COVERAGE_BRIDGE']
        self.assertEqual(bridges[-1]['timeframes'],list(m.TF))
        self.assertFalse(any(b['time']>=start+900 and b['time']<reopen for _,_,bars in plan for tf,b in bars if tf==1))

    def test_one_shot_plan_replays_identically(self):
        cls,_=m.load_engine(Path(__file__).with_name('engine.py'))
        spec=json.loads(Path(__file__).with_name('SPEC.json').read_text())
        plan=self.plan()
        self.assertEqual(m.replay(cls,spec,plan),m.replay(cls,spec,iter(plan)))

    def test_negative_fractional_boolean_counts_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c,paths=self.binding(d)
            for states in ({'OK':5,'EMPTY_200':-1},{'OK':3.5,'EMPTY_200':0.5},{'OK':True,'EMPTY_200':3}):
                a=json.loads(c['acquisition']);a['hour_states']=states
                bad=dict(c,acquisition=json.dumps(a).encode())
                with self.assertRaises(ValueError):m.validate_bindings(bad,paths)

if __name__=='__main__':unittest.main()
