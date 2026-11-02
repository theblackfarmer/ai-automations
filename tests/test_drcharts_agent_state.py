from dr_charts.tradovate_gateway.broadcasters import BroadcasterRegistry
from dr_charts.tradovate_gateway.journal import Journal
from dr_charts.tradovate_gateway.service import RelayService
from tests.drcharts_gateway_common import CaptureBroadcaster, GatewayCase


class CrashStateAgent(GatewayCase):
    def test_processed_fill_and_pending_delivery_survive_restart(self):
        self.service.on_entity("contract", "Created", {"id": 10, "name": "NQZ6"})
        self.service.on_entity("order", "Created", {"id": 100, "accountId": 1, "contractId": 10})
        self.service.on_entity("fill", "Created", self.fill(1))
        self.assertTrue(self.journal.seen("fill:1"))
        self.assertEqual(len(self.journal.pending()), 1)

        j2 = Journal(self.db)
        j2.init()
        self.assertTrue(j2.seen("fill:1"))
        self.assertEqual(j2.get_position(1, 10), 1)
        self.assertEqual(len(j2.pending()), 1)

        cap2 = CaptureBroadcaster("capture")
        service2 = RelayService(j2, BroadcasterRegistry([cap2]), {1})
        service2.orders[100] = (1, 10)
        service2.on_entity("fill", "Created", self.fill(1))
        self.assertEqual(len(j2.pending()), 1)
