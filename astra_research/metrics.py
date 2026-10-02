"""Descriptive statistics and predeclared serial block resampling."""
import numpy as np
import pandas as pd

def summarize(trades, *, seed=2601002, repetitions=2000):
    if trades.empty:return {'n':0,'expectancy_R':None,'profit_factor':None,'max_drawdown_R':None,'bootstrap_mean_95':None}
    x=trades.net_R.to_numpy(float);positive=x[x>0];negative=x[x<0]
    eq=np.r_[0,np.cumsum(x)];dd=np.maximum.accumulate(eq)-eq
    gross_profit=positive.sum();loss=-negative.sum()
    rng=np.random.default_rng(seed);n=len(x);block=max(1,int(np.sqrt(n)))
    means=[];drawdowns=[]
    for _ in range(repetitions):
        starts=rng.integers(0,n,size=int(np.ceil(n/block)))
        idx=((starts[:,None]+np.arange(block))%n).ravel()[:n]
        z=x[idx];means.append(float(z.mean()));e=np.r_[0,np.cumsum(z)]
        drawdowns.append(float((np.maximum.accumulate(e)-e).max()))
    dates=pd.to_datetime(trades.entry_time,utc=True).dt.tz_convert('America/New_York')
    monthly=trades.assign(month=dates.dt.strftime('%Y-%m')).groupby('month').net_R.agg(['count','sum','mean'])
    positive_months=monthly['sum'].clip(lower=0).sum()
    longest=streak=0
    for v in x:
        streak=streak+1 if v<0 else 0;longest=max(longest,streak)
    prior_realized=trades.net_USD_2contracts.cumsum().shift(1,fill_value=0)
    static_min=float(np.minimum(prior_realized+trades.get('intratrade_worst_net_USD_2contracts',trades.net_USD_2contracts),prior_realized+trades.net_USD_2contracts).min())
    return {'assumed_50k_static_2000_loss':{'minimum_equity_USD':50000+min(0,static_min),'breach':static_min < -2000,'scope':'Conservative intraminute marks, fixed2contracts; not a prop firm feasibility claim; missing bars remain uncertain'},'n':n,'expectancy_R':float(x.mean()),'profit_factor':float(gross_profit/loss) if loss else None,
      'win_rate':float((x>0).mean()),'total_R':float(x.sum()),'net_USD_2contracts':float(trades.net_USD_2contracts.sum()),
      'max_drawdown_R':float(dd.max()),'max_drawdown_USD':float((np.maximum.accumulate(np.r_[0,trades.net_USD_2contracts.cumsum()])-np.r_[0,trades.net_USD_2contracts.cumsum()]).max()),
      'worst_trade_R':float(x.min()),'longest_losing_streak':longest,
      'quantiles_R':{str(q):float(np.quantile(x,q)) for q in [.01,.05,.25,.5,.75,.95,.99]},
      'avg_winner_R':float(positive.mean()) if len(positive) else None,'avg_loser_R':float(negative.mean()) if len(negative) else None,
      'top5_gross_profit_share':float(np.sort(positive)[-5:].sum()/gross_profit) if gross_profit else None,
      'bootstrap_mean_95':[float(v) for v in np.quantile(means,[.025,.975])],
      'bootstrap_drawdown_95_99':[float(v) for v in np.quantile(drawdowns,[.95,.99])],
      'monthly':monthly.reset_index().to_dict('records'),'positive_month_concentration':float(monthly['sum'].max()/positive_months) if positive_months else None,
      'by_direction':{str(d):{'n':len(g),'expectancy_R':float(g.net_R.mean()),'total_R':float(g.net_R.sum())} for d,g in trades.groupby('direction')},
      'mean_hold_minutes':float(trades.hold_minutes.mean()),'held_gap_trades':int(trades.get('execution_gap_uncertain',pd.Series(dtype=bool)).sum()),'source_note':'Conditional research estimates; block bootstrap is not independent unseen validation.'}

def verdict(summaries, *, source_gaps=True, neighbors=None, causal_pass=False, null_evidence=None, execution_diagnostics=None):
    reasons=[]
    o=summaries.get('forward_oos',{}).get('MNQ_base',{})
    if o.get('n',0)<100:reasons.append('forward OOS fewer than100 completed nonoverlap trades')
    if len(o.get('monthly',[]))<3:reasons.append('forward OOS fewer than3 observed months')
    if any(o.get('by_direction',{}).get(str(d),{}).get('n',0)<30 for d in [-1,1]):reasons.append('fewer than30 OOS observations in at least one direction')
    for partition in ['validation','forward_oos']:
        for cost in ['NQ_base','MNQ_base','NQ_stress','MNQ_stress']:
            s=summaries.get(partition,{}).get(cost,{})
            if s.get('expectancy_R') is None or s['expectancy_R']<=0:reasons.append(partition+' '+cost+' nonpositive or unavailable expectancy')
    if o.get('bootstrap_mean_95') is None or o['bootstrap_mean_95'][0]<=0:reasons.append('OOS mean lower95% bound not positive')
    if o.get('top5_gross_profit_share') is None or o['top5_gross_profit_share']>.25:reasons.append('OOS concentration fails or unavailable')
    if o.get('positive_month_concentration') is None or o['positive_month_concentration']>.5:reasons.append('one OOS month exceeds50percent positive profit or unavailable')
    if o.get('profit_factor') is None or o['profit_factor']<=1:reasons.append('OOS profit factor not above1')
    if not causal_pass:reasons.append('complete positive-controlled causal evidence required')
    if neighbors is None:reasons.append('predeclared neighboring sensitivity evidence required')
    elif any(v is None or v<=0 for v in neighbors):reasons.append('neighboring sensitivity expectancy nonpositive or unavailable')
    if null_evidence is None or not null_evidence.get('familywise_pass',False):reasons.append('familywise random-entry null evidence not cleared')
    for partition in ['validation','forward_oos']:
        observed=summaries.get(partition,{}).get('MNQ_base',{}).get('held_gap_trades',0)
        diagnosed=(execution_diagnostics or {}).get(partition,{}).get('MNQ_base',{}).get('uncertain_trades',0)
        if max(observed,diagnosed)>0:reasons.append('unresolved missing intervals during '+partition+' positions, including censored positions')
    if source_gaps:reasons.append('unresolved source fidelity/data provenance prevents deployment')
    return {'research':'FAIL_OR_INSUFFICIENT' if reasons else 'PROVISIONAL_SURVIVOR','deployment':'NO_GO' if reasons else 'PAPER_ONLY','reasons':reasons}
