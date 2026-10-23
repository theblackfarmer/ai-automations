"""Causal quote-based research execution, separate from ASTRA signal authority.

Prices in the ledger are observed quotes, not broker fills. Existing stop and
target orders use a MARKET_IF_TOUCHED research model with zero extra exit
latency. New stop levels require a strictly later quote timestamp. Commission
and extra slippage are disclosed point deductions; neither rewrites a quote.
The module imports no signal engine, performs no I/O, and cannot place orders.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
from typing import Iterable, Optional


MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR


@dataclass(frozen=True)
class Quote:
    timestamp_ms: int
    bid: float
    ask: float
    source_id: str


@dataclass(frozen=True)
class Candidate:
    child_id: str
    decision_time_ms: int
    bar_start_ms: int
    direction: str
    stop: float
    target: Optional[float]
    target_known_at_ms: Optional[int]
    valid_at_decision: bool
    target_already_reached: bool = False
    invalidated_at_ms: Optional[int] = None
    parent_zone_id: str = ""
    target_bos_id: str = ""


@dataclass(frozen=True)
class Pivot:
    known_at_ms: int
    kind: str
    price: float
    pivot_time_ms: int
    source_id: str


@dataclass(frozen=True)
class Config:
    entry_latency_ms: int = 1000
    pending_expiry_ms: int = MINUTE
    max_quote_gap_ms: int = MINUTE
    commission_per_million: float = 52.5
    slippage_points_per_side: float = 0.0
    rollover_deadline_hour_utc: int = 20
    entry_resume_hour_utc: int = 22
    flatten_buffer_ms: int = MINUTE
    study_start_ms: Optional[int] = None
    study_end_ms: Optional[int] = None


@dataclass(frozen=True)
class Policy:
    name: str
    break_even_after_r: Optional[float] = None
    pivot_trail_after_r: Optional[float] = None
    max_hold_minutes: Optional[int] = None
    fixed_r_control: Optional[float] = None


DEFAULT_POLICIES = (
    Policy("original_target"),
    Policy("break_even_after_1R", break_even_after_r=1.0),
    Policy("confirmed_m1_swing_trail_after_1R", pivot_trail_after_r=1.0),
    Policy("time_exit_60m", max_hold_minutes=60),
    Policy("time_exit_240m", max_hold_minutes=240),
    Policy("fixed_1R_control", fixed_r_control=1.0),
    Policy("fixed_2R_control", fixed_r_control=2.0),
)


def _integer(value, label, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(label + " must be a nonnegative integer" if not positive
                         else label + " must be a positive integer")


def _number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + " must be finite")
    if value < 0 or (positive and value == 0):
        raise ValueError(label + " must be positive" if positive
                         else label + " must be nonnegative")


def _identity(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(label + " must be a nonempty string")


def _validate_config(config):
    for name in ("entry_latency_ms", "pending_expiry_ms", "max_quote_gap_ms", "flatten_buffer_ms"):
        _integer(getattr(config, name), name, positive=name != "entry_latency_ms")
    if config.entry_latency_ms >= config.pending_expiry_ms:
        raise ValueError("Entry latency must be shorter than pending expiry")
    for name in ("commission_per_million", "slippage_points_per_side"):
        _number(getattr(config, name), name)
    if not (type(config.rollover_deadline_hour_utc) is int and
            type(config.entry_resume_hour_utc) is int and
            0 < config.rollover_deadline_hour_utc < config.entry_resume_hour_utc < 24):
        raise ValueError("Invalid UTC rollover/resumption hours")
    if config.flatten_buffer_ms >= config.rollover_deadline_hour_utc * HOUR:
        raise ValueError("Flatten buffer must remain inside the preceding UTC day")
    for name in ("study_start_ms", "study_end_ms"):
        value = getattr(config, name)
        if value is not None:
            _integer(value, name)
    if config.study_end_ms is not None:
        if config.study_end_ms < config.flatten_buffer_ms:
            raise ValueError("Study end is shorter than the flatten buffer")
        if config.study_start_ms is not None and config.study_end_ms <= config.study_start_ms:
            raise ValueError("Study end must follow study start")


def _validate_candidate(candidate):
    if not isinstance(candidate, Candidate):
        raise ValueError("Candidates must be Candidate records")
    _identity(candidate.child_id, "child_id")
    for name in ("decision_time_ms", "bar_start_ms"):
        _integer(getattr(candidate, name), name)
    if candidate.bar_start_ms % MINUTE or candidate.decision_time_ms != candidate.bar_start_ms + MINUTE:
        raise ValueError("Candidate must describe one completed native UTC minute")
    if candidate.direction not in ("long", "short"):
        raise ValueError("Candidate direction must be long or short")
    _number(candidate.stop, "stop", positive=True)
    if candidate.target is not None:
        _number(candidate.target, "target", positive=True)
    for name in ("target_known_at_ms", "invalidated_at_ms"):
        value = getattr(candidate, name)
        if value is not None:
            _integer(value, name)
    if type(candidate.valid_at_decision) is not bool or type(candidate.target_already_reached) is not bool:
        raise ValueError("Candidate validity/reached fields must be explicit booleans")


def _validate_pivot(pivot):
    if not isinstance(pivot, Pivot):
        raise ValueError("Pivots must be Pivot records")
    for name in ("known_at_ms", "pivot_time_ms"):
        _integer(getattr(pivot, name), name)
    if (pivot.known_at_ms % MINUTE or pivot.pivot_time_ms % MINUTE or
            pivot.known_at_ms < pivot.pivot_time_ms + MINUTE):
        raise ValueError("Pivot must use completed native UTC minute clocks")
    if pivot.kind not in ("low", "high"):
        raise ValueError("Pivot kind must be low or high")
    _number(pivot.price, "pivot price", positive=True)
    _identity(pivot.source_id, "pivot source_id")


def _validate_policy(policy):
    if not isinstance(policy, Policy):
        raise ValueError("Policies must be Policy records")
    _identity(policy.name, "policy name")
    for name in ("break_even_after_r", "pivot_trail_after_r", "fixed_r_control"):
        value = getattr(policy, name)
        if value is not None:
            _number(value, name, positive=True)
    if policy.break_even_after_r is not None and policy.pivot_trail_after_r is not None:
        raise ValueError("Choose one stop-ratchet hypothesis per policy")
    if policy.max_hold_minutes is not None:
        _integer(policy.max_hold_minutes, "max_hold_minutes", positive=True)


def _quote_dict(quote, index):
    return dict(asdict(quote), record_index=index)


def _entry_window_reason(timestamp, config):
    if config.study_start_ms is not None and timestamp < config.study_start_ms:
        return "BEFORE_STUDY_START"
    if config.study_end_ms is not None and timestamp >= config.study_end_ms - config.flatten_buffer_ms:
        return "STUDY_END_ENTRY_CUTOFF"
    clock = timestamp % DAY
    cutoff = config.rollover_deadline_hour_utc * HOUR - config.flatten_buffer_ms
    if cutoff <= clock < config.entry_resume_hour_utc * HOUR:
        return "DAILY_ROLLOVER_ENTRY_CUTOFF"
    return None


def _flatten_deadline(timestamp, config):
    day = timestamp // DAY * DAY
    rollover = day + config.rollover_deadline_hour_utc * HOUR
    if timestamp >= rollover:
        rollover += DAY
    if config.study_end_ms is not None and config.study_end_ms <= rollover:
        return config.study_end_ms, "STUDY_END_FLATTEN"
    return rollover, "DAILY_ROLLOVER_FLATTEN"


class _Simulation:
    def __init__(self, policy, config):
        self.policy, self.config = policy, config
        self.pending = None
        self.position = None
        self.trades = []
        self.decisions = []
        self.last_exit_minute = None
        self.halted = False

    def _decision(self, candidate, status, at, **extra):
        row = dict(child_id=candidate.child_id, decision_time_ms=candidate.decision_time_ms,
                   status=status, recorded_at_ms=at, **extra)
        self.decisions.append(row)
        return row

    def receive_candidate(self, candidate):
        t = candidate.decision_time_ms
        # Timers and already-known revocations advance even without a new quote.
        # An expired prior order must not block the next minute's review merely
        # because both reviews are encountered before the next raw tick.
        self._advance_pending_clock(t)
        reason = None
        if self.halted:
            reason = "HALTED_AFTER_CENSORED_EXPOSURE"
        elif not candidate.valid_at_decision or (candidate.invalidated_at_ms is not None and candidate.invalidated_at_ms <= t):
            reason = "PERMISSION_INVALID_AT_DECISION"
        elif candidate.target is None or candidate.target_known_at_ms is None:
            reason = "ORIGINAL_TARGET_UNKNOWN"
        elif candidate.target_known_at_ms > candidate.bar_start_ms:
            reason = "ORIGINAL_TARGET_NOT_KNOWN_AT_BAR_OPEN"
        elif candidate.target_already_reached:
            reason = "ORIGINAL_TARGET_ALREADY_REACHED"
        elif ((candidate.direction == "long" and candidate.stop >= candidate.target) or
              (candidate.direction == "short" and candidate.stop <= candidate.target)):
            reason = "INVALID_ORIGINAL_STOP_TARGET_ORDER"
        else:
            reason = _entry_window_reason(t, self.config)
        if reason is None:
            if self.position is not None:
                reason = "POSITION_ALREADY_OPEN"
            elif self.pending is not None:
                reason = "EARLIER_PENDING_CANDIDATE"
            elif self.last_exit_minute == t // MINUTE:
                reason = "SAME_MINUTE_REENTRY_BLOCKED"
        if reason:
            self._decision(candidate, reason, t)
        else:
            row = self._decision(candidate, "PENDING", t)
            self.pending = (candidate, row)

    def _cancel_pending(self, reason, at):
        candidate, row = self.pending
        row.update(status=reason, recorded_at_ms=at)
        self.pending = None

    def _advance_pending_clock(self, at):
        if self.pending is None:
            return
        candidate, _ = self.pending
        expires = candidate.decision_time_ms + self.config.pending_expiry_ms
        invalid_at = candidate.invalidated_at_ms
        if invalid_at is not None and invalid_at <= at and invalid_at < expires:
            self._cancel_pending("PERMISSION_REVOKED_BEFORE_ENTRY", invalid_at)
        elif at >= expires:
            self._cancel_pending("PENDING_ENTRY_EXPIRED", expires)

    def receive_pivot(self, pivot):
        p = self.position
        if p is None or self.policy.pivot_trail_after_r is None:
            return
        wanted = "low" if p["direction"] == "long" else "high"
        # A minute that began before entry cannot establish a wholly post-entry
        # swing occurrence; late confirmation alone is insufficient.
        if (pivot.kind != wanted or pivot.pivot_time_ms < p["entry_time_ms"] or
                pivot.known_at_ms <= p["entry_time_ms"]):
            return
        p["latest_pivot"] = pivot
        if p["armed_at_ms"] is not None:
            self._queue_stop(pivot.price, max(pivot.known_at_ms, p["armed_at_ms"]),
                             "CONFIRMED_M1_SWING", pivot.source_id)

    def _queue_stop(self, price, after, reason, source_id):
        p = self.position
        current = p["pending_stops"][-1]["price"] if p["pending_stops"] else p["stop"]
        tighter = price > current if p["direction"] == "long" else price < current
        if tighter:
            p["pending_stops"].append(dict(price=price, activation_after_ms=after,
                                           reason=reason, source_id=source_id))

    def _entry(self, candidate, row, quote, index):
        direction = candidate.direction
        price = quote.ask if direction == "long" else quote.bid
        risk = abs(price - candidate.stop)
        sign = 1 if direction == "long" else -1
        target = candidate.target
        if self.policy.fixed_r_control is not None:
            target = price + sign * self.policy.fixed_r_control * risk
        deadline, flatten_reason = _flatten_deadline(quote.timestamp_ms, self.config)
        self.position = dict(
            candidate=candidate, child_id=candidate.child_id, direction=direction,
            entry_price=price, entry_time_ms=quote.timestamp_ms, entry_quote=_quote_dict(quote, index),
            initial_stop=candidate.stop, initial_risk_points=risk, original_target=candidate.target,
            target=target, stop=candidate.stop, pending_stops=[], latest_pivot=None,
            armed_at_ms=None, deadline_ms=deadline, flatten_reason=flatten_reason,
            mfe_points=0.0, mae_points=0.0, mfe_at_ms=None, mae_at_ms=None,
            last_observed_quote=quote, last_observed_quote_index=index, last_observed_mark_points=None,
            stop_history=[dict(price=candidate.stop, reason="INITIAL_CHILD_FAR_EDGE",
                               known_at_ms=candidate.decision_time_ms,
                               active_quote_timestamp_ms=quote.timestamp_ms,
                               active_quote_source_id=quote.source_id)])
        self._mark(quote, index)
        row.update(status="FILLED", recorded_at_ms=quote.timestamp_ms,
                   entry_quote_source_id=quote.source_id, entry_quote_record_index=index)
        self.pending = None

    def _mark(self, quote, index):
        p = self.position
        liquidation = quote.bid if p["direction"] == "long" else quote.ask
        gross = (liquidation - p["entry_price"]) * (1 if p["direction"] == "long" else -1)
        p["last_observed_quote"] = quote
        p["last_observed_quote_index"] = index
        p["last_observed_mark_points"] = gross
        if gross > p["mfe_points"]:
            p["mfe_points"], p["mfe_at_ms"] = gross, quote.timestamp_ms
        if -gross > p["mae_points"]:
            p["mae_points"], p["mae_at_ms"] = -gross, quote.timestamp_ms
        return liquidation, gross

    def _trade_base(self):
        p = self.position
        c = p["candidate"]
        return dict(
            child_id=c.child_id, parent_zone_id=c.parent_zone_id, target_bos_id=c.target_bos_id,
            direction=p["direction"], decision_time_ms=c.decision_time_ms,
            target_known_at_ms=c.target_known_at_ms, entry_time_ms=p["entry_time_ms"],
            entry_price=p["entry_price"], entry_quote=p["entry_quote"],
            initial_stop=p["initial_stop"], initial_risk_points=p["initial_risk_points"],
            original_target=p["original_target"], exit_target=p["target"], final_stop=p["stop"],
            stop_history=p["stop_history"], mfe_points=p["mfe_points"], mae_points=p["mae_points"],
            mfe_r=p["mfe_points"] / p["initial_risk_points"],
            mae_r=p["mae_points"] / p["initial_risk_points"],
            mfe_at_ms=p["mfe_at_ms"], mae_at_ms=p["mae_at_ms"],
            quote_execution_model="MARKET_IF_TOUCHED_OBSERVED_QUOTES_NOT_BROKER_FILLS",
            execution_allowed=False, current_native_approval=False)

    def _close(self, quote, index, reason, liquidation, gross):
        p = self.position
        fees = (p["entry_price"] + liquidation) * self.config.commission_per_million / 1_000_000
        slippage = 2 * self.config.slippage_points_per_side
        net = gross - fees - slippage
        row = self._trade_base()
        row.update(status="CLOSED", exit_reason=reason, exit_time_ms=quote.timestamp_ms,
                   exit_price=liquidation, exit_quote=_quote_dict(quote, index),
                   holding_ms=quote.timestamp_ms - p["entry_time_ms"],
                   gross_points=gross, commission_proxy_points=fees,
                   slippage_scenario_points=slippage, net_points=net,
                   gross_r=gross / p["initial_risk_points"], net_r=net / p["initial_risk_points"],
                   censored=False, execution_coverage_eligible=True)
        self.trades.append(row)
        self.position = None
        self.last_exit_minute = quote.timestamp_ms // MINUTE

    def _censor(self, reason, at):
        p = self.position
        row = self._trade_base()
        row.update(status="CENSORED", exit_reason=reason, censored_at_ms=at,
                   exit_time_ms=None, exit_price=None, exit_quote=None, holding_ms=None,
                   gross_points=None, commission_proxy_points=None, slippage_scenario_points=None,
                   net_points=None, gross_r=None, net_r=None, censored=True,
                   execution_coverage_eligible=False, observed_excursions_are_partial=True,
                   last_observed_quote=_quote_dict(p["last_observed_quote"], p["last_observed_quote_index"]),
                   last_observed_unrealized_gross_points=p["last_observed_mark_points"],
                   known_entry_commission_proxy_points=p["entry_price"] * self.config.commission_per_million / 1_000_000,
                   known_entry_slippage_scenario_points=self.config.slippage_points_per_side)
        self.trades.append(row)
        self.position = None
        self.halted = True

    def on_quote(self, quote, index, previous):
        t = quote.timestamp_ms
        p = self.position
        if p is not None:
            if t >= p["deadline_ms"]:
                self._censor(p["flatten_reason"] + "_DEADLINE_MISSED", p["deadline_ms"])
            elif previous is not None and t - previous.timestamp_ms > self.config.max_quote_gap_ms:
                self._censor("QUOTE_GAP_WITH_OPEN_EXPOSURE", previous.timestamp_ms + self.config.max_quote_gap_ms)
            else:
                while p["pending_stops"] and t > p["pending_stops"][0]["activation_after_ms"]:
                    pending_stop = p["pending_stops"].pop(0)
                    p["stop"] = pending_stop["price"]
                    p["stop_history"].append(dict(pending_stop,
                        active_quote_timestamp_ms=t, active_quote_source_id=quote.source_id))
                liquidation, gross = self._mark(quote, index)
                long = p["direction"] == "long"
                stop_hit = liquidation <= p["stop"] if long else liquidation >= p["stop"]
                target_hit = liquidation >= p["target"] if long else liquidation <= p["target"]
                reason = "STOP_MARKET_IF_TOUCHED" if stop_hit else "TARGET_MARKET_IF_TOUCHED" if target_hit else None
                if reason is None and t > p["deadline_ms"] - self.config.flatten_buffer_ms:
                    reason = p["flatten_reason"]
                if (reason is None and self.policy.max_hold_minutes is not None and
                        t >= p["entry_time_ms"] + self.policy.max_hold_minutes * MINUTE):
                    reason = "MAX_HOLD_TIME"
                if reason is not None:
                    self._close(quote, index, reason, liquidation, gross)
                else:
                    threshold = self.policy.break_even_after_r
                    if threshold is None:
                        threshold = self.policy.pivot_trail_after_r
                    if threshold is not None and p["armed_at_ms"] is None and gross >= threshold * p["initial_risk_points"]:
                        p["armed_at_ms"] = t
                        if self.policy.break_even_after_r is not None:
                            self._queue_stop(p["entry_price"], t, "PRICE_BREAK_EVEN_NOT_NET_COST_BREAK_EVEN", quote.source_id)
                        elif p["latest_pivot"] is not None:
                            pivot = p["latest_pivot"]
                            self._queue_stop(pivot.price, max(t, pivot.known_at_ms), "CONFIRMED_M1_SWING", pivot.source_id)
        self._advance_pending_clock(t)
        if self.pending is None:
            return
        candidate, row = self.pending
        reason = _entry_window_reason(t, self.config)
        if reason is not None:
            self._cancel_pending(reason, t)
            return
        # Structural target exhaustion is BID-based for both directions, as in
        # the frozen engine. Short executable TP still uses ASK after entry.
        long = candidate.direction == "long"
        target_reached = quote.bid >= candidate.target if long else quote.bid <= candidate.target
        stop_hit = quote.bid <= candidate.stop if long else quote.ask >= candidate.stop
        if target_reached:
            self._cancel_pending("ORIGINAL_TARGET_REACHED_BEFORE_ENTRY", t)
        elif stop_hit:
            self._cancel_pending("STOP_REACHED_BEFORE_ENTRY", t)
        elif t > candidate.decision_time_ms + self.config.entry_latency_ms:
            price = quote.ask if long else quote.bid
            valid = candidate.stop < price < candidate.target if long else candidate.target < price < candidate.stop
            if not valid:
                self._cancel_pending("EXECUTABLE_ENTRY_NOT_BETWEEN_STOP_AND_ORIGINAL_TARGET", t)
            elif self.last_exit_minute == t // MINUTE:
                self._cancel_pending("SAME_MINUTE_REENTRY_BLOCKED", t)
            else:
                self._entry(candidate, row, quote, index)

    def finish(self, last_quote):
        if self.position is not None:
            self._censor("END_OF_QUOTES_WITH_OPEN_EXPOSURE", last_quote.timestamp_ms)
        if self.pending is not None:
            candidate, _ = self.pending
            self._cancel_pending("NO_ELIGIBLE_QUOTE_BEFORE_END", last_quote.timestamp_ms if last_quote else candidate.decision_time_ms)
        closed = [trade for trade in self.trades if trade["status"] == "CLOSED"]
        censored = len(self.trades) - len(closed)
        net = [trade["net_points"] for trade in closed]
        wins = sum(x > 0 for x in net)
        losses = sum(x < 0 for x in net)
        positive = sum(x for x in net if x > 0)
        negative = -sum(x for x in net if x < 0)
        cumulative = peak = max_drawdown = 0.0
        for value in net:
            cumulative += value
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
        # These predicates describe observed execution coverage and retention
        # of the original target. They establish neither sample sufficiency nor
        # statistical edge, teacher fidelity, or current-native approval.
        coverage_eligible = bool(closed) and not censored
        summary = dict(
            completed_trades=len(closed), censored_trades=censored, entered_trades=len(self.trades),
            candidate_count=len(self.decisions), decision_counts=dict(Counter(x["status"] for x in self.decisions)),
            wins=wins, losses=losses, flat_trades=len(closed) - wins - losses,
            gross_points=sum(x["gross_points"] for x in closed),
            commission_proxy_points=sum(x["commission_proxy_points"] for x in closed),
            slippage_scenario_points=sum(x["slippage_scenario_points"] for x in closed),
            net_points=sum(net), net_r_sum=sum(x["net_r"] for x in closed),
            win_rate=wins / len(closed) if closed else None,
            profit_factor=positive / negative if negative else None,
            profit_factor_status="DEFINED" if negative else "NO_LOSSES" if closed else "NO_TRADES",
            max_closed_trade_drawdown_points=max_drawdown,
            execution_coverage_eligible=coverage_eligible,
            rule_preserving_exit_candidate=coverage_eligible and self.policy.fixed_r_control is None,
            fixed_r_control=self.policy.fixed_r_control is not None,
            halted_after_censoring=self.halted, genuine_oos=False, profitability_established=False,
            status="INELIGIBLE_CENSORED_EXPOSURE" if censored else "DEVELOPMENT_DIAGNOSTICS_ONLY" if closed else "NO_COMPLETED_TRADES")
        return dict(policy=asdict(self.policy), summary=summary, trades=self.trades, decisions=self.decisions)


def simulate_many(quotes: Iterable[Quote], candidates: Iterable[Candidate],
                  pivots: Iterable[Pivot] = (), policies: Iterable[Policy] = DEFAULT_POLICIES,
                  config: Optional[Config] = None):
    """Stream actual quotes once through independent one-position policies.

    Candidate joins and final same-cutoff permission come from the caller's
    causal engine adapter. This function rechecks all supplied clocks and entry
    prices. Same-time candidates use ascending stable child_id; quotes retain
    input/provider record order, but stop activation requires a later timestamp.
    A censored position permanently halts that policy: no silent recovery fill.
    """
    config = config or Config()
    if not isinstance(config, Config):
        raise ValueError("config must be Config")
    _validate_config(config)
    candidates, pivots, policies = list(candidates), list(pivots), list(policies)
    for candidate in candidates:
        _validate_candidate(candidate)
    for pivot in pivots:
        _validate_pivot(pivot)
    for policy in policies:
        _validate_policy(policy)
    if len({c.child_id for c in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate child_id")
    if not policies or len({p.name for p in policies}) != len(policies):
        raise ValueError("Policies must have unique names and be nonempty")
    candidates.sort(key=lambda c: (c.decision_time_ms, c.child_id))
    pivots.sort(key=lambda p: (p.known_at_ms, p.source_id, p.kind))
    states = [_Simulation(policy, config) for policy in policies]
    candidate_index = pivot_index = quote_count = 0
    previous = last_quote = None
    for index, quote in enumerate(quotes):
        if not isinstance(quote, Quote):
            raise ValueError("Quotes must be Quote records")
        _integer(quote.timestamp_ms, "quote timestamp")
        _number(quote.bid, "bid", positive=True)
        _number(quote.ask, "ask", positive=True)
        _identity(quote.source_id, "quote source_id")
        if quote.ask < quote.bid:
            raise ValueError("Crossed bid/ask quote")
        if previous is not None and quote.timestamp_ms < previous.timestamp_ms:
            raise ValueError("Quotes must retain chronological provider order")
        while candidate_index < len(candidates) and candidates[candidate_index].decision_time_ms <= quote.timestamp_ms:
            for state in states:
                state.receive_candidate(candidates[candidate_index])
            candidate_index += 1
        while pivot_index < len(pivots) and pivots[pivot_index].known_at_ms <= quote.timestamp_ms:
            for state in states:
                state.receive_pivot(pivots[pivot_index])
            pivot_index += 1
        for state in states:
            # Idle policies have no quote-driven state to advance. Every quote
            # still passes validation and updates the global previous record.
            if state.position is not None or state.pending is not None:
                state.on_quote(quote, index, previous)
        quote_count += 1
        previous = last_quote = quote
    # A missing quote is not an entry. Retain every unprocessed review instead
    # of silently dropping the tail of the signal population.
    for candidate in candidates[candidate_index:]:
        for state in states:
            state.receive_candidate(candidate)
            if state.pending is not None and state.pending[0].child_id == candidate.child_id:
                state._cancel_pending("NO_POST_DECISION_QUOTES", candidate.decision_time_ms)
    results = {state.policy.name: state.finish(last_quote) for state in states}
    return dict(schema="ASTRA6_QUOTE_EXECUTION_RESEARCH_V1", config=asdict(config),
                execution_allowed=False, current_native_approval=False, genuine_oos=False,
                quote_count=quote_count, candidate_count=len(candidates), pivot_count=len(pivots),
                pricing="OBSERVED_QUOTES_SIMULATED_EXECUTION_NOT_BROKER_FILLS",
                cost_scope="COMMISSION_NOTIONAL_PROXY_AND_DECLARED_POINT_SLIPPAGE_NOT_ACCOUNT_PROFIT",
                policies=results)
