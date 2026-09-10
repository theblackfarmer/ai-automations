import io, urllib.request
import numpy as np
import pandas as pd

URL='https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv'
req=urllib.request.Request(URL,headers={'User-Agent':'Astra6/1.0'})
with urllib.request.urlopen(req,timeout=180) as r: raw=r.read()
df=pd.read_csv(io.BytesIO(raw))
df.columns=['timestamp','open','high','low','close','volume','vwap_rth','vwap_eth']
df['timestamp']=pd.to_datetime(df['timestamp']); df=df.sort_values('timestamp').reset_index(drop=True)
for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
df=df.dropna(subset=['open','high','low','close']).reset_index(drop=True)
prev=df.close.shift(1)
tr=pd.concat([df.high-df.low,(df.high-prev).abs(),(df.low-prev).abs()],axis=1).max(axis=1)
df['atr']=tr.rolling(20,min_periods=20).mean()
# Reconstruct exactly the frozen v0.1 event sequence; do not change thresholds.
df['swh']=df.high.shift(2).gt(df.high.shift(3)) & df.high.shift(2).gt(df.high.shift(4)) & df.high.shift(2).ge(df.high.shift(1)) & df.high.shift(2).ge(df.high)
df['swl']=df.low.shift(2).lt(df.low.shift(3)) & df.low.shift(2).lt(df.low.shift(4)) & df.low.shift(2).le(df.low.shift(1)) & df.low.shift(2).le(df.low)
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
                        if f is not None: events.append((f,'long',L,li,b,r,p,c)); low_level=None
    if high_level is not None:
        hi,L=high_level
        if i>hi and df.at[i,'high']>L:
            b=next((k for k in range(i,min(i+61,len(df)-61)) if df.at[k,'high']>L),None)
            r=next((k for k in range(b+1,min(b+61,len(df)-61)) if df.at[k,'close']<=L),None) if b is not None else None
            if r is not None:
                rc=df.at[r,'close']; a=df.at[r,'atr']; p=next((k for k in range(r+1,min(r+31,len(df)-61)) if df.at[k,'close']<=rc-a),None)
                if p is not None:
                    rp=df.at[p,'close']; dist=rc-rp; ce=rp; c=next((k for k in range(p+1,min(p+121,len(df)-61)) if (ce:=max(ce,df.at[k,'high'])) and ce-rp>=.25*dist),None)
                    if c is not None:
                        f=next((k for k in range(c+1,min(c+121,len(df)-61)) if df.at[k,'high']<L and df.at[k,'close']<=rp),None)
                        if f is not None: events.append((f,'short',L,hi,b,r,p,c)); high_level=None
seen=set(); rows=[]
for idx,direction,L,level_idx,b,r,p,c in sorted(events):
    if (idx,direction) in seen: continue
    seen.add((idx,direction))
    entry=df.at[idx,'close']; atr_r=df.at[r,'atr']; atr_p=df.at[p,'atr']
    breach_depth=(L-df.at[b,'low']) if direction=='long' else (df.at[b,'high']-L)
    repricing_dist=abs(df.at[p,'close']-df.at[r,'close'])
    correction_extreme=(df.loc[p:c,'low'].min() if direction=='long' else df.loc[p:c,'high'].max())
    correction_dist=(df.at[p,'close']-correction_extreme) if direction=='long' else (correction_extreme-df.at[p,'close'])
    row={'timestamp':df.at[idx,'timestamp'],'direction':direction,'level':L,'level_idx':level_idx,'breach_idx':b,'reclaim_idx':r,'repricing_idx':p,'correction_idx':c,'entry':entry,
         'reference_age_bars':b-level_idx,'breach_duration_bars':r-b,'breach_depth_atr':breach_depth/atr_r if atr_r>0 else np.nan,
         'reclaim_latency_bars':r-b,'repricing_latency_bars':p-r,'repricing_distance_atr':repricing_dist/atr_r if atr_r>0 else np.nan,
         'correction_duration_bars':c-p,'correction_ratio':correction_dist/repricing_dist if repricing_dist>0 else np.nan,
         'level_distance_atr':abs(df.at[r,'close']-L)/atr_r if atr_r>0 else np.nan,
         'hour':df.at[idx,'timestamp'].hour,'dow':df.at[idx,'timestamp'].dayofweek,'atr':atr_r}
    for h in (5,15,30,60):
        j=idx+h
        if j>=len(df): continue
        row[f'ret_{h}']=(df.at[j,'close']/entry-1) if direction=='long' else (entry/df.at[j,'close']-1)
    rows.append(row)
out=pd.DataFrame(rows)
# Continuous feature audit: fixed quantile bins are descriptive, not optimized thresholds.
features=['breach_depth_atr','breach_duration_bars','reclaim_latency_bars','repricing_latency_bars','repricing_distance_atr','correction_duration_bars','correction_ratio','reference_age_bars','level_distance_atr','atr']
records=[]
for feature in features:
    x=out[[feature,'ret_30']].replace([np.inf,-np.inf],np.nan).dropna()
    if len(x)<100: continue
    rho=x[feature].corr(x.ret_30,method='spearman')
    qs=np.unique(x[feature].quantile([0,.2,.4,.6,.8,1]).values)
    if len(qs)<3: continue
    x=x.assign(bin=pd.cut(x[feature],bins=qs,include_lowest=True,duplicates='drop'))
    g=x.groupby('bin',observed=True).agg(n=('ret_30','size'),mean_ret_pct=('ret_30',lambda z:z.mean()*100),win_pct=('ret_30',lambda z:(z>0).mean()*100)).reset_index()
    for _,z in g.iterrows(): records.append({'feature':feature,'spearman_ret30':rho,'bin':str(z['bin']),'n':int(z.n),'mean_ret30_pct':z.mean_ret_pct,'win_pct':z.win_pct})
# Direction/session/year summaries for stability audit.
summ=[]
for keys,g in out.groupby(['direction','hour']):
    if len(g)>=100: summ.append({'group':'direction_hour','key':str(keys),'n':len(g),'mean_ret30_pct':g.ret_30.mean()*100,'win_pct':(g.ret_30>0).mean()*100})
for year,g in out.assign(year=out.timestamp.dt.year).groupby('year'):
    summ.append({'group':'year','key':str(year),'n':len(g),'mean_ret30_pct':g.ret_30.mean()*100,'win_pct':(g.ret_30>0).mean()*100})
pd.DataFrame(records).to_csv('astra_transition_feature_bins.csv',index=False)
pd.DataFrame(summ).to_csv('astra_transition_stability.csv',index=False)
print('FEATURE_AUDIT_EVENTS',len(out))
print('FEATURE_AUDIT_ROWS',len(records))
print('FEATURE_AUDIT_TOP_ABS_SPEARMAN')
if len(records):
    q=pd.DataFrame(records).groupby('feature').first().reset_index(); q['absrho']=q.spearman_ret30.abs(); print(q.sort_values('absrho',ascending=False)[['feature','spearman_ret30']].to_string(index=False))
print('OUTPUTS_WRITTEN astra_transition_feature_bins.csv astra_transition_stability.csv')
