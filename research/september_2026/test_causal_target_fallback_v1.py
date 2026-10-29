"""Synthetic causal invariants for ASTRA target fallback; NO historical returns."""
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from adaptive_market_paths_v1 import (CausalLiquidityBook, LiquidityLevel,
    MinuteBar, PeriodEvidence, Premarket0300To0800)
from causal_target_fallback_v1 import (ConfirmedSwingLiquidity,
                                       select_fallback_target, verify_next_open_only)

UTC = timezone.utc
NY = ZoneInfo('America/New_York')
T = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
DATA = 'NQ_RAW_NATIVE_SOURCE'
SEG = 'NQZ6_DATED'


def period(kind='PD_ETH', id='D1', high=110, low=95, start=None,
           known_at=T, complete=True, dataset=DATA, segment=SEG):
    return PeriodEvidence(kind=kind, period_id=id,
         start=start or T-timedelta(days=1), end=known_at,
         known_at=known_at, high=high, low=low, source_id=f'PERIOD:{kind}:{id}',
         coverage_complete=complete, dataset_id=dataset, contract_segment_id=segment)


def minute(start, high=101, low=99, dataset=DATA, segment=SEG):
    return MinuteBar(start, start + timedelta(minutes=1), 100, high, low, 100,
                     f'MINUTE:{start.timestamp()}', dataset, segment)


def run(levels=None, periods=None, minutes=None, decision=T,
        side='LONG', ref=100, stop=None, canonical=None, dataset=DATA, segment=SEG,
        swings=(), parent_id=None):
    return select_fallback_target(side=side, decision_at=decision,
        decision_reference=ref,
        exact_far_wick_stop=(98 if side=='LONG' else 102) if stop is None else stop,
        canonical_target=canonical, liquidity_levels=levels or [],
        source_periods=periods or [], known_completed_minutes=minutes or [],
        dataset_id=dataset, contract_segment_id=segment,
        confirmed_swings=swings, selected_parent_id=parent_id)


def levels_for(*periods, at=T):
    b=CausalLiquidityBook()
    for p in periods:b.register(p)
    return b.levels(at,latest_only=False)


class CanonicalPriority(unittest.TestCase):
    def test_first_opposing_zone_remains_authoritative(self):
        pdh=period(high=101)
        canonical=dict(zone_id='NATIVE_HTF_SUPPLY',price=120,known_at=int((T-timedelta(minutes=2)).timestamp()),valid=True)
        result=run(levels=levels_for(pdh),periods=[pdh],canonical=canonical)
        self.assertEqual((result.status,result.target_id,result.target_price),
                         ('CANONICAL_ZONE','NATIVE_HTF_SUPPLY',120))
        self.assertEqual(result.examined_levels,0)
        self.assertFalse(result.execution_allowed)
        self.assertFalse(result.changes_entry_confirmation)
    def test_canonical_short_preserves_original_price(self):
        canonical=dict(zone_id='DEMAND',price=87,known_at=int((T-timedelta(minutes=1)).timestamp()),valid=True)
        self.assertEqual(run(side='SHORT',canonical=canonical).target_price,87)
    def test_future_original_zone_is_not_accepted(self):
        canonical=dict(zone_id='Z',price=120,known_at=int((T+timedelta(minutes=1)).timestamp()),valid=True)
        with self.assertRaisesRegex(ValueError,'causally available'):
            run(canonical=canonical)
    def test_invalid_original_zone_is_not_relabelled_as_fallback(self):
        canonical=dict(zone_id='Z',price=120,known_at=int(T.timestamp()),valid=False)
        with self.assertRaisesRegex(ValueError,'not valid'):
            run(canonical=canonical)
    def test_original_far_wick_stop_never_moves(self):
        p=period(high=110)
        chosen=run(levels=levels_for(p),periods=[p],stop=97)
        self.assertEqual(chosen.selected_origin_far_wick_stop,97)
        self.assertEqual(chosen.status,'LIQUIDITY_FALLBACK')
        self.assertFalse(verify_next_open_only(chosen,100,original_stop=96))
        self.assertTrue(verify_next_open_only(chosen,100,original_stop=97))


