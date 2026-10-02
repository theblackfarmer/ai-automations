#!/usr/bin/env python3
"""ICC V2 competing-pivot audit. Diagnostic only; never changes the frozen engine/population."""
from __future__ import annotations
import io, urllib.request
import pandas as pd
from astra_v6_icc_event_engine_v2 import build_icc_events, causal_pivots, _first_break_after

URL="https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv"

def load():
    req=urllib.request.Request(URL,headers={"User-Agent":"Astra6-ICC-V2-Competing/1.0"})
    with urllib.request.urlopen(req,timeout=180) as r: raw=r.read()
    d=pd.read_csv(io.BytesIO(raw)); d.columns=["timestamp","open","high","low","close","volume","vwap_rth","vwap_eth"]
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
    highs,lows=causal_pivots(h)
    piv=[]
    for p in highs+lows:
        kind="high" if p in highs else "low"
        direction="long" if kind=="high" else "short"
        start=max(p.idx+1,int(h.timestamp.searchsorted(p.known_time,side="left")))
        bi=_first_break_after(h,start,direction=direction,level=p.price)
        piv.append({"pivot_time":p.time,"known_time":p.known_time,"price":p.price,"kind":kind,
                    "first_break_time":h.at[bi,"timestamp"] if bi is not None else pd.NaT})
    p=pd.DataFrame(piv)
    out=[]
    for _,x in e.iterrows():
        kind="high" if x.direction=="long" else "low"
        q=p[(p.kind==kind)&(p.pivot_time>x.source_pivot_time)&(p.known_time<=x.indication_time)].copy()
        q["same_price"]=q.price.eq(x.indication_level)
        q["active_at_I"]=q.first_break_time.isna() | (q.first_break_time>=x.indication_time)
        q["breaks_at_I"]=q.first_break_time.eq(x.indication_time)
        active=q[q.active_at_I]
        at_i=q[q.breaks_at_I]
        same_price=q[q.same_price]
        active_same_price=active[active.same_price]
        out.append({
            "newer_known_count":len(q),
            "newer_active_at_I_count":len(active),
            "newer_breaking_at_I_count":len(at_i),
            "newer_same_price_count":len(same_price),
            "newer_active_same_price_count":len(active_same_price),
            "newest_active_pivot_time":active.pivot_time.max() if len(active) else pd.NaT,
            "newest_active_pivot_price":active.loc[active.pivot_time.idxmax(),"price"] if len(active) else None,
        })
    d=pd.concat([e.reset_index(drop=True),pd.DataFrame(out)],axis=1)
    summary=pd.DataFrame([{
        "events":len(d),"long":int(d.direction.eq("long").sum()),"short":int(d.direction.eq("short").sum()),
        "events_with_newer_known_before_I":int(d.newer_known_count.gt(0).sum()),
        "events_with_newer_active_at_I":int(d.newer_active_at_I_count.gt(0).sum()),
        "events_with_newer_breaking_at_I":int(d.newer_breaking_at_I_count.gt(0).sum()),
        "events_with_newer_same_price":int(d.newer_same_price_count.gt(0).sum()),
        "events_with_newer_active_same_price":int(d.newer_active_same_price_count.gt(0).sum()),
        "max_newer_active_at_I":int(d.newer_active_at_I_count.max()),
    }])
    bydir=d.groupby("direction").agg(
        events=("direction","size"),
        newer_known=("newer_known_count",lambda s:int(s.gt(0).sum())),
        newer_active=("newer_active_at_I_count",lambda s:int(s.gt(0).sum())),
        newer_breaking_at_I=("newer_breaking_at_I_count",lambda s:int(s.gt(0).sum())),
        newer_same_price=("newer_same_price_count",lambda s:int(s.gt(0).sum())),
        newer_active_same_price=("newer_active_same_price_count",lambda s:int(s.gt(0).sum())),
    ).reset_index()
    examples=d[(d.newer_active_at_I_count>0)].sort_values(["newer_active_at_I_count","indication_time"],ascending=[False,True]).head(40)
    summary.to_csv("astra6_icc_v2_competing_pivot_summary.csv",index=False)
    bydir.to_csv("astra6_icc_v2_competing_pivot_by_direction.csv",index=False)
    d.to_csv("astra6_icc_v2_competing_pivot_detail.csv",index=False)
    examples.to_csv("astra6_icc_v2_competing_pivot_examples.csv",index=False)
    print(summary.to_string(index=False)); print(bydir.to_string(index=False))
    print(examples[["direction","indication_level","indication_time","source_pivot_time","newer_active_at_I_count","newer_breaking_at_I_count","newer_same_price_count","newest_active_pivot_time","newest_active_pivot_price"]].to_string(index=False))
    print("COMPETING-PIVOT AUDIT COMPLETE: diagnostic only; no population exclusions.")
if __name__=="__main__": main()
