"""Generated tiny tick fixtures. No acquired market data."""
import lzma
import struct
import acquire_month_v4 as a

START = a.parse_hour('2026-09-01T00:00:00Z')


def packet(epoch, body=None):
    if body is None:
        body = lzma.compress(struct.pack('>IIIff', 0, 100010, 100000, 1., 1.))
    rows, detail = a.codec.decode_hour(body, epoch)
    attempt = dict(state='OK' if rows else 'EMPTY_200', status=200,
                   requested_url=a.codec.hour_url(epoch), final_url=a.codec.hour_url(epoch),
                   body_bytes=len(body), body_sha256=a.codec.sha(body),
                   content_length=str(len(body)), **detail)
    return attempt, body, rows
