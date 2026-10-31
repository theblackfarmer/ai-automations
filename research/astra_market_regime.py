"""ASTRA causal regime context. Research only; no orders.
Feed completed 15-minute OHLC bars strictly in close-time order.
Thresholds are initial hypotheses, not validated performance improvements.
"""
from collections import deque
from dataclasses import dataclass
from math import isfinite
from statistics import median

@dataclass(frozen=True)
class Bar:
    close_time: object
    open: float
    high: float
    low: float
    close: float

@dataclass(frozen=True)
class Regime:
    asof: object
    label: str
    efficiency: object
    atr: object
    atr_relative: object
    displacement: object
    note: str

class CausalRegime15m:
    def __init__(self, lookback=20, vol_lookback=60, trend_eff=0.38,
                 chop_eff=0.20, high_vol_ratio=1.8):
        if lookback < 3 or vol_lookback < lookback or not (0 < chop_eff < trend_eff < 1):
            raise ValueError("invalid regime configuration")
        self.lookback, self.vol_lookback = lookback, vol_lookback
        self.trend_eff, self.chop_eff = trend_eff, chop_eff
        self.high_vol_ratio = high_vol_ratio
        self.bars = deque(maxlen=vol_lookback + lookback + 2)
        self.last_close_time = None
        self.latest = None

    def update_closed_bar(self, bar):
        v = (bar.open, bar.high, bar.low, bar.close)
        if not all(isfinite(x) for x in v) or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise ValueError("invalid OHLC")
        if self.last_close_time is not None and not bar.close_time > self.last_close_time:
            raise ValueError("bar timestamps must increase")
        self.bars.append(bar)
        self.last_close_time = bar.close_time
        b = list(self.bars)
        n = self.lookback
        if len(b) < self.vol_lookback + n + 1:
            self.latest = Regime(bar.close_time, "WARMUP", None, None, None, None, "insufficient closed bars")
            return self.latest
        closes = [x.close for x in b]
        changes = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        travel = sum(changes[-n:])
        net = closes[-1] - closes[-n-1]
        efficiency = abs(net) / travel if travel else 0.0
        tr = [max(b[i].high-b[i].low, abs(b[i].high-b[i-1].close),
                  abs(b[i].low-b[i-1].close)) for i in range(1, len(b))]
        atr = sum(tr[-n:]) / n
        prior = tr[-(self.vol_lookback+n):-n]
        baseline = median(prior) if prior else 0
        ratio = atr / baseline if baseline > 0 else None
        displacement = net / atr if atr else 0.0
        if ratio is not None and ratio > self.high_vol_ratio:
            label, note = "TRANSITION", "volatility expansion"
        elif efficiency >= self.trend_eff:
            label, note = ("TREND_UP" if net > 0 else "TREND_DOWN"), "efficient directional movement"
        elif efficiency <= self.chop_eff:
            label, note = "RANGE", "low directional efficiency"
        else:
            label, note = "TRANSITION", "mixed structure"
        self.latest = Regime(bar.close_time, label, efficiency, atr, ratio, displacement, note)
        return self.latest

    def assess_entry(self, decision_time, direction, mode="shadow"):
        """direction +1=long/-1=short; gate blocks nonaligned regimes.
        Shadow mode never affects existing ICC execution decisions.
        Caller preserves its own 15m BOS, 1m+5m, S/D, stops and exits.
        """
        if mode not in ("shadow", "gate") or direction not in (-1, 1):
            raise ValueError("invalid mode or direction")
        r = self.latest
        if r is None or r.asof > decision_time:
            raise ValueError("no causally available completed regime")
        aligned = (r.label == "TREND_UP" and direction == 1) or (r.label == "TREND_DOWN" and direction == -1)
        return {"regime_at": r.asof, "entry_at": decision_time, "regime": r.label,
                "direction": direction, "aligned": aligned,
                "reason": "aligned" if aligned else "not_aligned:" + r.label,
                "would_allow": aligned,
                "execute_allowed": aligned if mode == "gate" else True,
                "mode": mode, "efficiency": r.efficiency, "atr_relative": r.atr_relative}
