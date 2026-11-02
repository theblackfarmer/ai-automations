import os
import tempfile
import unittest

from dr_charts.tradovate_gateway.broadcasters import BroadcasterRegistry
from dr_charts.tradovate_gateway.journal import Journal
from dr_charts.tradovate_gateway.service import RelayService


class CaptureBroadcaster:
    def __init__(self, name="capture", fail=False):
        self.name = name
        self.fail = fail
        self.items = []

    def send(self, signal):
        if self.fail:
            raise RuntimeError("forced failure")
        self.items.append(signal)


class GatewayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "gateway.sqlite3")
        self.journal = Journal(self.db)
        self.journal.init()
        self.capture = CaptureBroadcaster()
        self.registry = BroadcasterRegistry([self.capture])
        self.service = RelayService(self.journal, self.registry, {1})

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, fills=None, net_pos=0):
        fills = fills or []
        return {
            "contracts": [{"id": 10, "name": "NQZ6"}],
            "orders": [{"id": 100, "accountId": 1, "contractId": 10}],
            "fills": fills,
            "positions": [{"accountId": 1, "contractId": 10, "netPos": net_pos}],
        }

    @staticmethod
    def fill(fid, action="Buy", qty=1, price=25000, order_id=100, contract_id=10):
        return {
            "id": fid,
            "orderId": order_id,
            "contractId": contract_id,
            "action": action,
            "qty": qty,
            "price": price,
            "timestamp": f"2026-10-23T18:00:{fid:02d}Z",
        }
