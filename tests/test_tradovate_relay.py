import json
import os
import tempfile
import unittest

from integrations.tradovate_relay.engine import SignalEngine
from integrations.tradovate_relay.journal import Journal
from integrations.tradovate_relay.models import Fill, SignalKind
from integrations.tradovate_relay.service import RelayService
from integrations.tradovate_relay.broadcasters import MultiBroadcaster


class Capture:
    def __init__(self):
        self.items=[]
    def send(self, signal):
        self.items.append(signal)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.j=Journal(os.path.join(self.tmp.name,"relay.sqlite3"))
        self.j.init()
        self.engine=SignalEngine(self.j)
        self.engine.hydrate_contract(10,"NQZ6")

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self, fid, side, qty, price):
        return Fill(fid,1,10,side,qty,price,"2026-10-23T18:00:00Z")

    def test_lifecycle_and_duplicate_suppression(self):
        a=self.engine.on_fill(self.fill(1,"Buy",1,25000))
        self.assertEqual(a.kind,SignalKind.ENTRY)
        self.assertEqual(a.new_position,1)
        b=self.engine.on_fill(self.fill(2,"Buy",1,25010))
        self.assertEqual(b.kind,SignalKind.ADD)
        c=self.engine.on_fill(self.fill(3,"Sell",1,25020))
        self.assertEqual(c.kind,SignalKind.REDUCE)
        d=self.engine.on_fill(self.fill(4,"Sell",1,25030))
        self.assertEqual(d.kind,SignalKind.EXIT)
        self.assertIsNone(self.engine.on_fill(self.fill(4,"Sell",1,25030)))

    def test_reverse(self):
        self.engine.on_fill(self.fill(10,"Buy",1,25000))
        s=self.engine.on_fill(self.fill(11,"Sell",2,24990))
        self.assertEqual(s.kind,SignalKind.REVERSE)
        self.assertEqual(s.new_position,-1)

    def test_outbox_retries_until_delivery(self):
        cap=Capture()
        svc=RelayService(self.j,MultiBroadcaster([cap]),{1})
        svc.orders[55]=(1,10)
        svc.on_entity("fill","Created",{"id":21,"orderId":55,"contractId":10,"action":"Buy","qty":1,"price":25000,"timestamp":"2026-10-23T18:00:00Z"})
        self.assertEqual(len(self.j.pending()),1)
        svc.drain_outbox()
        self.assertEqual(len(cap.items),1)
        self.assertEqual(len(self.j.pending()),0)

    def test_account_allowlist(self):
        cap=Capture()
        svc=RelayService(self.j,MultiBroadcaster([cap]),{1})
        svc.orders[70]=(2,10)
        svc.on_entity("fill","Created",{"id":22,"orderId":70,"contractId":10,"action":"Buy","qty":1,"price":25000,"timestamp":"2026-10-23T18:00:00Z"})
        svc.drain_outbox()
        self.assertEqual(cap.items,[])


if __name__=="__main__":
    unittest.main()
