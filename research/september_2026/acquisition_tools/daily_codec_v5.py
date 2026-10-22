"""ASTRA 6 Dukascopy daily BI5 decoder (V5).

Implements Dukascopy's current documented daily bucket layout:
  SYMBOL/YEAR/MONTH/DAY_ticks.bi5
Tick timestamps are milliseconds from UTC day start. The decoder validates the
entire file before returning BID minute OHLC rows. It performs no network I/O.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import lzma
import math
import struct
from datetime import datetime, timezone
from pathlib import Path

RECORD = struct.Struct(">IIIff")
MAX_DECOMPRESSED = 512 * 1024 * 1024


class DailyDecodeError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_day_start(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise DailyDecodeError("day must be explicit UTC")
    if any((dt.hour, dt.minute, dt.second, dt.microsecond)):
        raise DailyDecodeError("day must be UTC midnight")
    return int(dt.timestamp())


def daily_key(symbol: str, day_epoch: int) -> str:
    dt = datetime.fromtimestamp(day_epoch, timezone.utc)
    return f"{symbol}/{dt.year:04d}/{dt.month - 1:02d}/{dt.day:02d}_ticks.bi5"


def decode_daily(body: bytes, day_epoch: int, price_scale: int = 1000):
    """Return BID minute OHLC rows after validating every tick in the daily file."""
    if day_epoch % 86400:
        raise DailyDecodeError("day_epoch must be UTC midnight")
    if type(price_scale) is not int or price_scale <= 0:
        raise DailyDecodeError("price_scale must be a positive integer")
    if not body:
        raise DailyDecodeError("daily archive uses missing object for no-data days; zero-byte body is invalid")
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
        raw = decoder.decompress(body, max_length=MAX_DECOMPRESSED + 1)
        if len(raw) > MAX_DECOMPRESSED:
            raise DailyDecodeError("decompressed size limit exceeded")
        if not decoder.eof or decoder.unused_data:
            raise DailyDecodeError("truncated or trailing compressed stream")
    except lzma.LZMAError:
        raise DailyDecodeError("invalid LZMA stream") from None
    if len(raw) % RECORD.size:
        raise DailyDecodeError("uncompressed length is not divisible by 20")

    minutes = {}
    previous_ms = -1
    first_ms = None
    for index, (ms, ask_i, bid_i, ask_volume, bid_volume) in enumerate(RECORD.iter_unpack(raw)):
        if ms >= 86_400_000:
            raise DailyDecodeError(f"tick {index}: millisecond offset outside day")
        if ms < previous_ms:
            raise DailyDecodeError(f"tick {index}: decreasing millisecond offset")
        if bid_i <= 0 or ask_i <= 0 or ask_i < bid_i:
            raise DailyDecodeError(f"tick {index}: nonpositive or crossed quote")
        if any(not math.isfinite(v) or v <= 0 for v in (ask_volume, bid_volume)):
            raise DailyDecodeError(f"tick {index}: volume is not finite and positive")
        previous_ms = ms
        if first_ms is None:
            first_ms = ms

        bid = bid_i / price_scale
        minute = day_epoch + (ms // 60_000) * 60
        if minute not in minutes:
            minutes[minute] = [bid, bid, bid, bid]
        else:
            row = minutes[minute]
            row[1] = max(row[1], bid)
            row[2] = min(row[2], bid)
            row[3] = bid

    rows = [(minute, *ohlc) for minute, ohlc in sorted(minutes.items())]
    return rows, {
        "tick_count": len(raw) // RECORD.size,
        "minute_count": len(rows),
        "decompressed_bytes": len(raw),
        "first_tick_ms": first_ms,
        "last_tick_ms": previous_ms if raw else None,
        "price_scale": price_scale,
    }


def csv_bytes(rows) -> bytes:
    from io import StringIO
    s = StringIO(newline="")
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["epoch", "open", "high", "low", "close"])
    for epoch, o, h, l, c in rows:
        w.writerow([epoch, format(o, ".10g"), format(h, ".10g"), format(l, ".10g"), format(c, ".10g")])
    return s.getvalue().encode()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--day", required=True, help="UTC day, e.g. 2026-08-03T00:00:00Z")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--symbol", default="USATECHIDXUSD")
    p.add_argument("--price-scale", default=1000, type=int)
    a = p.parse_args(argv)

    day = utc_day_start(a.day)
    body = a.input.read_bytes()
    rows, meta = decode_daily(body, day, a.price_scale)
    out = a.output
    out.mkdir(parents=True, exist_ok=False)
    csv_data = csv_bytes(rows)
    (out / "native-bid-1m.csv").write_bytes(csv_data)
    receipt = {
        "schema": "astra6-dukascopy-daily-v5",
        "symbol": a.symbol,
        "provider": "Dukascopy",
        "source_key": daily_key(a.symbol, day),
        "day_start": day,
        "source_sha256": sha256_bytes(body),
        "source_bytes": len(body),
        "csv": {
            "path": "native-bid-1m.csv",
            "sha256": sha256_bytes(csv_data),
            "bytes": len(csv_data),
        },
        **meta,
        "execution_allowed": False,
        "performance": None,
    }
    (out / "RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    raise SystemExit(main())
