"""Chronological ICC interpretation ASTRA6-R1-20261002. No outcomes."""
from dataclasses import dataclass, replace
from types import MappingProxyType
import numpy as np
import pandas as pd
from .data import resample
SCHEMA = ['strategy','direction','decision_time','stop','target','expiry_time',
          'structure_known_time','indication_time','correction_time','indication_level',
          'reaction_level','first_pivot_time','transition_pivot_time']

def pivots(bars, minutes, left=2, right=2):
    """Strict symmetric extrema, dual kinds excluded, contiguous windows only."""
    if left < 1 or right < 1:
        raise ValueError('pivot widths must be positive')
    columns = ['known_time','pivot_time','window_start','kind','price','known_idx','pivot_idx']
    n = left+right+1
    if len(bars) < n:
        return pd.DataFrame(columns=columns)
    hs = np.lib.stride_tricks.sliding_window_view(bars.high.to_numpy(float), n)
    ls = np.lib.stride_tricks.sliding_window_view(bars.low.to_numpy(float), n)
    other = np.arange(n) != left
    high = (hs[:,left,None] > hs[:,other]).all(axis=1)
    low = (ls[:,left,None] < ls[:,other]).all(axis=1)
    # Every adjacent interval must be contiguous, not merely matching endpoints.
    ts = bars.timestamp.reset_index(drop=True)
    edges = (ts.diff().iloc[1:] == pd.Timedelta(minutes=minutes)).to_numpy()
    good = np.lib.stride_tricks.sliding_window_view(edges, n-1).all(axis=1)
    starts = np.flatnonzero(good & (high ^ low))
    pi, ki = starts+left, starts+n-1
    return pd.DataFrame({
        'known_time': bars.timestamp.iloc[ki].to_numpy(),
        'pivot_time': bars.timestamp.iloc[pi].to_numpy(),
        'window_start': bars.start_time.iloc[starts].to_numpy(),
        'kind': np.where(high[starts], 'high', 'low'),
        'price': np.where(high[starts], bars.high.to_numpy()[pi], bars.low.to_numpy()[pi]).astype(float),
        'known_idx': ki, 'pivot_idx': pi,
    }, columns=columns)


@dataclass(frozen=True)
class Prepared:
    """Immutable bar/pivot cache. Verified independently before prefix use."""
    ht: tuple
    lt: tuple
    hp: object
    lp: object
    h_columns: tuple
    l_columns: tuple
    h_hashes: bytes
    l_hashes: bytes
    params: tuple
    verified: bool = False


def _row_hashes(frame):
    return pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()


def prepare(h, l, htf_minutes=60, ltf_minutes=15, left=2, right=2):
    return Prepared(
        tuple(h.itertuples(index=False)), tuple(l.itertuples(index=False)),
        MappingProxyType({int(r.known_idx): r for r in pivots(h,htf_minutes,left,right).itertuples(index=False)}),
        MappingProxyType({int(r.known_idx): r for r in pivots(l,ltf_minutes,left,right).itertuples(index=False)}),
        tuple(h.columns), tuple(l.columns), _row_hashes(h), _row_hashes(l),
        (htf_minutes,ltf_minutes,left,right))


def verify_prepared(h, l, cache):
    """Independent scalar oracle checks every possible pivot, not just cached ones.

    No call to the vectorized detector: confirms exact source values, contiguous
    windows, strict extrema, dual exclusion, confirmation index and availability.
    Returns a new immutable verified cache; the input remains unmodified.
    """
    if (tuple(h.columns)!=cache.h_columns or tuple(l.columns)!=cache.l_columns or
        tuple(h.itertuples(index=False))!=cache.ht or tuple(l.itertuples(index=False))!=cache.lt or
        _row_hashes(h)!=cache.h_hashes or _row_hashes(l)!=cache.l_hashes):
        raise ValueError('prepared source mismatch')
    hm,lm,left,right=cache.params
    for bars, mapping, minutes in [(cache.ht,cache.hp,hm),(cache.lt,cache.lp,lm)]:
        expected={}
        for k in range(left+right,len(bars)):
            i=k-right
            window=bars[i-left:k+1]
            if any(window[j].timestamp-window[j-1].timestamp!=pd.Timedelta(minutes=minutes)
                   for j in range(1,len(window))):
                continue
            other=window[:left]+window[left+1:]
            ph=all(bars[i].high>x.high for x in other)
            pl=all(bars[i].low<x.low for x in other)
            if ph==pl:
                continue
            expected[k]=(bars[k].timestamp,bars[i].timestamp,window[0].start_time,
                         'high' if ph else 'low',float(bars[i].high if ph else bars[i].low),k,i)
        actual={k:tuple(v) for k,v in mapping.items()}
        if actual!=expected:
            raise ValueError('prepared pivot oracle mismatch')
    return replace(cache,verified=True)


def _check_prefix(frame, columns, hashes, count):
    if len(frame)>count or tuple(frame.columns)!=columns or _row_hashes(frame)!=hashes[:len(frame)*8]:
        raise ValueError('prepared prefix source mismatch')

def protecting_pivots(raw, ltf_minutes, left=2, right=2):
    return pivots(resample(raw,ltf_minutes),ltf_minutes,left,right)

