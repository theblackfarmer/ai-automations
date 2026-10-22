import unittest
from exit_comparison_v1 import evaluate,summarize
class ExitComparisonTests(unittest.TestCase):
    def signal(self):return [dict(id='x',entry_epoch=0,side='LONG',entry=100,stop=99)]
    def test_target(self):
        r=evaluate({0:(100,102,100,101)},self.signal(),(1.0,),1)
        self.assertEqual(r[0]['reason'],'TARGET')
        self.assertEqual(r[0]['realized_r'],1)
    def test_both_hits_unresolved(self):
        r=evaluate({0:(100,102,98,100)},self.signal(),(1.0,),1)
        self.assertEqual(r[0]['status'],'UNRESOLVED')
        self.assertEqual(r[0]['reason'],'AMBIGUOUS_INTRABAR_ORDER')
    def test_missing_bar_unresolved(self):
        r=evaluate({120:(100,102,99,101)},self.signal(),(1.0,),2)
        self.assertEqual(r[0]['status'],'UNRESOLVED')
    def test_cost_deducted(self):
        r=evaluate({0:(100,102,100,101)},self.signal(),(1.0,),1,0.2)
        self.assertAlmostEqual(r[0]['realized_r'],0.8)
    def test_short(self):
        s=[dict(id='s',entry_epoch=0,side='SHORT',entry=100,stop=101)]
        r=evaluate({0:(100,100,98,99)},s,(1.0,),1)
        self.assertEqual(r[0]['reason'],'TARGET')
    def test_summary_flags_incomplete(self):
        r=evaluate({},self.signal(),(1.0,),1)
        self.assertTrue(summarize(r)[0]['not_comparable_if_unresolved'])
    def test_entry_bar_stop_not_skipped(self):
        rows=evaluate({0:(100,100.5,98,99),60:(100,102,100,101)},self.signal(),(1.0,),2)
        self.assertEqual(rows[0]['reason'],'STOP')
        self.assertEqual(rows[0]['exit_epoch'],0)

    def test_missing_entry_bar_cannot_use_future(self):
        row=evaluate({60:(100,102,100,101)},self.signal(),(1.0,),2)[0]
        self.assertEqual(row['status'],'UNRESOLVED')

    def test_gap_stop_uses_worse_open(self):
        row=evaluate({0:(100,100.5,99.5,100),60:(98,100,97,99)},self.signal(),(1.0,),2)[0]
        self.assertEqual(row['reason'],'STOP_GAP')
        self.assertEqual(row['realized_r'],-2)

    def test_short_gap_stop_uses_worse_open(self):
        signal=[dict(id='s',entry_epoch=0,side='SHORT',entry=100,stop=101)]
        row=evaluate({0:(100,100.5,99.5,100),60:(103,104,102,103)},signal,(1.0,),2)[0]
        self.assertEqual(row['realized_r'],-3)

    def test_gap_target_open_precedes_later_stop_touch(self):
        row=evaluate({0:(100,100.5,99.5,100),60:(102,103,98,100)},self.signal(),(1.0,),2)[0]
        self.assertEqual(row['reason'],'TARGET')
        self.assertEqual(row['realized_r'],1)

    def test_time_stop_counts_entry_minute(self):
        row=evaluate({0:(100,100.5,99.5,100.2)},self.signal(),(1.0,),1)[0]
        self.assertEqual(row['reason'],'TIME_STOP')
        self.assertEqual(row['exit_epoch'],0)
        self.assertAlmostEqual(row['realized_r'],0.2)

    def test_missing_middle_minute_prevents_future_target(self):
        row=evaluate({0:(100,100.5,99.5,100),120:(100,102,100,101)},self.signal(),(1.0,),3)[0]
        self.assertEqual(row['status'],'UNRESOLVED')

    def test_malformed_direct_bars_rejected(self):
        for bar in [(100,float('nan'),99,100),(100,99,98,100),(100,101,99)]:
            with self.subTest(bar=bar), self.assertRaises(ValueError):
                evaluate({0:bar},self.signal(),(1.0,),1)

    def test_entry_must_match_observed_open(self):
        with self.assertRaises(ValueError):
            evaluate({0:(101,102,100,101)},self.signal(),(1.0,),1)

    def test_fractional_entry_clock_rejected(self):
        s=self.signal();s[0]['entry_epoch']=0.5
        with self.assertRaises(ValueError):evaluate({},s)

    def test_invalid_parameters_rejected_without_signals(self):
        for kwargs in [dict(max_hold=1.5),dict(max_hold=True),dict(multiples=(float('nan'),)),dict(multiples=())]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):evaluate({},[],**kwargs)

    def test_future_suffix_does_not_change_resolved_exit(self):
        bars={0:(100,102,100,101)}
        truncated=evaluate(bars,self.signal(),(1.0,),3)
        bars[60]=(100,100,90,95)
        self.assertEqual(evaluate(bars,self.signal(),(1.0,),3),truncated)

if __name__=='__main__':unittest.main()
