"""All-candidate prefix audit with independent raw-bucket and leaking-detector controls."""
import numpy as np
import pandas as pd
from . import data,icc,vincent


def equal_rows(a,b):
    if list(a.columns)!=list(b.columns) or len(a)!=len(b):return False
    try:
        pd.testing.assert_frame_equal(a.reset_index(drop=True),b.reset_index(drop=True),check_dtype=False,check_exact=True)
        return True
    except AssertionError:return False


def validate_cache(raw,bars,minutes):
    """Independently reconstruct EVERY cached OHLCV bucket from raw row windows.

    Unlike the production groupby aggregator, this uses fixed trailing row windows
    and verifies endpoints/count/spacing. No earlier cached bucket is trusted.
    """
    if raw.empty:
        if len(bars):raise AssertionError('buckets exist without raw data')
        return 0
    t=pd.DatetimeIndex(raw.timestamp).asi8
    if minutes==240:
        local=raw.timestamp.dt.tz_convert('America/New_York')
        days=pd.date_range(local.iloc[0].date()-pd.Timedelta(days=1),local.iloc[-1].date(),freq='D')
        anchors=[pd.Timestamp(str(day.date())+' 18:00',tz='America/New_York').tz_convert('UTC') for day in list(days)+[days[-1]+pd.Timedelta(days=1)]]
        grid=pd.DatetimeIndex([anchor+pd.Timedelta(hours=4*k) for anchor,nxt in zip(anchors,anchors[1:]) for k in range(7) if anchor+pd.Timedelta(hours=4*(k+1))<=nxt]).asi8
    else:
        grid=pd.date_range(raw.timestamp.iloc[0].floor(f'{minutes}min'),raw.timestamp.iloc[-1],freq=f'{minutes}min').asi8
    gi=np.searchsorted(t,grid);valid=(gi+minutes-1<len(t))
    gi=gi[valid];grid=grid[valid]
    good=(t[gi]==grid)&(t[gi+minutes-1]==grid+(minutes-1)*60_000_000_000)
    expected_starts=np.unique(grid[good])
    if not np.array_equal(pd.DatetimeIndex(bars.start_time).asi8,expected_starts):raise AssertionError('cache bucket membership differs from raw complete intervals')
    if bars.empty:return 0
    starts=pd.DatetimeIndex(bars.start_time).asi8
    idx=np.searchsorted(t,starts);end=idx+minutes-1
    if (end>=len(t)).any() or not np.array_equal(t[idx],starts):raise AssertionError('invalid cached bucket starts')
    expected_close=starts+minutes*60_000_000_000
    if not np.array_equal(pd.DatetimeIndex(bars.timestamp).asi8,expected_close):raise AssertionError('cached close-known time incorrect')
    if not np.array_equal(pd.DatetimeIndex(bars.known_time).asi8,expected_close):raise AssertionError('cached availability incorrect')
    if not np.array_equal(t[end],expected_close-60_000_000_000):raise AssertionError('incomplete raw bucket')
    # Monotonic minute-aligned unique raw data plus count and endpoints establishes no gaps inside.
    expected={'open':raw.open.to_numpy()[idx],'close':raw.close.to_numpy()[end],
      'high':raw.high.rolling(minutes).max().to_numpy()[end],
      'low':raw.low.rolling(minutes).min().to_numpy()[end],
      'volume':raw.volume.rolling(minutes).sum().to_numpy()[end]}
    for c,v in expected.items():
        if not np.array_equal(bars[c].to_numpy(),v):raise AssertionError('raw/cache mismatch '+c)
    return len(bars)


def detect(raw,bars,kind,params):
    if kind=='icc':
        key=('prepared',params['htf_minutes'],params['ltf_minutes'],params.get('left',2),params.get('right',2))
        return icc.signals_from_bars(bars[params['htf_minutes']],bars[params['ltf_minutes']],_prepared=bars.get(key),**params)
    return vincent.signals_from_bars(raw,bars[params['ltf_minutes']],**params)


def leaking_detector(raw,bars,kind,params):
    """Deliberately wrong engine: future whole-input length changes entry stop."""
    out=detect(raw,bars,kind,params).copy()
    if len(out):out['stop']=out.stop+len(raw)*.25
    return out


def compare_prefixes(raw,events,kind,params,cache,detector=detect,first_only=False):
    tested=0;failures=[]
    times=sorted(pd.to_datetime(events.decision_time,utc=True).unique()) if len(events) else []
    if first_only:times=times[:1]
    for t in times:
        t=pd.Timestamp(t);rp=raw[raw.known_time<=t]
        bp={tf:b[b.timestamp<=t] for tf,b in cache.items() if isinstance(tf,int)}
        bp.update({tf:b for tf,b in cache.items() if isinstance(tf,tuple)})
        expected=events[events.decision_time<=t]
        if kind=='vincent':
            # Session-reset detector: reconstruct complete predecessor/current-day state.
            day=t.tz_convert('America/New_York').normalize();start=day-pd.Timedelta(days=4)
            rp=rp[rp.timestamp>=start];bp={tf:b[b.timestamp>=start] for tf,b in bp.items() if isinstance(tf,int)}
            expected=expected[expected.decision_time>=day]
        rerun=detector(rp,bp,kind,params)
        if kind=='vincent':rerun=rerun[rerun.decision_time>=day]
        tested+=int((expected.decision_time==t).sum())
        if not equal_rows(expected,rerun):failures.append({'decision_time':str(t),'expected_rows':len(expected),'prefix_rows':len(rerun)})
    return tested,failures


def audit(raw,events,kind,params,*,cache=None):
    cache={} if cache is None else cache
    timeframes=[params['htf_minutes'],params['ltf_minutes']] if kind=='icc' else [params['ltf_minutes']]
    bucket_count=0;preprocess_positive=False
    for tf in timeframes:
        if tf not in cache:cache[tf]=data.resample(raw,tf)
        bucket_count+=validate_cache(raw,cache[tf],tf)
        if len(cache[tf])>1:
            broken=cache[tf].copy();broken.loc[broken.index[0],'high']+=.25
            try:validate_cache(raw,broken,tf)
            except AssertionError:preprocess_positive=True
            try:validate_cache(raw,cache[tf].iloc[1:],tf)
            except AssertionError:pass
            else:raise AssertionError('preprocessing audit blind to dropped bucket')
    if kind=='icc':
        key=('prepared',params['htf_minutes'],params['ltf_minutes'],params.get('left',2),params.get('right',2))
        if key not in cache:
            h,l=cache[params['htf_minutes']],cache[params['ltf_minutes']]
            prep=icc.prepare(h,l,htf_minutes=params['htf_minutes'],ltf_minutes=params['ltf_minutes'],left=params.get('left',2),right=params.get('right',2))
            cache[key]=icc.verify_prepared(h,l,prep)
    tested,failures=compare_prefixes(raw,events,kind,params,cache)
    positive=False
    if len(events):
        broken=leaking_detector(raw,cache,kind,params)
        _,caught=compare_prefixes(raw,broken,kind,params,cache,detector=leaking_detector,first_only=True)
        positive=bool(caught)
    return {'events':len(events),'events_prefix_compared':tested,'failed_prefixes':failures,
       'positive_control_detected':positive,'preprocessing_positive_control_detected':preprocess_positive,
       'all_raw_buckets_independently_reconstructed':bucket_count,
       'method':'every candidate prefix through same detector; independently reconstruct every cached bucket from raw trailing windows; detector and preprocessing planted leaks',
       'pass':not failures and tested==len(events) and (positive or len(events)==0) and (preprocess_positive or not bucket_count),
       'empty_population':not len(events)}
