from dr_charts.tradovate_gateway.service import ReconciliationError
from tests.drcharts_gateway_common import GatewayCase


class ReconnectAgent(GatewayCase):
    def test_first_snapshot_is_baseline_not_historical_broadcast(self):
        f1 = self.fill(1)
        self.service.on_snapshot(self.snapshot([f1], net_pos=1))
        self.assertTrue(self.journal.seen("fill:1"))
        self.assertEqual(self.journal.pending(), [])
        self.assertEqual(self.journal.get_position(1, 10), 1)

    def test_reconnect_replays_only_unseen_fill(self):
        f1 = self.fill(1)
        self.service.on_snapshot(self.snapshot([f1], net_pos=1))
        f2 = self.fill(2, price=25010)
        self.service.on_snapshot(self.snapshot([f1, f2], net_pos=2))
        pending = self.journal.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0][0], "fill:2")
        self.assertEqual(self.journal.get_position(1, 10), 2)

    def test_reconnect_position_mismatch_fails_closed(self):
        self.service.on_snapshot(self.snapshot([self.fill(1)], net_pos=1))
        with self.assertRaises(ReconciliationError):
            self.service.on_snapshot(self.snapshot([self.fill(1)], net_pos=5))

    def test_unresolved_baseline_fill_fails_closed(self):
        broken = self.snapshot([self.fill(1, order_id=999)], net_pos=0)
        with self.assertRaises(ReconciliationError):
            self.service.on_snapshot(broken)
