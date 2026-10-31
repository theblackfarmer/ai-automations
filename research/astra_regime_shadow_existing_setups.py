"""Research-only 15m shadow classification for frozen causal setups.
Uses previous COMPLETE fifteen-minute bucket; never changes execution decisions.
Timestamps are treated in the original CSV's naive ET clock. Raw minute open/close
semantics must be verified independently before treating this as a production gate.
"""
import hashlib
import io
import urllib.request
import pandas as pd
from astra_market_regime import Bar, CausalRegime15m

URL = "https://raw.githubusercontent.com/s-k-28/nq-es-trader-5k-payout/main/data/Dataset_NQ_1min_2022_2025.csv"
EXPECTED = "1577e60a7feab411e49da7a56c7052a64738cd1757cfd60aa11fd783ff43b60b"
def run():
    with urllib.request.urlopen(urllib.request.Request(URL,headers={"User-Agent":"ASTRA-research/1"}),timeout=180) as response:
        raw=response.read()
    fingerprint=hashlib.sha256(raw).hexdigest()
    if fingerprint != EXPECTED:
        raise RuntimeError("Historical source checksum mismatch: "+fingerprint)
    bars=pd.read_csv(io.BytesIO(raw))
    bars.columns=["timestamp","open","high","low","close","volume","vwap_rth","vwap_eth"]
    bars["timestamp"]=pd.to_datetime(bars.timestamp)
    bars=bars.sort_values("timestamp").drop_duplicates("timestamp")
    for col in ("open","high","low","close"):
        bars[col]=pd.to_numeric(bars[col],errors="coerce")
    bars=bars.dropna(subset=["timestamp","open","high","low","close"])
    # Strictly completed 15-minute windows. No filling absent minutes.
    bars["bucket_end"]=bars.timestamp.dt.floor("15min")+pd.Timedelta(minutes=15)
    grouped=bars.groupby("bucket_end",sort=True)
    fifteen=grouped.agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),count=("close","size"))
    fifteen=fifteen[fifteen["count"]==15]
    classifier=CausalRegime15m()
    observations=[]
    prev_end=None
    for end,bar in fifteen.iterrows():
        # Reset across any missing higher-timeframe windows to avoid stale context.
        if prev_end is not None and end-prev_end!=pd.Timedelta(minutes=15):
            classifier=CausalRegime15m()
        regime=classifier.update_closed_bar(Bar(end,float(bar.open),float(bar.high),float(bar.low),float(bar.close)))
        observations.append({"regime_at":end,"regime":regime.label,"efficiency":regime.efficiency,
                             "atr_relative":regime.atr_relative})
        prev_end=end
    regime_df=pd.DataFrame(observations).sort_values("regime_at")
    setups=pd.read_parquet("astra6_causal_setups.parquet")
    if setups.empty:
        raise RuntimeError("No frozen causal setups; cannot report coverage")
    setups["confirmation_timestamp"]=pd.to_datetime(setups.confirmation_timestamp)
    candidates=setups.sort_values("confirmation_timestamp")
    # Strict backward means observations from the currently-forming 15m window
    # cannot be used, even if source minute timestamps label opens rather than closes.
    merged=pd.merge_asof(candidates,regime_df,left_on="confirmation_timestamp",
                         right_on="regime_at",direction="backward",allow_exact_matches=False,
                         tolerance=pd.Timedelta(minutes=15))
    merged["regime"]=merged["regime"].fillna("UNKNOWN")
    merged["would_allow"]=( ((merged.direction=="long") & (merged.regime=="TREND_UP")) |
                           ((merged.direction=="short") & (merged.regime=="TREND_DOWN")) )
    merged["shadow_only"]=True
    merged["month"]=merged.confirmation_timestamp.dt.to_period("M").astype(str)
    merged.to_csv("astra_regime_shadow_setups.csv",index=False)
    monthly=merged.groupby(["month","regime"],dropna=False).agg(
        candidates=("setup_id","size"),aligned=("would_allow","sum")).reset_index()
    monthly.to_csv("astra_regime_shadow_census.csv",index=False)
    print("Historical source hash:",fingerprint)
    print("Frozen setups:",len(merged),"aligned:",int(merged.would_allow.sum()),
          "unknown:",int((merged.regime=="UNKNOWN").sum()))
    print("No trade P&L was computed. Shadow classification only.")
if __name__=="__main__":
    run()
