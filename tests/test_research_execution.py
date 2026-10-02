import pandas as pd
from astra_research.execution import simulate
from astra_research.metrics import summarize

def raw(values):
    t=pd.date_range('2026-01-01',periods=len(values),freq='min',tz='UTC')
    return pd.DataFrame(values,columns=['open','high','low','close']).assign(timestamp=t)

def signal(t, direction=1, stop=98., target=104.):
    return pd.DataFrame([dict(strategy='fixture',direction=direction,decision_time=t,
          stop=stop,target=target,expiry_time=t+pd.Timedelta(minutes=3))])

def test_same_minute_stop_target_is_stop_first():
    r=raw([[100,106,97,100],[100,101,99,100]])
    t,d=simulate(r,signal(r.timestamp[0]),slippage_ticks=0)
    assert len(t)==1 and t.iloc[0].exit_reason=='stop'
    assert t.iloc[0].gross_points==-2 and d['ambiguous_minutes']==1

def test_partial_breakeven_not_retroactive_on_target_bar():
    r=raw([[100,105,99,104],[103,103,99,100],[100,100,100,100]])
    t,_=simulate(r,signal(r.timestamp[0]),slippage_ticks=0)
    assert t.iloc[0].exit_time==r.timestamp[1]
    assert t.iloc[0].gross_points==2 # half at104; half at100

def test_gap_stop_adverse_fill_and_costs():
    r=raw([[100,101,99,100],[95,96,94,95]])
    t,_=simulate(r,signal(r.timestamp[0]),instrument='MNQ',slippage_ticks=1)
    assert t.iloc[0].gross_points==-5.5
    assert t.iloc[0].net_points==-6.25

def test_no_same_minute_reentry_after_exit():
    r=raw([[100,101,97,98],[100,101,97,98]])
    s=signal(r.timestamp[0]);s=pd.concat([s,s],ignore_index=True)
    t,d=simulate(r,s,slippage_ticks=0)
    assert len(t)==1 and d['overlap_skipped']==1

def test_end_of_data_is_censored_not_fake_timeout_win():
    r=raw([[100,101,99,100]])
    t,d=simulate(r,signal(r.timestamp[0]),slippage_ticks=0)
    assert t.empty and d['censored']==1

def test_target_behind_gap_entry_rejected():
    r=raw([[106,107,105,106]])
    t,d=simulate(r,signal(r.timestamp[0]),slippage_ticks=0)
    assert t.empty and d['invalid_fill']==1

def test_mirrored_short_stop():
    r=raw([[100,101,99,100],[104,105,103,104]])
    t,_=simulate(r,signal(r.timestamp[0],-1,102,96),slippage_ticks=0)
    assert t.iloc[0].gross_points==-4

def test_drawdown_includes_initial_loss():
    t=pd.DataFrame(dict(net_R=[-1.,-2.,1.],net_USD_2contracts=[-10.,-20.,10.],
                       entry_time=pd.date_range('2026-01-01',periods=3,tz='UTC'),direction=[1,1,-1],hold_minutes=[1,1,1]))
    s=summarize(t,repetitions=10)
    assert s['max_drawdown_R']==3. and s['max_drawdown_USD']==30.

def test_expired_before_delayed_fill_is_rejected():
    r=raw([[100,101,99,100]])
    s=signal(r.timestamp[0]);s['expiry_time']=r.timestamp[0]
    t,d=simulate(r,s)
    assert t.empty and d['expired_before_fill']==1

def test_survival_gate_cannot_ignore_concentration_and_missing_checks():
    from astra_research.metrics import verdict
    good=dict(n=100,expectancy_R=.2,profit_factor=2.,monthly=[{}, {}, {}],
              by_direction={'1':{'n':50},'-1':{'n':50}},bootstrap_mean_95=[.1,.3],
              top5_gross_profit_share=.1,positive_month_concentration=.99)
    s={p:{c:good for c in ['NQ_base','MNQ_base','NQ_stress','MNQ_stress']} for p in ['validation','forward_oos']}
    v=verdict(s,source_gaps=False,causal_pass=True,neighbors=[.1],null_evidence={'familywise_pass':True})
    assert v['deployment']=='NO_GO' and any('month' in x for x in v['reasons'])

def test_timeout_gap_loss_in_static_equity():
    r=raw([[1000,1001,999,1000],[1000,1001,999,1000],[1000,1001,999,1000],[400,401,399,400]])
    s=signal(r.timestamp[0],stop=100,target=1200)
    t,_=simulate(r,s,slippage_ticks=0)
    m=summarize(t,repetitions=10)
    assert t.iloc[0].exit_reason=='time'
    assert m['assumed_50k_static_2000_loss']['breach']


def test_verdict_counts_censored_validation_gaps():
    from astra_research.metrics import verdict
    v=verdict({},execution_diagnostics={'validation':{'MNQ_base':{'uncertain_trades':1}}})
    assert any('validation positions' in r for r in v['reasons'])


def test_gap_exit_extremes_are_labelled_envelopes_not_exact_excursions():
    r=raw([[100,101,99,100],[95,120,80,100]])
    t,_=simulate(r,signal(r.timestamp[0]),slippage_ticks=0)
    assert 'mae_R' not in t and 'mfe_R' not in t
    assert 'Not exact trade MAE/MFE' in t.iloc[0].envelope_scope
    assert t.iloc[0].gross_points==-5