class DirectionalSearch(unittest.TestCase):
    def test_long_nearest_eligible_pdh_over_pwh(self):
        daily=period('PD_ETH','D1',high=105)
        weekly=period('PW_ETH','W1',high=113)
        r=run(levels=levels_for(daily,weekly),periods=[daily,weekly])
        self.assertEqual((r.status,r.target_id,r.target_price),
                         ('LIQUIDITY_FALLBACK','PD_ETH:D1:H',105))
    def test_short_chooses_pdl_over_pwl(self):
        daily=period('PD_RTH','D1',high=109,low=97)
        weekly=period('PW_ETH','W1',high=113,low=83)
        r=run(side='SHORT',levels=levels_for(daily,weekly),periods=[daily,weekly])
        self.assertEqual((r.target_id,r.target_price),('PD_RTH:D1:L',97))
    def test_previous_month_is_valid_level_family(self):
        month=period('PM_ETH','2026-09',high=108,low=85)
        r=run(levels=levels_for(month),periods=[month])
        self.assertEqual(r.target_id,'PM_ETH:2026-09:H')
    def test_no_directional_liquidity_ahead_does_not_force_target(self):
        p=period(high=99,low=91)
        r=run(levels=levels_for(p),periods=[p])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('NOT_AHEAD',r.exclusion_counts)
    def test_nearest_price_wins_not_timeframe_name(self):
        week=period('PW_ETH','W1',high=102)
        day=period('PD_ETH','D1',high=109)
        r=run(levels=levels_for(week,day),periods=[week,day])
        self.assertEqual(r.target_id,'PW_ETH:W1:H')
    def test_latest_completed_day_supersedes_old_day(self):
        old=period('PD_ETH','D0',high=101,known_at=T-timedelta(minutes=2))
        new=period('PD_ETH','D1',high=112,known_at=T)
        # The new period can be used at exactly its knowledge time, but older
        # PDH must not be selected simply because it is geometrically closer.
        result=run(levels=levels_for(old,new),periods=[old,new])
        self.assertEqual(result.target_id,'PD_ETH:D1:H')
        self.assertEqual(result.exclusion_counts['SUPERSEDED_PERIOD'],2)
    def test_future_period_cannot_hide_prior_known_period(self):
        old=period('PD_ETH','D0',high=106)
        future=period('PD_ETH','D1',high=102,known_at=T+timedelta(days=1))
        r=run(levels=levels_for(old,future,at=T+timedelta(days=2)),periods=[old,future])
        self.assertEqual(r.target_id,'PD_ETH:D0:H')
    def test_target_available_without_m1_m5_entry_evidence_does_not_enter(self):
        p=period(high=106)
        r=run(levels=levels_for(p),periods=[p])
        self.assertEqual(r.status,'LIQUIDITY_FALLBACK')
        self.assertFalse(r.execution_allowed)
        self.assertFalse(r.changes_entry_confirmation)


