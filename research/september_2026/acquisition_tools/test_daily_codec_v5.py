import lzma
import struct
import unittest
from datetime import datetime, timezone

import daily_codec_v5 as d


def day(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def body(records):
    raw = b"".join(struct.pack(">IIIff", *r) for r in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


class DailyCodecV5Tests(unittest.TestCase):
    def test_daily_key_zero_indexed_month(self):
        self.assertEqual(
            d.daily_key("USATECHIDXUSD", day("2026-08-03T00:00:00Z")),
            "USATECHIDXUSD/2026/07/03_ticks.bi5",
        )

    def test_decode_aggregates_bid_minutes(self):
        start = day("2026-08-03T00:00:00Z")
        b = body([
            (1_000, 100_100, 100_000, 1.0, 1.0),
            (20_000, 100_300, 100_200, 1.0, 1.0),
            (50_000, 100_050, 99_900, 1.0, 1.0),
            (61_000, 100_500, 100_400, 1.0, 1.0),
        ])
        rows, meta = d.decode_daily(b, start, 1000)
        self.assertEqual(rows, [
            (start, 100.0, 100.2, 99.9, 99.9),
            (start + 60, 100.4, 100.4, 100.4, 100.4),
        ])
        self.assertEqual(meta["tick_count"], 4)
        self.assertEqual(meta["minute_count"], 2)

    def test_reject_out_of_day_timestamp(self):
        start = day("2026-08-03T00:00:00Z")
        with self.assertRaises(d.DailyDecodeError):
            d.decode_daily(body([(86_400_000, 100100, 100000, 1.0, 1.0)]), start)

    def test_reject_decreasing_timestamp(self):
        start = day("2026-08-03T00:00:00Z")
        with self.assertRaises(d.DailyDecodeError):
            d.decode_daily(body([
                (2_000, 100100, 100000, 1.0, 1.0),
                (1_000, 100100, 100000, 1.0, 1.0),
            ]), start)

    def test_reject_crossed_quote_and_bad_volume(self):
        start = day("2026-08-03T00:00:00Z")
        for rec in [
            (1_000, 99_000, 100_000, 1.0, 1.0),
            (1_000, 100_100, 100_000, 0.0, 1.0),
        ]:
            with self.assertRaises(d.DailyDecodeError):
                d.decode_daily(body([rec]), start)

    def test_zero_byte_is_not_silently_market_closed(self):
        with self.assertRaises(d.DailyDecodeError):
            d.decode_daily(b"", day("2026-08-03T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
