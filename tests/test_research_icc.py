import numpy as np
import pandas as pd
import pytest
from astra_research.data import validate_raw, resample, load_csv
from astra_research.icc import signals, signals_from_bars, pivots

def fixture(direction=1):
    t=pd.date_range('2026-01-05 00:00',periods=44,freq='15min',tz='UTC')
    rows=[]
    for i,ts in enumerate(t):
        o,c,h,l=105.,105.,108.,104.
        if i<24:
            h=[107,109,110,108,107,116][i//4]; l=100.
            if i>=20: o,c,h,l=104.,114.,116.,103.
        if i==24:o,c,h,l=110.,108.,111.,107.
        if i==27:l=100.
        if i==30:h=111.
        if i==33:l=103.
        if i==36:o,c,h,l=105.,112.,113.,104.
        for m in range(15):
            # One coherent minute stream drives every higher timeframe.
            oo=o; cc=c if m==14 else o
            hh=h if m==0 else max(oo,cc)
            ll=l if m==0 else min(oo,cc)
            if direction==-1: oo,hh,ll,cc=220-oo,220-ll,220-hh,220-cc
            rows.append((ts+pd.Timedelta(minutes=m),oo,hh,ll,cc,1))
    return validate_raw(pd.DataFrame(rows,columns=['timestamp','open','high','low','close','volume']))

@pytest.mark.parametrize('direction',[1,-1])
def test_positive_complete_sequence_and_every_raw_prefix(direction):
    raw=fixture(direction)
    full=signals(raw)
    assert len(full)==1
    e=full.iloc[0]
    assert e.direction==direction
    assert e.indication_time < e.correction_time < e.structure_known_time < e.decision_time
    assert e.target== (116 if direction==1 else 104)
    assert e.decision_time==pd.Timestamp('2026-01-05 09:15',tz='UTC')
    # Every raw prefix around and before decision: no signal appears too early,
    # and all fields match after the decision, including later added extrema.
    for cutoff in [e.structure_known_time,e.decision_time-pd.Timedelta(minutes=1),e.decision_time,e.decision_time+pd.Timedelta(minutes=1)]:
        prefix=signals(raw[raw.known_time<=cutoff])
        expected=full[full.decision_time<=cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(prefix,expected,check_dtype=False)
        for tf in [15,60]:
            actual=resample(raw[raw.known_time<=cutoff],tf)
            bars=resample(raw,tf)
            pd.testing.assert_frame_equal(actual,bars[bars.timestamp<=cutoff].reset_index(drop=True))

@pytest.mark.parametrize('change,match',[
    ('duplicate','duplicate'),('offtick','off-tick'),('geometry','OHLC'),('volume','volume'),('nonfinite','nonfinite'),('order','chronological')])
def test_data_rejects_defects(change,match):
    d=fixture().iloc[:5].copy()
    if change=='duplicate':d.loc[1,'timestamp']=d.loc[0,'timestamp']
    if change=='offtick':d.loc[0,'high']+=.1
    if change=='geometry':d.loc[0,'high']=1
    if change=='volume':d.loc[0,'volume']=0
    if change=='nonfinite':d.loc[0,'high']=np.inf
    if change=='order':d=d.iloc[::-1]
    with pytest.raises(ValueError,match=match):validate_raw(d)

def test_complete_buckets_only_and_pivot_gap_firewall():
    raw=fixture()
    incomplete=raw.drop(index=400)
    bars=resample(incomplete,15)
    assert len(bars)==len(resample(raw,15))-1
    ps=pivots(bars,15)
    missing_time=raw.timestamp.iloc[400].floor('15min')
    assert all(not (p.window_start<=missing_time<p.known_time) for p in ps.itertuples())
    assert len(resample(raw.iloc[:14],15))==0

def test_strict_ties_and_dual_pivots_are_excluded():
    b=resample(fixture(),15).iloc[:5].copy()
    b['high']=[10,11,12,12,10]; b['low']=[7,7,7,7,7]
    assert pivots(b,15).empty
    b['high']=[10,11,12,11,10]; b['low']=[7,6,5,6,7]
    assert pivots(b,15).empty
    b['low']=7
    p=pivots(b,15)
    assert len(p)==1 and p.iloc[0].known_time==b.timestamp.iloc[4]

def test_pre_c_windows_and_cross_not_just_side():
    raw=fixture(); h=resample(raw,60); l=resample(raw,15)
    assert len(signals_from_bars(h,l))==1
    # A high close on the structure-known bar cannot itself be K; staying above
    # reaction thereafter without crossing cannot manufacture a later K.
    l.loc[35:,'close']=112.; l.loc[35:,'high']=113.
    assert signals_from_bars(h,l).empty
    # C shifted downstream of the first low makes its window inadmissible.
    l=resample(raw,15)
    l.loc[24:28,'open']=104.; l.loc[24:28,'close']=105.
    l.loc[29,'open']=106.; l.loc[29,'close']=105.
    assert signals_from_bars(h,l).empty

def test_expiry_and_new_indication_supersession():
    raw=fixture(); h=resample(raw,60); l=resample(raw,15)
    assert signals_from_bars(h,l,horizon=8).empty
    # Strong opposite break of a confirmed early low on hour7 supersedes I.
    h.loc[:,'low']=[99,98,97,98,99,103,99,96,99,100,100]
    h.loc[7,['open','close','high']]=[106,96,107]
    assert signals_from_bars(h,l).empty

@pytest.mark.parametrize('date',['2026-03-06','2026-03-09'])
def test_four_hour_anchor_uses_et18_across_dst(date):
    start=pd.Timestamp(date+' 18:00',tz='America/New_York').tz_convert('UTC')
    d=pd.DataFrame(dict(timestamp=pd.date_range(start,periods=240,freq='min'),open=100,high=101,low=99,close=100,volume=1))
    b=resample(validate_raw(d),240)
    assert len(b)==1 and b.start_time.iloc[0]==start
    assert b.timestamp.iloc[0]==start+pd.Timedelta(hours=4)

def test_future_append_cannot_replace_first_completed_structure():
    raw=fixture(); full=signals(raw)
    assert len(full)==1
    decision=full.iloc[0].decision_time
    prefix=signals(raw[raw.known_time<=decision])
    pd.testing.assert_frame_equal(full,prefix)
    mutated=raw.copy()
    mask=mutated.timestamp>=decision
    for c in ['open','high','low','close']:mutated.loc[mask,c]+=100
    earlier=signals(mutated)
    pd.testing.assert_frame_equal(earlier[earlier.decision_time<=decision].reset_index(drop=True),full)


def test_load_forward_excludes_frozen_bad_segment_before_validation(tmp_path):
    p=tmp_path/'forward.csv'
    p.write_text('datetime,open,high,low,close,volume\n'
                 '2026-03-04T14:00:00+00:00,100.1,101.1,99.1,100.1,0\n'
                 '2026-03-05T14:00:00+00:00,100,101,99,100,1\n')
    d=load_csv(p,forward=True)
    assert len(d)==1
    assert d.known_time.iloc[0]==pd.Timestamp('2026-03-05 14:01',tz='UTC')


def test_historical_et_localization_and_source_availability(tmp_path):
    p=tmp_path/'historical.csv'
    p.write_text('timestamp ET,open,high,low,close,volume\n'
                 '3/9/2026 09:30,100,101,99,100,1\n')
    d=load_csv(p)
    assert d.timestamp.iloc[0]==pd.Timestamp('2026-03-09 13:30',tz='UTC')
    assert d.known_time.iloc[0]==pd.Timestamp('2026-03-09 13:31',tz='UTC')


def test_pivot_width_sensitivities_and_momentum_filter_do_not_look_forward():
    raw=fixture()
    for width in [1,2,3]:
        full=signals(raw,left=width,right=width)
        for event in full.itertuples():
            prior=signals(raw[raw.known_time<=event.decision_time],left=width,right=width)
            pd.testing.assert_frame_equal(prior,full[full.decision_time<=event.decision_time].reset_index(drop=True))
    assert signals(raw,body_fraction=.99).empty

@pytest.mark.parametrize('width',[1,2,3])
@pytest.mark.parametrize('direction',[1,-1])
def test_verified_cache_equals_uncached_at_every_bar_prefix(width,direction):
    from astra_research.icc import prepare,verify_prepared
    raw=fixture(direction);h=resample(raw,60);l=resample(raw,15)
    cache=verify_prepared(h,l,prepare(h,l,left=width,right=width))
    for cutoff in l.timestamp:
        hh=h[h.timestamp<=cutoff];ll=l[l.timestamp<=cutoff]
        kw=dict(left=width,right=width)
        pd.testing.assert_frame_equal(signals_from_bars(hh,ll,**kw),
                                      signals_from_bars(hh,ll,_prepared=cache,**kw))


def test_cache_rejects_unverified_changed_source_and_corrupt_pivots():
    from dataclasses import replace
    from types import MappingProxyType
    from astra_research.icc import prepare,verify_prepared
    raw=fixture();h=resample(raw,60);l=resample(raw,15)
    cache=prepare(h,l)
    with pytest.raises(ValueError,match='verified'):
        signals_from_bars(h,l,_prepared=cache)
    verified=verify_prepared(h,l,cache)
    changed=l.copy();changed.loc[0,'close']+=.25
    with pytest.raises(ValueError,match='source mismatch'):
        signals_from_bars(h,changed,_prepared=verified)
    with pytest.raises(ValueError,match='source mismatch'):
        verify_prepared(h,changed,cache)
    with pytest.raises(ValueError,match='oracle mismatch'):
        verify_prepared(h,l,replace(cache,lp=MappingProxyType({})))
    with pytest.raises(TypeError):cache.lp[999]='mutation'
