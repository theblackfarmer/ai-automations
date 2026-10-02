"""Frozen timing null; diagnostic only, never a strategy selector."""
import numpy as np
import pandas as pd
from .execution import simulate


def random_entry(raw,trades,pivots,*,kind,seed=2601002,repetitions=100):
    if trades.empty:return {'observed_n':0,'valid_repetitions':0,'p_one_sided':None,'familywise_pass':False}
    rng=np.random.default_rng(seed);times=pd.DatetimeIndex(raw.timestamp)
    local=times.tz_convert('America/New_York');month=local.strftime('%Y-%m')
    eligible=np.ones(len(raw),dtype=bool)
    if kind=='vincent':
        eligible=(local.hour*60+local.minute>=572)&(local.hour*60+local.minute<958)
    pools={m:np.flatnonzero((month==m)&eligible) for m in set(month)}
    groups=trades.assign(month=pd.to_datetime(trades.entry_time,utc=True).dt.tz_convert('America/New_York').dt.strftime('%Y-%m')).groupby('month')
    means=[];counts=[]
    for _ in range(repetitions):
        signals=[]
        for m,g in groups:
            pool=pools.get(m,np.array([],dtype=int))
            if len(pool)<len(g):continue
            chosen=rng.choice(pool,size=len(g),replace=False)
            for idx,t in zip(chosen,g.itertuples()):
                moment=times[idx];price=float(raw.open.iloc[idx]);d=int(t.direction)
                risk=float(t.risk_points);target_distance=d*(t.target-t.entry)
                # Preserve original intended timeout, not outcome-derived holding duration.
                expiry=moment+pd.Timedelta(hours=120 if '4h' in t.strategy else 24)
                if kind=='vincent':expiry=moment.tz_convert('America/New_York').normalize()+pd.Timedelta(hours=16)
                signals.append(dict(strategy='random_entry',direction=d,decision_time=moment,
                    stop=price+d*.25-d*risk,target=price+d*.25+d*target_distance,expiry_time=expiry))
        out,_=simulate(raw,pd.DataFrame(signals),pivots=pivots,instrument='MNQ')
        if len(out):means.append(float(out.net_R.mean()));counts.append(len(out))
    observed=float(trades.net_R.mean())
    p=(1+sum(v>=observed for v in means))/(1+len(means)) if means else None
    return {'observed_n':len(trades),'observed_mean_R':observed,'valid_repetitions':len(means),
       'null_mean_R_interval95':list(map(float,np.quantile(means,[.025,.975]))) if means else None,
       'null_completed_N_range':[min(counts),max(counts)] if counts else None,
       'p_one_sided':p,'familywise_pass':False,'eligible_for_inference':False,
       'inference_limit':'Descriptive randomized benchmark only; variable overlap/censor populations prevent an established exchangeable significance test. No familywise clearance.',
       'note':'Same exit geometry and causal trail, randomized monthly timing. Changed overlap counts are exposed; not an exact conditional permutation.'}


def holm(results,alpha=.05):
    values=sorted([(v['p_one_sided'],k) for k,v in results.items() if v.get('p_one_sided') is not None])
    n=len(results);running=0.;still=True
    for rank,(p,k) in enumerate(values):
        adjusted=min(1.,max(running,(n-rank)*p));running=adjusted
        passed=still and p<=alpha/(n-rank) and results[k].get('eligible_for_inference',False)
        still=passed
        results[k]['holm_adjusted_p']=adjusted;results[k]['familywise_pass']=passed
    return results


def condition_slices(raw,trades):
    if trades.empty:return []
    dates=raw.timestamp.dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d')
    daily=raw.assign(day=dates).groupby('day').agg(open=('open','first'),close=('close','last'),high=('high','max'),low=('low','min'))
    daily['range']=daily.high-daily.low
    daily['prior_up']=(daily.close>daily.open).shift(1)
    daily['prior_range_high']=(daily['range']>daily['range'].rolling(20,min_periods=20).median()).shift(1)
    t=trades.copy();t['day']=pd.to_datetime(t.entry_time,utc=True).dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d')
    t=t.join(daily[['prior_up','prior_range_high']],on='day')
    out=[]
    for col in ['prior_up','prior_range_high']:
        for label,g in t.groupby(col,dropna=False):out.append({'diagnostic':col,'label':str(label),'n':len(g),'mean_R':float(g.net_R.mean())})
    return out
