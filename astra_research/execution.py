"""Minute-resolution, adverse-fill execution separate from causal decisions."""
from __future__ import annotations
import numpy as np
import pandas as pd

COSTS = {'NQ': (20., 5.), 'MNQ': (2., 1.50)}

def simulate(raw, signals, *, instrument='MNQ', slippage_ticks=1, fee_multiplier=1., pivots=None, end=None):
    """One position at a time, two equal contracts, 50% TP1 then causal trail.

    Inputs must be validated raw interval-open timestamps and source-causal signals.
    Censored positions are reported separately; no invented closing fill.
    """
    if raw.empty:
        return pd.DataFrame(), {'candidates': len(signals), 'no_data': len(signals)}
    bars = raw.sort_values('timestamp').reset_index(drop=True)
    times = pd.DatetimeIndex(bars.timestamp)
    ns = times.asi8
    o, h, l = [bars[c].to_numpy(float) for c in ('open', 'high', 'low')]
    tick=.25; slip=slippage_ticks*tick
    value, fee = COSTS[instrument]; fee*=fee_multiplier
    end_ns=pd.Timestamp(end).value if end is not None else ns[-1]+60_000_000_000
    diagnostics=dict(candidates=len(signals), overlap_skipped=0, no_next_bar=0,
                     invalid_fill=0, expired_before_fill=0, censored=0, ambiguous_minutes=0, entry_gap_minutes=0, held_gap_minutes=0, uncertain_trades=0)
    rows=[]; busy_until=-1
    if signals.empty:
        return pd.DataFrame(), diagnostics
    pt={}
    if pivots is not None and len(pivots):
        for kind in ('high','low'):
            q=pivots[pivots.kind==kind].sort_values('known_time')
            pt[kind]=(pd.DatetimeIndex(q.known_time).asi8,q.price.to_numpy(float))
    for s in signals.sort_values('decision_time',kind='stable').to_dict('records'):
        decision=pd.Timestamp(s['decision_time']).value
        i=int(np.searchsorted(ns,decision,side='left'))
        if i>=len(ns) or ns[i]>=end_ns:
            diagnostics['no_next_bar']+=1;continue
        if ns[i]<=busy_until:
            diagnostics['overlap_skipped']+=1;continue
        if ns[i]>=pd.Timestamp(s['expiry_time']).value:
            diagnostics['expired_before_fill']+=1;continue
        d=int(s['direction']);entry=o[i]+d*slip;stop=float(s['stop']);target=float(s['target'])
        risk=d*(entry-stop)
        if risk<1. or d*(target-entry)<=0:
            diagnostics['invalid_fill']+=1;continue
        diagnostics['entry_gap_minutes']+=int(ns[i]>decision)
        expiry=min(pd.Timestamp(s['expiry_time']).value,end_ns)
        remaining=1.;points=0.;tp=False;pending_stop=None;ambiguities=0
        reason='censored';exit_i=None;max_adverse=0.;max_favorable=0.
        last_pivot=-1;held_gaps=0;worst_mark=0.
        for j in range(i,len(ns)):
            if ns[j]>=end_ns:break
            if j>i and ns[j]-ns[j-1]>60_000_000_000:
                held_gaps+=int((ns[j]-ns[j-1])/60_000_000_000)-1
            if pending_stop is not None:
                stop=max(stop,pending_stop) if d==1 else min(stop,pending_stop)
                pending_stop=None
            if tp and pt:
                pns,prices=pt.get('low' if d==1 else 'high',(np.array([]),np.array([])))
                k=int(np.searchsorted(pns,ns[j],side='right'))-1
                if k>last_pivot and k>=0:
                    last_pivot=k
                    # Pivot must be known after signal, not a stale pre-entry structure.
                    if pns[k]>decision:
                        candidate=prices[k]-d*tick
                        stop=max(stop,candidate) if d==1 else min(stop,candidate)
            if ns[j]>=expiry:
                points+=remaining*d*(o[j]-d*slip-entry);reason='time';exit_i=j;break
            adverse=(l[j]-entry) if d==1 else (entry-h[j])
            favorable=(h[j]-entry) if d==1 else (entry-l[j])
            max_adverse=min(max_adverse,adverse);max_favorable=max(max_favorable,favorable)
            hit_stop=l[j]<=stop if d==1 else h[j]>=stop
            hit_target=(h[j]>=target if d==1 else l[j]<=target) and not tp
            adverse_mark=min(o[j],max(l[j],stop)) if d==1 else max(o[j],min(h[j],stop))
            worst_mark=min(worst_mark,points+remaining*d*(adverse_mark-d*slip-entry))
            if hit_stop:
                if hit_target:ambiguities+=1
                base=min(stop,o[j]) if d==1 else max(stop,o[j])
                points+=remaining*d*(base-d*slip-entry);reason='stop';exit_i=j;break
            if hit_target:
                # No favorable gap price improvement; adverse target slippage is conservative.
                points+=.5*d*(target-d*slip-entry);remaining=.5;tp=True
                pending_stop=entry
        diagnostics['ambiguous_minutes']+=ambiguities
        diagnostics['held_gap_minutes']+=held_gaps
        diagnostics['uncertain_trades']+=int(held_gaps>0)
        if exit_i is None:
            diagnostics['censored']+=1;busy_until=end_ns
            continue
        busy_until=ns[exit_i]
        worst_mark=min(worst_mark,points)
        net_points=points-fee/value
        rows.append(dict(strategy=s['strategy'],direction=d,decision_time=s['decision_time'],
             entry_time=times[i],exit_time=times[exit_i],entry=entry,initial_stop=float(s['stop']),
             target=target,risk_points=risk,gross_points=points,net_points=net_points,
             net_R=net_points/risk,net_USD_2contracts=2*value*net_points,
             exit_reason=reason,partial_taken=tp,ambiguous_minutes=ambiguities,
             hold_minutes=(ns[exit_i]-ns[i])/60e9,observed_bar_adverse_envelope_R=max_adverse/risk,observed_bar_favorable_envelope_R=max_favorable/risk,
             envelope_scope="Full observed bars may include prices after stop exit; time-exit bar excluded. Not exact trade MAE/MFE.",
             slippage_ticks=slippage_ticks,roundtrip_fee_per_contract=fee,instrument=instrument,
             held_gap_minutes=held_gaps,execution_gap_uncertain=held_gaps>0,
             intratrade_worst_net_USD_2contracts=2*value*(worst_mark-fee/value)))
    return pd.DataFrame(rows),diagnostics