class IntegrityGuards(unittest.TestCase):
    def test_unsourced_level_is_ineligible_even_when_price_is_nearest(self):
        p=period(high=110)
        spoof=replace(levels_for(p)[0], id='FAKE:UNBACKED:H',price=101)
        r=run(levels=[spoof],periods=[p])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertEqual(r.exclusion_counts['SOURCE_PERIOD_NOT_AUTHENTICATED'],1)
    def test_changing_period_high_invalidates_level(self):
        p=period(high=110)
        altered=replace(levels_for(p)[0],price=111)
        r=run(levels=[altered],periods=[p])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertEqual(r.exclusion_counts['SOURCE_PERIOD_MISMATCH'],1)
    def test_incomplete_premarket_level_is_not_used(self):
        p=period(kind='PREMARKET_0300_0800',complete=False)
        r=run(levels=levels_for(p),periods=[p])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('PERIOD_UNVERIFIED_OR_INCOMPLETE',r.exclusion_counts)
    def test_future_known_liquidity_is_never_selected(self):
        p=period(known_at=T+timedelta(minutes=1))
        l=levels_for(p,at=T+timedelta(hours=1))
        r=run(levels=l,periods=[p])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('FUTURE_KNOWN',r.exclusion_counts)
    def test_wrong_contract_or_adjusted_series_ineligible(self):
        p=period(dataset='ADJUSTED_CONTINUOUS',segment='NQH6')
        r=run(levels=levels_for(p),periods=[p])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('WRONG_SOURCE_OR_CONTRACT',r.exclusion_counts)
    def test_unobserved_minute_breaks_freshness(self):
        p=period(known_at=T)
        r=run(levels=levels_for(p,at=T),periods=[p],decision=T+timedelta(minutes=3),
              minutes=[minute(T),minute(T+timedelta(minutes=2))])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('MISSING_MINUTE_OR_SCHEDULE_BRIDGE',r.exclusion_counts)
    def test_absent_prefix_is_not_silently_complete(self):
        p=period()
        r=run(levels=levels_for(p),periods=[p],decision=T+timedelta(minutes=1))
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('MISSING_MINUTE_OR_SCHEDULE_BRIDGE',r.exclusion_counts)
    def test_crossed_liquidity_is_not_fresh_destination(self):
        p=period(high=105)
        r=run(levels=levels_for(p),periods=[p],decision=T+timedelta(minutes=2),
              minutes=[minute(T,high=106), minute(T+timedelta(minutes=1))])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('ALREADY_TOUCHED_OR_SWEPT',r.exclusion_counts)
    def test_equal_touch_is_not_independently_unswept(self):
        p=period(high=105)
        r=run(levels=levels_for(p),periods=[p],decision=T+timedelta(minutes=1),
              minutes=[minute(T,high=105)])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('ALREADY_TOUCHED_OR_SWEPT',r.exclusion_counts)
    def test_valid_contiguous_history_proves_no_touch(self):
        p=period(high=105)
        bars=[minute(T+timedelta(minutes=i)) for i in range(3)]
        r=run(levels=levels_for(p),periods=[p],decision=T+timedelta(minutes=3),minutes=bars)
        self.assertEqual((r.status,r.target_price),('LIQUIDITY_FALLBACK',105))
    def test_previously_swept_book_status_is_ineligible(self):
        p=period(high=105)
        bad=replace(levels_for(p)[0],state='SWEPT_RECLAIM',state_known_at=T)
        r=run(levels=[bad],periods=[p])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('LEVEL_CONSUMED_OR_UNRESOLVED',r.exclusion_counts)
    def test_future_bar_prefix_rejected_not_leaked(self):
        p=period()
        with self.assertRaisesRegex(ValueError,'Future bar'):
            run(levels=levels_for(p),periods=[p],minutes=[minute(T)])
    def test_unordered_duplicate_source_minutes_rejected(self):
        p=period()
        with self.assertRaisesRegex(ValueError,'Unordered or duplicate'):
            run(levels=levels_for(p),periods=[p],decision=T+timedelta(minutes=2),
                minutes=[minute(T),minute(T)])
    def test_exact_stop_not_replaced_by_pdh_or_pdl(self):
        p=period(high=105)
        with self.assertRaisesRegex(ValueError,'stop must be adverse'):
            run(levels=levels_for(p),periods=[p],stop=101)
    def test_eligible_target_invalid_if_next_open_gaps_past_it(self):
        p=period(high=105)
        r=run(levels=levels_for(p),periods=[p])
        self.assertFalse(verify_next_open_only(r,106,98))
        self.assertTrue(verify_next_open_only(r,103,98))


