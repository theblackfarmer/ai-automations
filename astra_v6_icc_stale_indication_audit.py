#!/usr/bin/env python3
"""ICC V2 stale-indication / indication-age audit."""
from __future__ import annotations
import io, urllib.request
import pandas as pd
from astra_v6_icc_event_engine_v2 import build_icc_events, causal_pivots

URL="https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv"

def load():
    req=urllib.request.Request(URL,headers={"User-Agent":"Astra6-ICC-V2-Stale/1.0"})
    with urllib.request.urlopen(req,timeout=180) as r: raw=r.read()
    d=pd.read_csv(io.BytesIO(raw))
    d.columns=["timestamp","open","high","low","close","volume","vwap_rth","vwap_eth"]
    d.timestamp=pd.to_datetime(d.timestamp)
    for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
    return d.dropna(subset=["open","high","low","close"]).sort_values("timestamp").reset_index(drop=True)

def rs(d,rule):
    return d.set_index("timestamp").resample(rule,label="left",closed="left").agg(
        open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum")
    ).dropna(subset=["open","high","low","close"]).reset_index()

def main():
    b=load(); h=rs(b,"1h"); m15=rs(b,"15min"); m5=rs(b,"5min")
    e=build_icc_events(h,m15,m5)
    e["indication_age_hours"]=(e.indication_time-e.source_pivot_time).dt.total_seconds()/3600
    e["pivot_known_to_I_hours"]=(e.indication_time-e.source_pivot_known_time).dt.total_seconds()/3600
    e["C_after_I_hours"]=(e.correction_start_time-e.indication_time).dt.total_seconds()/3600
    # Competing known pivots: newer same-kind HTF pivots known before I and more recent than source.
    highs,lows=causal_pivots(h)
    piv=pd.DataFrame([{"time":p.time,"known_time":p.known_time,"price":p.price,"kind":p.kind} for p in highs+lows])
    rows=[]
    for _,x in e.iterrows():
        kind="high" if x.direction=="long" else "low"
        prior=piv[(piv.kind==kind)&(piv.known_time<=x.indication_time)&(piv.time>x.source_pivot_time)]
        rows.append({
            "newer_same_kind_known_before_I":len(prior)>0,
            "newer_same_kind_count":len(prior),
            "newest_same_kind_pivot_time":prior.time.max() if len(prior) else pd.NaT,
            "newest_same_kind_pivot_age_hours":((x.indication_time-prior.time.max()).total_seconds()/3600) if len(prior) else None
        })
    e=pd.concat([e.reset_index(drop=True),pd.DataFrame(rows)],axis=1)
    bins=[-1,24,72,168,336,720,1440,999999]
    labels=["<=1d","1-3d","3-7d","7-14d","14-30d","30-60d",">60d"]
    e["age_bucket"]=pd.cut(e.indication_age_hours,bins=bins,labels=labels)
    summary=pd.DataFrame([{
        "events":len(e),"long":int(e.direction.eq("long").sum()),"short":int(e.direction.eq("short").sum()),
        "median_age_hours":e.indication_age_hours.median(),"mean_age_hours":e.indication_age_hours.mean(),
        "max_age_hours":e.indication_age_hours.max(),
        "over_7d":int((e.indication_age_hours>168).sum()),
        "over_14d":int((e.indication_age_hours>336).sum()),
        "over_30d":int((e.indication_age_hours>720).sum()),
        "over_60d":int((e.indication_age_hours>1440).sum()),
        "with_newer_same_kind_pivot_known_before_I":int(e.newer_same_kind_known_before_I.sum()),
        "newer_same_kind_median_count":e.newer_same_kind_count.median()
    }])
    dist=e.groupby(["direction","age_bucket"],observed=False).size().reset_index(name="events")
    tail=e.sort_values("indication_age_hours",ascending=False).head(30)
    summary.to_csv("astra6_icc_v2_stale_summary.csv",index=False)
    dist.to_csv("astra6_icc_v2_stale_age_distribution.csv",index=False)
    tail.to_csv("astra6_icc_v2_stale_top30.csv",index=False)
    e.to_csv("astra6_icc_v2_stale_event_detail.csv",index=False)
    print(summary.to_string(index=False)); print(dist.to_string(index=False)); print(tail[["direction","source_pivot_time","indication_time","indication_age_hours","newer_same_kind_count","newest_same_kind_pivot_time"]].to_string(index=False))
    print("STALE AUDIT COMPLETE: diagnostic only; no engine changes.")
if __name__=="__main__": main()
