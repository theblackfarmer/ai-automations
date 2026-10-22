import unittest
from exit_comparison_v1 import evaluate,summarize
class ExitComparisonTests(unittest.TestCase):
    def signal(self):return [dict(id='x',entry_epoch=0,side='LONG',entry=100,stop=99)]
    def test_target(self):
        r=evaluate({60:(100,102,100,101)},self.signal(),(1.0,),1)
        self.assertEqual(r[0]['reason'],'TARGET')
        self.assertEqual(r[0]['realized_r'],1)
    def test_both_hits_unresolved(self):
        r=evaluate({60:(100,102,98,100)},self.signal(),(1.0,),1)
        self.assertEqual(r[0]['status'],'UNRESOLVED')
        self.assertEqual(r[0]['reason'],'AMBIGUOUS_INTRABAR_ORDER')
    def test_missing_bar_unresolved(self):
        r=evaluate({120:(100,102,99,101)},self.signal(),(1.0,),2)
        self.assertEqual(r[0]['status'],'UNRESOLVED')
    def test_cost_deducted(self):
        r=evaluate({60:(100,102,100,101)},self.signal(),(1.0,),1,0.2)
        self.assertAlmostEqual(r[0]['realized_r'],0.8)
    def test_short(self):
        s=[dict(id='s',entry_epoch=0,side='SHORT',entry=100,stop=101)]
        r=evaluate({60:(100,100,98,99)},s,(1.0,),1)
        self.assertEqual(r[0]['reason'],'TARGET')
    def test_summary_flags_incomplete(self):
        r=evaluate({},self.signal(),(1.0,),1)
        self.assertTrue(summarize(r)[0]['not_comparable_if_unresolved'])
if __name__=='__main__':unittest.main()
