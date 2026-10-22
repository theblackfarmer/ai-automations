import unittest
from entry_review_adapter_v1 import candidates
class AdapterTests(unittest.TestCase):
    def e(self,kind='ENTRY_REVIEW',**extra):
        return dict(id='a',kind=kind,known_at=60,direction='LONG',zone_low=99.,zone_high=100.,**extra)
    def test_next_open(self):
        rows,blocked=candidates([self.e()],{60:(101.,102.,100.,101.)})
        self.assertEqual(rows[0]['entry'],101.)
        self.assertEqual(rows[0]['stop'],99.)
        self.assertFalse(blocked)
    def test_no_next_bar(self):
        rows,blocked=candidates([self.e()],{})
        self.assertEqual(rows,[])
        self.assertEqual(blocked[0]['reason'],'NEXT_OPEN_UNOBSERVED')
    def test_blocked_review_never_trades(self):
        rows,blocked=candidates([self.e('ENTRY_REVIEW_BLOCKED')],{60:(101.,102.,100.,101.)})
        self.assertFalse(rows)
        self.assertEqual(blocked[0]['reason'],'ENGINE_REVIEW_BLOCKED')
    def test_bad_risk_rejected(self):
        rows,blocked=candidates([self.e()],{60:(98.,100.,97.,99.)})
        self.assertFalse(rows)
        self.assertEqual(blocked[0]['reason'],'NONPOSITIVE_INITIAL_RISK')
if __name__=='__main__':unittest.main()
