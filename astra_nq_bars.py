import io, urllib.request
import numpy as np
import pandas as pd

URL='https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv'
req=urllib.request.Request(URL,headers={'User-Agent':'Astra6/1.0'})
with urllib.request.urlopen(req,timeout=180) as r: raw=r.read()
df=pd.read_csv(io.BytesIO(raw))
df.columns=['timestamp','open','high','low','close','volume','vwap_rth','vwap_eth']
df['timestamp']=pd.to_datetime(df['timestamp'])
df=df.sort_values('timestamp').reset_index(drop=True)
for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
df=df.dropna(subset=['open','high','low','close']).reset_index(drop=True)
prev=df['close'].shift(1)
tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
df['atr']=tr.rolling(20,min_periods=20).mean()
# Frozen causal 2-left/2-right swing confirmation.
df['swh']=df['high'].shift(2).gt(df['high'].shift(3)) & df['high'].shift(2).gt(df['high'].shift(4)) & df['high'].shift(2).ge(df['high'].shift(1)) & df['high'].shift(2).ge(df['high'])
df['swl']=df['low'].shift(2).lt(df['low'].shift(3)) & df['low'].shift(2).lt(df['low'].shift(4)) & df['low'].shift(2).le(df['low'].shift(1)) & df['low'].shift(2).le(df['low'])

events=[]; low_level=None; high_level=None
for i in range(4,len(df)-61):
    if bool(df.at[i,'swl']): low_level=(i-2,float(df.at[i-2,'low']))
    if bool(df.at[i,'swh']): high_level=(i-2,float(df.at[i-2,'high']))
    if not np.isfinite(df.at[i,'atr']) or df.at[i,'atr']<=0: continue
    if low_level is not None:
        li,L=low_level
        if i>li and df.at[i,'low']<L:
            b=next((k for k in range(i,min(i+61,len(df)-61)) if df.at[k,'low']<L),None)
            r=next((k for k in range(b+1,min(b+61,len(df)-61)) if df.at[k,'close']>=L),None) if b is not None else None
            if r is not None:
                rc=df.at[r,'close']; a=df.at[r,'atr']; p=next((k for k in range(r+1,min(r+31,len(df)-61)) if df.at[k,'close']>=rc+a),None)
                if p is not None:
                    rp=df.at[p,'close']; dist=rp-rc; ce=rp; c=next((k for k in range(p+1,min(p+121,len(df)-61)) if (ce:=min(ce,df.at[k,'low'])) and rp-ce>=.25*dist),None)
                    if c is not None:
                        f=next((k for k in range(c+1,min(c+121,len(df)-61)) if df.at[k,'low']>L and df.at[k,'close']>=rp),None)
                        if f is not None: events.append((f,'long',L,b,r,p,c)); low_level=None
    if high_level is not None:
        hi,L=high_level
        if i>hi and df.at[i,'high']>L:
            b=next((k for k in range(i,min(i+61,len(df)-61)) if df.at[k,'high']>L),None)
            r=next((k for k in range(b+1,min(b+61,len(df)-61)) if df.at[k,'close']<=L),None) if b is not None else None
            if r is not None:
                rc=df.at[r,'close']; a=df.at[r,'atr']; p=next((k for k in range(r+1,min(r+31,len(df)-61)) if df.at[k,'close']<=rc-a),None)
                if p is not None:
                    rp=df.at[p,'close']; dist=rc-rp; ce=rp
                    c=next((k for k in range(p+1,min(p+121,len(df)-61)) if (ce:=max(ce,df.at[k,'high'])) and ce-rp>=.25*dist),None)
                    if c is not None:
                        f=next((k for k in range(c+1,min(c+121,len(df)-61)) if df.at[k,'high']<L and df.at[k,'close']<=rp),None)
                        if f is not None: events.append((f,'short',L,b,r,p,c)); high_level=None

seen=set(); rows=[]
for idx,direction,L,b,r,p,c in sorted(events):
    if (idx,direction) in seen: continue
    seen.add((idx,direction)); entry=df.at[idx,'close']
    row={'timestamp':df.at[idx,'timestamp'],'direction':direction,'level':L,'breach_idx':b,'reclaim_idx':r,'repricing_idx':p,'correction_idx':c,'entry':entry}
    for h in (5,15,30,60):
        j=idx+h
        if direction=='long':
            row[f'ret_{h}']=df.at[j,'close']/entry-1; row[f'mfe_{h}']=df.loc[idx:j,'high'].max()/entry-1; row[f'mae_{h}']=df.loc[idx:j,'low'].min()/entry-1
        else:
            row[f'ret_{h}']=entry/df.at[j,'close']-1; row[f'mfe_{h}']=entry/df.loc[idx:j,'low'].min()-1; row[f'mae_{h}']=entry/df.loc[idx:j,'high'].max()-1
    rows.append(row)
out=pd.DataFrame(rows)
print('REAL_NQ_ROWS',len(df)); print('REAL_NQ_RANGE',df.timestamp.iloc[0],df.timestamp.iloc[-1]); print('TRANSITION_EVENTS',len(out))
if len(out):
    print('DIRECTION_COUNTS',out.direction.value_counts().to_dict())
    for h in (5,15,30,60):
        x=out[f'ret_{h}']; print(f'H{h}_N',len(x),'MEAN_RETURN_PCT',round(x.mean()*100,4),'MEDIAN_RETURN_PCT',round(x.median()*100,4),'WIN_RATE',round((x>0).mean()*100,2),'MFE_PCT',round(out[f'mfe_{h}'].mean()*100,4),'MAE_PCT',round(out[f'mae_{h}'].mean()*100,4))
    print('YEAR_COUNTS',out.assign(year=out.timestamp.dt.year).groupby('year').size().to_dict())
else: print('NO_EVENTS')

# Trigger comment: force a fresh push-run of the frozen development engine.
