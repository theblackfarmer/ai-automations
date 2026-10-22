"""Synthetic dispatch equivalence; never market-profit evidence."""
import inspect
import random
import unittest
from unittest.mock import patch
import quote_execution as q
from test_quote_execution import candidate, quote, T


def reference():
    source = inspect.getsource(q.simulate_many)
    guarded = ('            if state.position is not None or state.pending is not None:\n'
               '                state.on_quote(quote, index, previous)')
    if source.count(guarded) != 1:
        raise AssertionError('Dispatch changed; review reference')
    namespace = dict(vars(q))
    exec(compile(source.replace(guarded, '            state.on_quote(quote, index, previous)'),
                 '<original-unconditional-dispatch>', 'exec'), namespace)
    return namespace['simulate_many']


class IdleDispatchTests(unittest.TestCase):
    def setUp(self):
        self.original = reference()

    def equal_results(self, quotes, candidates, pivots=(), config=None):
        self.assertEqual(q.simulate_many(iter(quotes), candidates, pivots, config=config),
                         self.original(iter(quotes), candidates, pivots, config=config))

    def test_rich_paths_match_original(self):
        quotes = [quote(-120000), quote(-60000), quote(500), quote(1000),
                  quote(1001), quote(2000,111,112), quote(2000,99,100),
                  quote(3000,111,112), quote(60000,112,113), quote(120000,114,115),
                  quote(180000,116,117), quote(180001,109,110), quote(181000,121,122),
                  quote(240000), quote(241001), quote(241002), quote(302003), quote(420000)]
        candidates = [candidate(), candidate('tie'), candidate('next',time=T+240000),
                      candidate('halted',time=T+420000), candidate('tail',time=T+600000)]
        pivots = [q.Pivot(T+180000,'low',110,T+120000,'synthetic-pivot')]
        self.equal_results(quotes,candidates,pivots)

    def test_randomized_long_short_revocation_and_expiry_match_original(self):
        for seed in range(24):
            with self.subTest(seed=seed):
                rng=random.Random(seed)
                candidates=[]
                for minute in range(0,12,2):
                    t=T+minute*q.MINUTE
                    long=minute%4==0
                    candidates.append(candidate(str(minute),time=t,
                        direction='long' if long else 'short',stop=90 if long else 110,
                        target=120 if long else 80,
                        invalidated_at_ms=t+1500 if seed%3==0 else None))
                quotes=[quote(-120000)]
                for minute in range(12):
                    if rng.random()<.25:
                        continue
                    for offset in (0,500,1000,1001,1001,30000):
                        bid=rng.choice((79,89,99,101,111,121))
                        quotes.append(quote(minute*q.MINUTE+offset,bid,bid+1))
                self.equal_results(quotes,candidates)

    def test_rollover_study_end_empty_and_idle_match_original(self):
        for t in (20*q.HOUR-q.MINUTE,23*q.HOUR):
            config=q.Config(study_end_ms=t+q.MINUTE if t==23*q.HOUR else None)
            self.equal_results([quote(-2000,time=t),quote(1001,time=t),quote(60000,time=t)],
                               [candidate(time=t)],config=config)
        self.equal_results([],[candidate()])
        self.equal_results([quote(-120000),quote(1000)],[])

    def test_malformed_idle_quotes_still_fail_identically(self):
        streams=[[object()],[quote(0,101,100)],[quote(0,float('nan'),100)],
                 [quote(0),q.Quote(T+1,99,100,'')],[quote(1000),quote(0)]]
        for stream in streams:
            with self.subTest(stream=stream):
                messages=[]
                for run in (self.original,q.simulate_many):
                    with self.assertRaises(ValueError) as caught:
                        run(iter(stream),[])
                    messages.append(str(caught.exception))
                self.assertEqual(*messages)

    def test_idle_calls_removed_and_all_quotes_retained(self):
        quotes=[quote(i) for i in range(100)]
        original=q._Simulation.on_quote
        calls=[]
        def counted(state,*args):
            calls.append(state.policy.name)
            return original(state,*args)
        with patch.object(q._Simulation,'on_quote',counted):
            expected=self.original(quotes,[])
            self.assertEqual(len(calls),100*len(q.DEFAULT_POLICIES))
            calls.clear()
            actual=q.simulate_many(quotes,[])
            self.assertEqual(calls,[])
        self.assertEqual(actual,expected)
        self.assertEqual(actual['quote_count'],100)


if __name__=='__main__':
    unittest.main()
