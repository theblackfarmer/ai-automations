#!/usr/bin/env python3
"""Astra 6: causal, bar-by-bar transition engine.

No future search is used to create a setup. BUY/SELL are mirrored:
long = sell-side sweep/reclaim -> bullish repricing -> correction -> confirmation
short = buy-side sweep/reclaim -> bearish repricing -> correction -> confirmation
"""
import io, urllib.request
import numpy as np
import pandas as pd

URL='https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv'
req=urllib.request.Request(URL,headers={'User-Agent':'Astra6/1.0'})
with urllib.request.urlopen(req,timeout=180) as r: raw=r.read()
df=pd.read_csv(io.BytesIO(raw))
df.columns=['timestamp','open','high','low','close','volume','vwap_rth','vwap_eth']
df['timestamp']=pd.to_datetime(df.timestamp); df=df.sort_values('timestamp').reset_index(drop=True)
for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
df=df.dropna(subset=['open','high','low','close']).reset_index(drop=True)
prev=df.close.shift(1)
tr=pd.concat([df.high-df.low,(df.high-prev).abs(),(df.low-prev).abs()],axis=1).max(axis=1)
df['atr']=tr.rolling(20,min_periods=20).mean()
df['swh']=df.high.shift(2).gt(df.high.shift(3)) & df.high.shift(2).gt(df.high.shift(4)) & df.high.shift(2).ge(df.high.shift(1)) & df.high.shift(2).ge(df.high)
df['swl']=df.low.shift(2).lt(df.low.shift(3)) & df.low.shift(2).lt(df.low.shift(4)) & df.low.shift(2).le(df.low.shift(1)) & df.low.shift(2).le(df.low)

low_level=None; high_level=None; active=[]; rows=[]; setup_id=0
for i in range(4,len(df)-1):
    # Swing is confirmed now; pivot itself is two bars old.
    if bool(df.at[i,'swl']): low_level=(i-2,float(df.at[i-2,'low']))
    if bool(df.at[i,'swh']): high_level=(i-2,float(df.at[i-2,'high']))
    nxt=[]
    for s in active:
        age=i-s['state_idx']
        if age>60: continue
        d=s['direction']
        if s['state']=='breach':
            if (d=='long' and df.at[i,'close']>=s['level']) or (d=='short' and df.at[i,'close']<=s['level']):
                s.update(state='reclaim',state_idx=i,reclaim_idx=i,reclaim_close=float(df.at[i,'close']),reclaim_atr=float(df.at[i,'atr']))
            nxt.append(s); continue
        if s['state']=='reclaim':
            if age>30: continue
            a=s['reclaim_atr']
            if d=='long' and df.at[i,'close']>=s['reclaim_close']+a:
                s.update(state='repricing',state_idx=i,repricing_idx=i,repricing_close=float(df.at[i,'close']),repricing_dist=float(df.at[i,'close']-s['reclaim_close']))
            elif d=='short' and df.at[i,'close']<=s['reclaim_close']-a:
                s.update(state='repricing',state_idx=i,repricing_idx=i,repricing_close=float(df.at[i,'close']),repricing_dist=float(s['reclaim_close']-df.at[i,'close']))
            nxt.append(s); continue
        if s['state']=='repricing':
            dist=s['repricing_dist']
            if dist<=0: continue
            if d=='long':
                s['correction_extreme']=min(s.get('correction_extreme',s['repricing_close']),float(df.at[i,'low']))
                if s['repricing_close']-s['correction_extreme']>=.25*dist: s.update(state='correction',state_idx=i,correction_idx=i)
            else:
                s['correction_extreme']=max(s.get('correction_extreme',s['repricing_close']),float(df.at[i,'high']))
                if s['correction_extreme']-s['repricing_close']>=.25*dist: s.update(state='correction',state_idx=i,correction_idx=i)
            nxt.append(s); continue
        # Confirmation is evaluated on the current bar only.
        ok=(df.at[i,'low']>s['level'] and df.at[i,'close']>=s['repricing_close']) if d=='long' else (df.at[i,'high']<s['level'] and df.at[i,'close']<=s['repricing_close'])
        if ok:
            setup_id+=1; j=i+1
            rows.append({'setup_id':setup_id,'direction':d,'level_idx':s['level_idx'],'level':s['level'],'breach_idx':s['breach_idx'],'reclaim_idx':s['reclaim_idx'],'repricing_idx':s['repricing_idx'],'correction_idx':s['correction_idx'],'confirmation_idx':i,'confirmation_timestamp':df.at[i,'timestamp'],'confirmation_close':float(df.at[i,'close']),'sweep_extreme':s['sweep_extreme'],'repricing_dist':s['repricing_dist'],'market_on_confirm_entry':float(df.at[i,'close']),'next_open_entry':float(df.at[j,'open'])})
            continue
        nxt.append(s)
    active=nxt
    # New breach starts only on this bar. It is never backfilled from future bars.
    if np.isfinite(df.at[i,'atr']):
        if low_level is not None and i>low_level[0] and df.at[i,'low']<low_level[1]:
            active.append({'state':'breach','state_idx':i,'direction':'long','level_idx':low_level[0],'level':low_level[1],'breach_idx':i,'sweep_extreme':float(df.at[i,'low'])}); low_level=None
        if high_level is not None and i>high_level[0] and df.at[i,'high']>high_level[1]:
            active.append({'state':'breach','state_idx':i,'direction':'short','level_idx':high_level[0],'level':high_level[1],'breach_idx':i,'sweep_extreme':float(df.at[i,'high'])}); high_level=None

out=pd.DataFrame(rows)
if len(out):
    out['causal_ok']=out.apply(lambda r: all(r[c]<=r.confirmation_idx for c in ['level_idx','breach_idx','reclaim_idx','repricing_idx','correction_idx']),axis=1)
else: out['causal_ok']=pd.Series(dtype=bool)
viol=int((~out.causal_ok).sum()) if len(out) else 0
summary=pd.DataFrame([{'setups':len(out),'long_setups':int((out.direction=='long').sum()) if len(out) else 0,'short_setups':int((out.direction=='short').sum()) if len(out) else 0,'causal_violations':viol}])
out.to_parquet('astra6_causal_setups.parquet',index=False); summary.to_csv('astra6_causal_summary.csv',index=False)
print('REAL_NQ_ROWS',len(df)); print('CAUSAL_SETUPS',len(out)); print('DIRECTION_COUNTS',out.direction.value_counts().to_dict() if len(out) else {}); print('CAUSAL_VIOLATIONS',viol)
if viol: raise SystemExit('FAIL: causal lookahead violation')
