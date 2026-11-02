from dr_charts.tradovate_gateway.service import ReconciliationError
from tests.drcharts_gateway_common import GatewayCase


class EventOrderingAgent(GatewayCase):
    def test_fill_before_order_is_buffered_not_lost(self):
        self.service.on_entity("contract", "Created", {"id": 10, "name": "NQZ6"})
        self.service.on_entity("fill", "Created", self.fill(1))
        self.assertIn(1, self.service.pending_fills)
        self.service.on_entity("order", "Created", {"id": 100, "accountId": 1, "contractId": 10})
        self.assertNotIn(1, self.service.pending_fills)
        self.assertEqual(len(self.journal.pending()), 1)

    def test_contract_mismatch_fails_closed(self):
        self.service.on_entity("contract", "Created", {"id": 10, "name": "NQZ6"})
        self.service.on_entity("order", "Created", {"id": 100, "accountId": 1, "contractId": 10})
        with self.assertRaises(ReconciliationError):
            self.service.on_entity("fill", "Created", self.fill(2, contract_id=11))
