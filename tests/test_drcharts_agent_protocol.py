from dr_charts.tradovate_gateway.config import Settings
from dr_charts.tradovate_gateway.tradovate import _request, _receive_until_response, decode_frame
from tests.drcharts_gateway_common import GatewayCase


class ProtocolAgent(GatewayCase):
    def test_authorize_uses_raw_token_body(self):
        self.assertEqual(_request("authorize", 7, "secret"), "authorize\n7\n\nsecret")

    def test_decodes_open_heartbeat_close_and_array(self):
        self.assertEqual(decode_frame("o"), ("o", []))
        self.assertEqual(decode_frame("h"), ("h", []))
        self.assertEqual(decode_frame('a[{"s":200,"i":1}]')[1][0]["s"], 200)
        self.assertEqual(decode_frame('c[3000,"bye"]')[0], "close")

    def test_rejects_non_discord_webhook_host(self):
        s = Settings("demo", "t", 1, frozenset({1}), "https://evil.example/api/webhooks/x", "", "", self.db)
        with self.assertRaises(ValueError):
            s.validate_runtime()

    def test_events_racing_response_are_buffered(self):
        class FakeWS:
            def __init__(self):
                self.frames = [
                    'a[{"e":"props","d":{"entityType":"order","eventType":"Created","entity":{"id":9}}}]',
                    'a[{"s":200,"i":2,"d":{"ok":true}}]',
                ]
                self.sent = []
            def recv(self):
                return self.frames.pop(0)
            def send(self, value):
                self.sent.append(value)
        response, buffered = _receive_until_response(FakeWS(), 2, lambda: None)
        self.assertEqual(response["s"], 200)
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0]["e"], "props")
