"""Run with python -m unittest discover -s research -p 'test_astra_market_regime.py'."""
import unittest
from datetime import datetime, timedelta, timezone
from astra_market_regime import Bar, CausalRegime15m

class Tests(unittest.TestCase):
    def bar(self, i, p):
        t = datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15*i)
        return Bar(t, p - .2, p + 1, p - 1, p)

    def test_alignment_and_no_future_bar(self):
        m = CausalRegime15m(lookback=5, vol_lookback=8)
        for i in range(20):
            r = m.update_closed_bar(self.bar(i, 100+i))
        self.assertEqual(r.label, "TREND_UP")
        t = r.asof + timedelta(minutes=1)
        self.assertTrue(m.assess_entry(t, -1, "shadow")["execute_allowed"])
        self.assertFalse(m.assess_entry(t, -1, "gate")["execute_allowed"])
        self.assertTrue(m.assess_entry(t, 1, "gate")["execute_allowed"])
        with self.assertRaises(ValueError):
            m.assess_entry(r.asof - timedelta(minutes=1), 1)
        with self.assertRaises(ValueError):
            m.update_closed_bar(self.bar(19, 150))

    def test_warmup(self):
        m = CausalRegime15m()
        r = m.update_closed_bar(self.bar(0, 100))
        self.assertEqual(r.label, "WARMUP")
        self.assertTrue(m.assess_entry(r.asof, 1, "shadow")["execute_allowed"])
        self.assertFalse(m.assess_entry(r.asof, 1, "gate")["execute_allowed"])
if __name__ == "__main__":
    unittest.main()
