from dr_charts.tradovate_gateway.models import Fill
from tests.drcharts_gateway_common import GatewayCase


class ValidationAgent(GatewayCase):
    def test_invalid_action_is_rejected(self):
        with self.assertRaises(ValueError):
            Fill(1, 1, 10, "Hold", 1, 25000.0, "x")

    def test_invalid_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            Fill(1, 1, 10, "Buy", 0, 25000.0, "x")

    def test_invalid_price_is_rejected(self):
        with self.assertRaises(ValueError):
            Fill(1, 1, 10, "Buy", 1, float("nan"), "x")

    def test_disallowed_account_never_broadcasts(self):
        self.service.on_entity("contract", "Created", {"id": 10, "name": "NQZ6"})
        self.service.on_entity("order", "Created", {"id": 100, "accountId": 2, "contractId": 10})
        self.service.on_entity("fill", "Created", self.fill(4))
        self.assertEqual(self.journal.pending(), [])
