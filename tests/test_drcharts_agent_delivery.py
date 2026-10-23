from dr_charts.tradovate_gateway.broadcasters import BroadcasterRegistry
from dr_charts.tradovate_gateway.service import RelayService
from tests.drcharts_gateway_common import CaptureBroadcaster, GatewayCase


class DeliveryAgent(GatewayCase):
    def test_each_destination_is_acknowledged_independently(self):
        good = CaptureBroadcaster("good")
        bad = CaptureBroadcaster("bad", fail=True)
        registry = BroadcasterRegistry([good, bad])
        service = RelayService(self.journal, registry, {1})
        service.on_entity("contract", "Created", {"id": 10, "name": "NQZ6"})
        service.on_entity("order", "Created", {"id": 100, "accountId": 1, "contractId": 10})
        service.on_entity("fill", "Created", self.fill(1))
        service.drain_outbox()
        pending = self.journal.pending()
        self.assertEqual(len(good.items), 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0][1], "bad")
        bad.fail = False
        service.drain_outbox()
        self.assertEqual(len(good.items), 1)
        self.assertEqual(len(bad.items), 1)
        self.assertEqual(self.journal.pending(), [])
