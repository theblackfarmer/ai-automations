import pandas as pd
import pytest
from astra_research import data,causality

def test_preprocessing_control_checks_earlier_buckets_not_only_last():
    raw=pd.DataFrame(dict(timestamp=pd.date_range('2026-01-01',periods=20,freq='min',tz='UTC'),open=10.,high=11.,low=9.,close=10.,volume=1))
    raw=data.validate_raw(raw);b=data.resample(raw,5)
    assert causality.validate_cache(raw,b,5)==4
    b.loc[0,'high']+=.25
    with pytest.raises(AssertionError,match='raw/cache mismatch'):causality.validate_cache(raw,b,5)

def test_real_leaking_detector_flows_through_prefix_audit(monkeypatch):
    raw=pd.DataFrame(dict(timestamp=pd.date_range('2026-01-01',periods=20,freq='min',tz='UTC'),open=10.,high=11.,low=9.,close=10.,volume=1))
    raw=data.validate_raw(raw);b=data.resample(raw,5);t=b.timestamp.iloc[1]
    def fixture_engine(h,l,**kwargs):
        if not (l.timestamp==t).any():return pd.DataFrame(columns=['decision_time','stop'])
        return pd.DataFrame([dict(decision_time=t,stop=9.)])
    monkeypatch.setattr(causality.icc,'signals_from_bars',fixture_engine)
    p={'htf_minutes':5,'ltf_minutes':5};cache={5:b}
    correct=causality.detect(raw,cache,'icc',p)
    assert causality.compare_prefixes(raw,correct,'icc',p,cache)[1]==[]
    broken=causality.leaking_detector(raw,cache,'icc',p)
    assert causality.compare_prefixes(raw,broken,'icc',p,cache,causality.leaking_detector)[1]

def test_preprocessing_control_detects_deleted_bucket():
    raw=pd.DataFrame(dict(timestamp=pd.date_range('2026-01-01',periods=20,freq='min',tz='UTC'),open=10.,high=11.,low=9.,close=10.,volume=1))
    raw=data.validate_raw(raw);b=data.resample(raw,5)
    with pytest.raises(AssertionError,match='membership'):causality.validate_cache(raw,b.iloc[1:],5)


def test_vincent_prefix_ignores_other_engines_prepared_cache(monkeypatch):
    raw=pd.DataFrame(dict(timestamp=pd.date_range('2026-01-01 15:00',periods=20,freq='min',tz='UTC'),open=10.,high=11.,low=9.,close=10.,volume=1))
    raw=data.validate_raw(raw);b=data.resample(raw,2);t=b.timestamp.iloc[1]
    def engine(raw,bars,**kwargs):
        return pd.DataFrame([dict(decision_time=t,stop=9.)])
    monkeypatch.setattr(causality.vincent,'signals_from_bars',engine)
    events=engine(raw,b)
    assert causality.compare_prefixes(raw,events,'vincent',{'ltf_minutes':2},{2:b,('prepared',60,15):object()})==(1,[])


@pytest.mark.parametrize('day',['2026-03-07','2026-10-31'])
def test_independent_four_hour_membership_across_dst(day):
    raw=pd.DataFrame(dict(timestamp=pd.date_range(day,periods=4*24*60,freq='min',tz='UTC'),open=10.,high=11.,low=9.,close=10.,volume=1))
    raw=data.validate_raw(raw);bars=data.resample(raw,240)
    assert causality.validate_cache(raw,bars,240)==len(bars)