class FifteenMinuteAndLowerStructure(unittest.TestCase):
    def swing(self, timeframe=15, side='BUY_SIDE', price=104,
              parent='P1', known=T, state='AVAILABLE'):
        start=T-timedelta(minutes=timeframe)
        return ConfirmedSwingLiquidity(
            id=f'{timeframe}:SWING:{side}:{price}', timeframe_minutes=timeframe,
            side=side, price=price, pivot_start=start, pivot_source_end=T,
            known_at=known, parent_id=parent, source_id=f'NQ:{timeframe}:{int(start.timestamp())}',
            dataset_id=DATA, contract_segment_id=SEG, state=state)

    def test_confirmed_15m_buy_side_is_valid_fallback_without_supply_zone(self):
        p=self.swing(timeframe=15,side='BUY_SIDE',price=104)
        r=run(swings=[p],parent_id='P1')
        self.assertEqual((r.status,r.target_id,r.source_kind,r.target_price),
                         ('LIQUIDITY_FALLBACK',p.id,'M15_CONFIRMED_SWING',104))
        self.assertFalse(r.execution_allowed)

    def test_confirmed_15m_low_is_short_liquidity_objective(self):
        p=self.swing(side='SELL_SIDE',price=96)
        r=run(swings=[p],side='SHORT',parent_id='P1')
        self.assertEqual((r.status,r.source_kind,r.target_price),
                         ('LIQUIDITY_FALLBACK','M15_CONFIRMED_SWING',96))

    def test_m1_m5_swings_may_supply_target_without_changing_entry_gate(self):
        m1=self.swing(timeframe=1,price=102)
        m5=self.swing(timeframe=5,price=107)
        r=run(swings=[m1,m5],parent_id='P1')
        self.assertEqual(r.target_id,m1.id)
        self.assertEqual(r.source_kind,'M1_CONFIRMED_SWING')
        self.assertFalse(r.changes_entry_confirmation)

    def test_15m_structural_liquidity_can_be_nearer_than_pdh(self):
        pdh=period(high=108)
        m15=self.swing(price=104)
        r=run(levels=levels_for(pdh),periods=[pdh],swings=[m15],parent_id='P1')
        self.assertEqual((r.target_id,r.target_price),(m15.id,104))

    def test_future_confirmed_pivot_not_used(self):
        m15=self.swing(known=T+timedelta(minutes=1))
        r=run(swings=[m15],parent_id='P1')
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('FUTURE_KNOWN_SWING',r.exclusion_counts)

    def test_cross_parent_pivot_never_used_as_target(self):
        m15=self.swing(parent='UNRELATED')
        r=run(swings=[m15],parent_id='P1')
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('WRONG_OR_UNVERIFIED_PARENT',r.exclusion_counts)

    def test_source_pivot_known_before_its_bar_completed_fails(self):
        with self.assertRaisesRegex(ValueError,'before its actual native source bar'):
            self.swing(known=T-timedelta(seconds=1))

    def test_swept_confirmed_15m_pivot_not_reused(self):
        m15=self.swing(state='SWEPT_RECLAIM')
        r=run(swings=[m15],parent_id='P1')
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('SWING_CONSUMED_OR_UNRESOLVED',r.exclusion_counts)

    def test_unobserved_15m_post_confirmation_gap_is_unknown(self):
        m15=self.swing()
        r=run(swings=[m15],parent_id='P1',decision=T+timedelta(minutes=2),
              minutes=[minute(T)])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('SWING_MISSING_MINUTE_OR_SCHEDULE_BRIDGE',r.exclusion_counts)

    def test_15m_pivot_touched_before_decision_is_already_taken(self):
        m15=self.swing(price=104)
        r=run(swings=[m15],parent_id='P1',decision=T+timedelta(minutes=1),
              minutes=[minute(T,high=104)])
        self.assertEqual(r.status,'NO_VERIFIED_TARGET')
        self.assertIn('SWING_ALREADY_TOUCHED_OR_SWEPT',r.exclusion_counts)

    def test_parent_required_for_swing_but_not_global_pdh(self):
        m15=self.swing(price=102)
        day=period(high=105)
        r=run(swings=[m15],levels=levels_for(day),periods=[day])
        self.assertEqual((r.target_price,r.target_id),(105,'PD_ETH:D1:H'))
        self.assertIn('WRONG_OR_UNVERIFIED_PARENT',r.exclusion_counts)


class PremarketWindow(unittest.TestCase):
    def _complete(self,day):
        book=Premarket0300To0800()
        start=datetime(day.year,day.month,day.day,3,tzinfo=NY)
        for i in range(300):
            a=start+timedelta(minutes=i)
            book.feed(MinuteBar(a,a+timedelta(minutes=1),100,105,95,100,
                               f'PM:{day}:{i}',DATA,SEG))
        end=datetime(day.year,day.month,day.day,8,tzinfo=NY)
        return book,book.seal(day,end,source_id='REAL_NATIVE_PREMARKET_WINDOW',
                              dataset_id=DATA,contract_segment_id=SEG)
    def test_completed_3am_to_8am_high_is_target_after_8am(self):
        day=date(2026,10,28)
        book,period=self._complete(day)
        start=period.known_at
        result=run(levels=levels_for(period,at=start),periods=[period],
                   decision=start)
        self.assertEqual(result.target_id,'PREMARKET_0300_0800:2026-10-28:H')
        self.assertTrue(period.coverage_complete)
        self.assertEqual(result.target_price,105)
    def test_3am_to_8am_not_known_before_window_closed(self):
        day=date(2026,10,28)
        book,period=self._complete(day)
        decision=period.known_at-timedelta(minutes=1)
        result=run(levels=levels_for(period,at=period.known_at),periods=[period],
                   decision=decision)
        self.assertEqual(result.status,'NO_VERIFIED_TARGET')
    def test_dst_after_fall_back_is_exactly_300_minutes(self):
        day=date(2026,11,4)
        book,p=self._complete(day)
        self.assertEqual(p.end-p.start,timedelta(minutes=300))
        self.assertEqual(p.end.hour,13)  # 08:00 EST = 13:00 UTC
        self.assertTrue(p.coverage_complete)

if __name__ == '__main__': unittest.main()