def iter_signals_from_bars(h,l,htf_minutes=60,ltf_minutes=15,left=2,right=2,
                           body_fraction=.5,horizon=96,strategy='icc_1h_15m',_prepared=None):
    if not 0 <= body_fraction <= 1 or horizon < 1:
        raise ValueError('invalid frozen parameter')
    if _prepared is None:
        hp = {int(r.known_idx):r for r in pivots(h,htf_minutes,left,right).itertuples(index=False)}
        lp = {int(r.known_idx):r for r in pivots(l,ltf_minutes,left,right).itertuples(index=False)}
        ht,lt = tuple(h.itertuples(index=False)),tuple(l.itertuples(index=False))
    else:
        c = _prepared
        if not c.verified or c.params != (htf_minutes,ltf_minutes,left,right):
            raise ValueError('prepared cache must be independently verified with matching parameters')
        _check_prefix(h,c.h_columns,c.h_hashes,len(c.ht))
        _check_prefix(l,c.l_columns,c.l_hashes,len(c.lt))
        # Future records cannot enter state: traversal is bounded to actual prefix
        # lengths and map lookup uses only the current completed-bar index.
        ht,lt,hp,lp = c.ht[:len(h)],c.lt[:len(l)],c.hp,c.lp
    latest = {'high':None,'low':None}
    active = None
    j = 0
    for i,bar in enumerate(lt):
        # Simultaneously known HTF indication supersedes before LTF processing.
        while j < len(ht) and ht[j].timestamp <= bar.timestamp:
            hb = ht[j]
            if active is not None and active['correction'] is None:
                active['target'] = max(active['target'],hb.high) if active['direction']==1 else min(active['target'],hb.low)
            if j in hp:
                p = hp[j]
                latest[p.kind] = p
            breaks=[]
            if j:
                previous=ht[j-1].close
                for kind,direction in [('high',1),('low',-1)]:
                    p=latest[kind]
                    if p is None:
                        continue
                    beyond=hb.close>p.price if direction==1 else hb.close<p.price
                    crossed=previous<=p.price if direction==1 else previous>=p.price
                    if beyond:
                        latest[kind]=None  # broken/superseded pivots never resurrect
                    if beyond and crossed:
                        breaks.append((direction,p))
            if len(breaks)==1:
                direction,p=breaks[0]
                span=hb.high-hb.low
                if span>0 and direction*(hb.close-hb.open)>0 and abs(hb.close-hb.open)>=body_fraction*span:
                    active=dict(direction=direction,indication=hb.timestamp,level=p.price,
                                target=hb.high if direction==1 else hb.low,
                                correction=None,c_idx=None,chain=[],structure=None)
            j+=1
        if active is None or bar.timestamp<=active['indication']:
            continue
        a=active; direction=a['direction']
        if a['correction'] is None:
            if direction*(bar.close-bar.open)<0:
                a['correction'],a['c_idx']=bar.timestamp,i
            continue
        if i-a['c_idx']>=horizon:
            active=None
            continue
        p=lp.get(i)
        if a['structure'] is None and p is not None and p.window_start>=a['correction']:
            chain=a['chain']
            if chain and chain[-1].kind==p.kind:
                chain[-1]=p
            else:
                chain.append(p)
                del chain[:-3]
            wanted=['low','high','low'] if direction==1 else ['high','low','high']
            if len(chain)==3 and [q.kind for q in chain]==wanted and direction*(chain[2].price-chain[0].price)>0:
                a['structure']=tuple(chain)
        if a['structure'] is None:
            continue
        first,reaction,transition=a['structure']
        if direction*(bar.close-first.price)<0:
            active=None
            continue
        if bar.timestamp<=transition.known_time:
            continue
        previous=lt[i-1].close
        if direction*(bar.close-reaction.price)>0 and direction*(previous-reaction.price)<=0 and direction*(bar.close-a['level'])>0:
            row=dict(strategy=strategy,direction=direction,decision_time=bar.timestamp,
                     stop=transition.price-direction*.25,target=a['target'],
                     expiry_time=bar.timestamp+pd.Timedelta(hours=120 if htf_minutes==240 else 24),
                     structure_known_time=transition.known_time,indication_time=a['indication'],
                     correction_time=a['correction'],indication_level=a['level'],reaction_level=reaction.price,
                     first_pivot_time=first.pivot_time,transition_pivot_time=transition.pivot_time)
            active=None
            yield row

def signals_from_bars(h,l,htf_minutes=60,ltf_minutes=15,left=2,right=2,body_fraction=.5,horizon=96,strategy='icc_1h_15m',_prepared=None):
    return pd.DataFrame(iter_signals_from_bars(h,l,htf_minutes,ltf_minutes,left,right,body_fraction,horizon,strategy,_prepared),columns=SCHEMA)

def iter_signals(raw,htf_minutes=60,ltf_minutes=15,left=2,right=2,body_fraction=.5,horizon=96,strategy='icc_1h_15m'):
    yield from iter_signals_from_bars(resample(raw,htf_minutes),resample(raw,ltf_minutes),htf_minutes,ltf_minutes,left,right,body_fraction,horizon,strategy)

def signals(raw,htf_minutes=60,ltf_minutes=15,left=2,right=2,body_fraction=.5,horizon=96,strategy='icc_1h_15m'):
    return pd.DataFrame(iter_signals(raw,htf_minutes,ltf_minutes,left,right,body_fraction,horizon,strategy),columns=SCHEMA)
