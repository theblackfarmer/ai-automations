"""Research-only: compare causal ASTRA discovery event exits. NOT validated ASTRA entries."""
import pandas as pd
import numpy as np

DATA_URL = "https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv"
raw = pd.read_csv(DATA_URL)
raw.columns = ["timestamp","open","high","low","close","volume","vwap_rth","vwap_eth"]
raw["timestamp"] = pd.to_datetime(raw.timestamp)
raw = raw.dropna(subset=["timestamp","open","high","low","close"]).sort_values("timestamp").reset_index(drop=True)
events = pd.read_parquet("astra6_causal_setups.parquet").sort_values("confirmation_idx")
# Completion-guarded higher timeframe context: never inspect a partially formed 15m candle.
raw["bucket"] = raw.timestamp.dt.floor("15min")
grp = raw.groupby("bucket", sort=True)
htf = grp.agg(start=("timestamp","min"),last=("timestamp","max"),n=("close","size"),close=("close","last"))
htf = htf[(htf.n == 15) & ((htf["last"]-htf["start"]) == pd.Timedelta(minutes=14))].copy()
htf["known_at"] = htf["last"] + pd.Timedelta(minutes=1)
htf["ma10"] = htf.close.rolling(10,min_periods=10).mean()
htf["ma20"] = htf.close.rolling(20,min_periods=20).mean()
htf["bias"] = np.where(htf.ma10 > htf.ma20,1,-1)
htf.loc[htf.ma20.isna(),"bias"] = 0
known = htf[["known_at","bias","close","ma20"]].sort_values("known_at")
results=[]
# Same independent research candidate for each exit method; not a capital-constrained portfolio.
for _,ev in events.iterrows():
    i=int(ev.confirmation_idx); j=i+1
    if j>=len(raw):continue
    side=1 if ev.direction=="long" else -1
    entry=float(raw.at[j,"open"])
    stop=float(ev.sweep_extreme)
    risk=(entry-stop)*side
    if not np.isfinite(risk) or risk<=0.25 or risk>300:continue
    ts=raw.at[i,"timestamp"]+pd.Timedelta(minutes=1)
    context=known[known.known_at<=ts]
    bias=int(context.iloc[-1].bias) if len(context) else 0
    # Only continuous minute bars; data gaps end observation rather than invent fills.
    maxend=min(j+240,len(raw)-1)
    valid_end=j
    for k in range(j+1,maxend+1):
        if raw.at[k,"timestamp"]-raw.at[k-1,"timestamp"]!=pd.Timedelta(minutes=1):break
        valid_end=k
    if valid_end<=j:continue
    for kind,rr in [("fixed_1R",1),("fixed_3R",3),("fixed_5R",5),("15m_runner",None)]:
        px=None; exit_reason="time"; end=valid_end
        target=entry+side*rr*risk if rr is not None else None
        for k in range(j,valid_end+1):
            bar=raw.iloc[k]
            hitstop=(bar.low<=stop if side==1 else bar.high>=stop)
            hittarget=(bar.high>=target if side==1 else bar.low<=target) if target is not None else False
            if hitstop:
                px=min(stop,float(bar.open)) if side==1 else max(stop,float(bar.open))
                exit_reason="stop_first";end=k;break
            if hittarget:
                px=target;exit_reason="target";end=k;break
            if kind=="15m_runner":
                # A completed 15m close is available only after the close minute.
                at=bar.timestamp+pd.Timedelta(minutes=1)
                recent=known[known.known_at<=at]
                if len(recent) and int(recent.iloc[-1].bias)==-side:
                    px=float(bar.close);exit_reason="htf_flip";end=k;break
        if px is None:px=float(raw.at[end,"close"])
        net=(px-entry)*side-2.0
        results.append(dict(setup_id=int(ev.setup_id),timestamp=str(raw.at[j,"timestamp"]),
                            year=int(raw.at[j,"timestamp"].year),side=side,htf_aligned=(bias==side),
                            exit=kind,entry=entry,stop=stop,risk=risk,exit_price=px,
                            net_points=net,net_R=net/risk,holding_min=end-j,reason=exit_reason))
res=pd.DataFrame(results)
if res.empty:raise SystemExit("No eligible exit observations; no performance claim")
res.to_csv("research_asymmetry_observations.csv",index=False)
def score(g):
    p=g.net_points
    wins=p[p>0]; losses=p[p<=0]
    return pd.Series(dict(n=len(g),win_rate=round(100*len(wins)/len(g),2),
        net_points=round(p.sum(),2),mean_win=round(wins.mean(),2) if len(wins) else None,
        mean_loss=round(losses.mean(),2) if len(losses) else None,
        profit_factor=round(wins.sum()/(-losses.sum()),3) if len(losses) and losses.sum()<0 else None,
        mean_R=round(g.net_R.mean(),3),avg_hold_min=round(g.holding_min.mean(),1)))
summary=res.groupby(["exit","year","htf_aligned"],dropna=False).apply(score,include_groups=False).reset_index()
summary.to_csv("research_asymmetry_summary.csv",index=False)
print(summary.to_string(index=False))
print("WARNING: Independent candidate outcome study, overlapping events, no one-position constraint,")
print("constant 2-point costs; not executable futures PnL or proven ASTRA strategy.")
