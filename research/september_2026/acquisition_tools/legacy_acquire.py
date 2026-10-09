#!/usr/bin/env python3
"""Acquire only the frozen September study, without printing price observations.

Standard library only. --dry-run makes no network requests or writes. --probe-hour
fetches one in-window hour into the same resumable cache, but emits no candle CSV.
The full run writes native-1m.csv plus metadata-only MANIFEST.json. A 404, empty
response, timeout, invalid quote and absent minute remain distinct observations.
"""
import argparse
import collections
import concurrent.futures
import csv
import hashlib
import http.client
import io
import json
import lzma
import math
import os
from pathlib import Path
import socket
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


UTC = timezone.utc
SYMBOL = "USATECHIDXUSD"
SCALE = 1000
MAX_RETRIES = 2  # Three attempts total, including the initial attempt.
MAX_COMPRESSED = 32 * 1024 * 1024
MAX_DECOMPRESSED = 128 * 1024 * 1024
URL_ROOT = "https://www.dukascopy.com/datafeed/"
GOOD_STATES = {"OK", "EMPTY_200"}
EXPECTED = {
    "symbol": "NAS100 CFD", "feed": "Dukascopy USATECHIDXUSD",
    "side": "bid", "price_scale": 1000,
    "native_input": "UTC minute-start aggregated fromticks",
    "warmup_start": "2026-08-24T00:00:00Z",
    "review_start": "2026-09-01T12:00:00Z",
    "review_end": "2026-09-05T00:00:00Z",
    "aggregation": "Complete consecutive minute membership only; declared UTC epoch4H, not Pepperstone native",
    "performance": False, "execution_allowed": False,
}


class AcquisitionError(Exception):
    def __init__(self, state, reason):
        super().__init__(reason)
        self.state, self.reason = state, reason


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(UTC).isoformat()


def utc(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def clocks(epoch):
    return {
        "utc": utc(epoch),
        "CT": datetime.fromtimestamp(epoch, ZoneInfo("America/Chicago")).isoformat(),
        "ET": datetime.fromtimestamp(epoch, ZoneInfo("America/New_York")).isoformat(),
    }


def parse_hour(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError("Hour must have an explicit UTC offset")
    epoch = int(dt.timestamp())
    if dt.microsecond or epoch % 3600:
        raise ValueError("Hour must be exactly aligned to a UTC hour")
    return epoch


def load_protocol(path):
    raw = Path(path).read_bytes()
    protocol = json.loads(raw)
    for key, expected in EXPECTED.items():
        if protocol.get(key) != expected or type(protocol.get(key)) is not type(expected):
            raise ValueError("Frozen protocol mismatch: " + key)
    start, review, end = [parse_hour(protocol[k]) for k in
                          ("warmup_start", "review_start", "review_end")]
    if not start < review < end:
        raise ValueError("Invalid frozen date ordering")
    return protocol, raw, sha(raw), start, end


def hour_url(epoch):
    dt = datetime.fromtimestamp(epoch, UTC)
    return (f"{URL_ROOT}{SYMBOL}/{dt.year:04d}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def hour_key(epoch):
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y%m%dT%H0000Z")


def atomic_bytes(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def missing_ranges(start, end, present):
    """Represent all missing minutes; never guess whether the market was closed."""
    spans, opened = [], None
    for epoch in range(start, end, 60):
        if epoch not in present and opened is None:
            opened = epoch
        if epoch in present and opened is not None:
            spans.append({"start": opened, "end_exclusive": epoch,
                          "minutes": (epoch - opened) // 60})
            opened = None
    if opened is not None:
        spans.append({"start": opened, "end_exclusive": end,
                      "minutes": (end - opened) // 60})
    return spans


def decode_hour(body, epoch):
    """Validate the whole hour before returning any bid candles."""
    if not body:
        return [], {"tick_count": 0, "empty_kind": "ZERO_BYTE_HTTP_200",
                    "decompressed_bytes": 0, "first_tick_ms": None, "last_tick_ms": None}
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
        raw = decoder.decompress(body, max_length=MAX_DECOMPRESSED + 1)
        if len(raw) > MAX_DECOMPRESSED:
            raise AcquisitionError("DECOMPRESSION_ERROR", "Decompressed size limit exceeded")
        if not decoder.eof or decoder.unused_data:
            raise AcquisitionError("DECOMPRESSION_ERROR", "Truncated or trailing compressed stream")
    except lzma.LZMAError:
        raise AcquisitionError("DECOMPRESSION_ERROR", "Invalid LZMA stream") from None
    if len(raw) % 20:
        raise AcquisitionError("INVALID_TICKS", "Uncompressed length is not divisible by 20")
    minutes, previous_ms, first_ms = {}, -1, None
    for index, (ms, ask, bid, ask_volume, bid_volume) in enumerate(struct.iter_unpack(">IIIff", raw)):
        if ms >= 3600000:
            raise AcquisitionError("INVALID_TICKS", f"Tick {index}: millisecond offset outside hour")
        if ms < previous_ms:
            raise AcquisitionError("INVALID_TICKS", f"Tick {index}: decreasing millisecond offset")
        if bid <= 0 or ask <= 0 or ask < bid:
            raise AcquisitionError("INVALID_TICKS", f"Tick {index}: nonpositive or crossed quote")
        if any(not math.isfinite(v) or v <= 0 for v in (ask_volume, bid_volume)):
            raise AcquisitionError("INVALID_TICKS", f"Tick {index}: volume is not finite and positive")
        previous_ms = ms
        if first_ms is None:
            first_ms = ms
        minute = epoch + (ms // 60000) * 60
        if minute not in minutes:
            minutes[minute] = [bid, bid, bid, bid]
        else:
            row = minutes[minute]
            row[1], row[2], row[3] = max(row[1], bid), min(row[2], bid), bid
    rows = [(minute, *ohlc) for minute, ohlc in sorted(minutes.items())]
    return rows, {"tick_count": len(raw) // 20, "decompressed_bytes": len(raw),
                  "empty_kind": "DECOMPRESSED_ZERO_TICKS" if not raw else None,
                  "first_tick_ms": first_ms, "last_tick_ms": previous_ms if raw else None}


def check_redirect(url, original_url):
    parsed, original = urllib.parse.urlsplit(url), urllib.parse.urlsplit(original_url)
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"www.dukascopy.com", "datafeed.dukascopy.com"}
            or parsed.username or parsed.password or parsed.port is not None
            or parsed.path != original.path or parsed.query or parsed.fragment):
        raise AcquisitionError("UNEXPECTED_REDIRECT", "Redirect outside the documented public file path")


class DocumentedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, original, history):
        self.original, self.history = original, history
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_redirect(newurl, self.original)
        self.history.append({"status": code, "from_url": req.full_url, "to_url": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def response_metadata(response, requested_url, attempt, body):
    final_url = response.geturl()
    check_redirect(final_url, requested_url)
    attempt.update({"status": response.getcode(), "final_url": final_url,
                    "body_bytes": len(body), "body_sha256": sha(body),
                    "content_type": response.headers.get("Content-Type"),
                    "content_length": response.headers.get("Content-Length"),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "received_via_plain_http": urllib.parse.urlsplit(final_url).scheme == "http"})


def fetch_attempt(epoch, timeout=20, opener_factory=None):
    requested = hour_url(epoch)
    attempt = {"started_at": now(), "requested_url": requested,
               "final_url": None, "status": None, "body_bytes": None,
               "body_sha256": None, "redirects": []}
    body, rows, details = None, [], {}
    try:
        factory = opener_factory or (lambda original, history:
            urllib.request.build_opener(DocumentedRedirect(original, history)))
        opener = factory(requested, attempt["redirects"])
        request = urllib.request.Request(requested, headers={
            "User-Agent": "IsaacResearchDataAcquisition/1.0", "Accept-Encoding": "identity"})
        with opener.open(request, timeout=timeout) as response:
            try:
                body = response.read(MAX_COMPRESSED + 1)
            except http.client.IncompleteRead as error:
                body = error.partial
                response_metadata(response, requested, attempt, body)
                raise AcquisitionError("INCOMPLETE_RESPONSE", "Response ended before declared length") from None
            response_metadata(response, requested, attempt, body)
            if len(body) > MAX_COMPRESSED:
                raise AcquisitionError("BODY_TOO_LARGE", "Compressed body size limit exceeded")
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdigit() or int(declared) != len(body)):
                raise AcquisitionError("INCOMPLETE_RESPONSE", "Content-Length does not match complete body")
            if response.getcode() != 200:
                raise AcquisitionError("HTTP_ERROR", "Unexpected HTTP success status")
        rows, details = decode_hour(body, epoch)
        attempt["state"] = "OK" if rows else "EMPTY_200"
        attempt.update(details)
    except urllib.error.HTTPError as error:
        # An error status remains visible even if its optional response body fails.
        attempt.update(state="HTTP_404" if error.code == 404 else "HTTP_ERROR",
                       status=error.code, final_url=error.geturl(), reason="HTTP error response")
        try:
            body = error.read(MAX_COMPRESSED + 1)
            response_metadata(error, requested, attempt, body)
        except (OSError, http.client.HTTPException, AcquisitionError) as read_error:
            attempt["error_body_read_failure"] = type(read_error).__name__
        finally:
            error.close()
    except AcquisitionError as error:
        attempt.update(state=error.state, reason=error.reason)
    except (TimeoutError, socket.timeout):
        attempt.update(state="TIMEOUT", reason="Transport timed out")
    except urllib.error.URLError as error:
        attempt.update(state="TIMEOUT" if isinstance(error.reason, (TimeoutError, socket.timeout))
                       else "TRANSPORT_ERROR", reason="Transport request failed",
                       error_type=type(error.reason).__name__)
    except (OSError, ValueError, http.client.HTTPException) as error:
        attempt.update(state="TRANSPORT_ERROR", reason="Transport request failed",
                       error_type=type(error).__name__)
    attempt["finished_at"] = now()
    return attempt, body, rows


def store_body(target, body):
    digest = sha(body)
    relative = f"blobs/{digest}.bi5"
    path = target / relative
    if not path.exists() or sha(path.read_bytes()) != digest:
        atomic_bytes(path, body)
    return relative


def validated_cache(target, receipt, epoch, protocol_sha, script_sha):
    if (receipt.get("hour_start") != epoch or receipt.get("protocol_sha256") != protocol_sha
            or receipt.get("script_sha256") != script_sha
            or receipt.get("requested_url") != hour_url(epoch)
            or receipt.get("state") not in GOOD_STATES):
        raise ValueError("Receipt identity or final state is not reusable")
    attempts = receipt.get("attempts", [])
    if not attempts:
        raise ValueError("Missing attempt receipt")
    last = attempts[-1]
    digest = last.get("body_sha256", "")
    if (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            or last.get("body_file") != f"blobs/{digest}.bi5"):
        raise ValueError("Invalid content-addressed body identity")
    if last.get("state") != receipt["state"] or last.get("status") != 200:
        raise ValueError("Cached response was not a validated HTTP 200")
    if last.get("requested_url") != hour_url(epoch):
        raise ValueError("Cached request identity differs")
    check_redirect(last.get("final_url", ""), hour_url(epoch))
    body = (target / last["body_file"]).read_bytes()
    if sha(body) != digest or len(body) != last.get("body_bytes"):
        raise ValueError("Cached body hash or length mismatch")
    declared = last.get("content_length")
    if declared is not None and (not declared.isdigit() or int(declared) != len(body)):
        raise ValueError("Cached response length differs")
    rows, detail = decode_hour(body, epoch)
    if (detail["tick_count"] != last.get("tick_count")
            or ("OK" if rows else "EMPTY_200") != receipt["state"]
            or len(rows) != receipt.get("minute_count")
            or 60 - len(rows) != receipt.get("missing_minutes")
            or missing_ranges(epoch, epoch + 3600, {r[0] for r in rows}) != receipt.get("missing_minute_ranges")
            or any(detail[key] != last.get(key) for key in
                   ("first_tick_ms", "last_tick_ms", "decompressed_bytes", "empty_kind"))):
        raise ValueError("Cached metadata does not match decoded body")
    return rows


def acquire_hour(target, epoch, protocol_sha, script_sha, timeout=20,
                 fetcher=fetch_attempt, sleeper=time.sleep):
    receipt_path = target / "hours" / (hour_key(epoch) + ".json")
    old, cache_events = {}, []
    if receipt_path.exists():
        try:
            old = json.loads(receipt_path.read_bytes())
            if not isinstance(old, dict):
                raise ValueError("Receipt must be a JSON object")
            rows = validated_cache(target, old, epoch, protocol_sha, script_sha)
            return old, rows, True
        except (ValueError, OSError, AcquisitionError, TypeError, KeyError) as error:
            cache_events = [{"at": now(), "state": "CACHE_NOT_REUSED",
                             "reason": str(error)}]
            if not isinstance(old, dict):
                old = {}
    attempts = list(old.get("attempts", []))
    receipt = {"hour_start": epoch, "hour_end_exclusive": epoch + 3600,
               "clocks": clocks(epoch), "requested_url": hour_url(epoch),
               "protocol_sha256": protocol_sha, "script_sha256": script_sha,
               "cache_events": list(old.get("cache_events", [])) + cache_events,
               "attempts": attempts}
    for retry in range(MAX_RETRIES + 1):
        attempt, body, rows = fetcher(epoch, timeout)
        attempt["attempt_number"] = len(attempts) + 1
        if body is not None:
            attempt["body_file"] = store_body(target, body)
        attempts.append(attempt)
        present = {row[0] for row in rows}
        receipt.update(state=attempt["state"], minute_count=len(rows),
                       missing_minutes=60 - len(rows),
                       missing_minute_ranges=missing_ranges(epoch, epoch + 3600, present),
                       updated_at=now())
        # Commit body first, then receipt; a crash cannot leave a reusable partial hour.
        atomic_json(receipt_path, receipt)
        if attempt["state"] in GOOD_STATES or attempt["state"] == "HTTP_404":
            break
        if retry < MAX_RETRIES:
            sleeper(retry + 1)
    return receipt, rows, False


def ensure_identity(target, protocol_raw, protocol_sha, script_sha):
    target.mkdir(parents=True, exist_ok=True)
    identity = {"protocol_sha256": protocol_sha, "script_sha256": script_sha,
                "symbol": SYMBOL, "side": "BID", "scale": SCALE,
                "timestamp_semantics": "UTC minute-open; integer epoch seconds",
                "origin": URL_ROOT, "max_retries": MAX_RETRIES,
                "redirect_allowlist": ["www.dukascopy.com", "datafeed.dukascopy.com"],
                "plain_http_note": "Published redirect may use HTTP. Body hashes validate local integrity, not server authenticity."}
    path = target / "ACQUISITION_IDENTITY.json"
    frozen = target / "FROZEN_PROTOCOL.json"
    if path.exists():
        if json.loads(path.read_bytes()) != identity:
            raise ValueError("Existing acquisition identity differs; use a new explicit target")
        if not frozen.exists() or sha(frozen.read_bytes()) != protocol_sha:
            raise ValueError("Frozen protocol missing or changed")
    else:
        if any(target.iterdir()):
            raise ValueError("New acquisition target must be empty")
        atomic_bytes(frozen, protocol_raw)
        atomic_json(path, identity)
    return identity


def metadata_manifest(results, start, end, protocol_sha, script_sha, run_started, probe):
    receipts = sorted((result[0] for result in results), key=lambda r: r["hour_start"])
    rows = sorted((row for result in results for row in result[1]), key=lambda row: row[0])
    epochs = [row[0] for row in rows]
    if len(set(epochs)) != len(epochs) or any(t < start or t >= end or t % 60 for t in epochs):
        raise ValueError("Output timestamps are duplicated, unaligned or outside frozen window")
    states = dict(collections.Counter(r["state"] for r in receipts))
    expected = (end - start) // 3600
    complete = len(receipts) == expected and all(r["state"] in GOOD_STATES for r in receipts)
    holes = missing_ranges(start, end, set(epochs))
    manifest = {
        "run_started_at": run_started, "run_finished_at": now(),
        "scope": "SINGLE_HOUR_PROBE" if probe else "FROZEN_FULL_WINDOW",
        "protocol_sha256": protocol_sha, "script_sha256": script_sha,
        "symbol": "NAS100 CFD", "feed": "Dukascopy USATECHIDXUSD", "side": "BID",
        "start": clocks(start), "end_exclusive": clocks(end),
        "expected_hours": expected, "processed_hours": len(receipts),
        "reused_verified_hours": sum(result[2] for result in results), "hour_states": states,
        "all_hours_validated_or_explicit_empty_200": complete,
        "minute_count": len(rows), "first_minute": clocks(epochs[0]) if epochs else None,
        "last_minute": clocks(epochs[-1]) if epochs else None,
        "missing_minutes": sum(h["minutes"] for h in holes), "missing_minute_ranges": holes,
        "missing_minute_interpretation": "UNCLASSIFIED: no ticks, closure or acquisition/source issue; no synthetic fills",
        "unresolved_hours": [{"hour_start": r["hour_start"], "state": r["state"]}
                             for r in receipts if r["state"] not in GOOD_STATES],
        "price_values_printed": False, "performance_computed": False,
        "teacher_fidelity_claim": False, "execution_allowed": False,
        "protocol_changed": False,
        "plain_http_response_hours": sum(bool(r["attempts"][-1].get("received_via_plain_http")) for r in receipts),
        "readiness": "ACQUISITION_ONLY; session/gap validation required before study",
    }
    return manifest, rows


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["epoch", "open", "high", "low", "close"])
    for row in rows:
        writer.writerow([row[0], *(f"{p // SCALE}.{p % SCALE:03d}" for p in row[1:])])
    return stream.getvalue().encode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path,
                        help="Dedicated empty/resumable acquisition directory")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--probe-hour", help="One UTC hour inside the frozen dates; no candle CSV")
    parser.add_argument("--workers", type=int, default=8, choices=range(1, 9))
    parser.add_argument("--timeout", type=int, default=20, choices=range(1, 61))
    args = parser.parse_args(argv)
    _, raw, protocol_sha, start, end = load_protocol(args.protocol)
    hours = list(range(start, end, 3600))
    if args.probe_hour:
        probe_hour = parse_hour(args.probe_hour)
        if probe_hour not in hours:
            raise ValueError("Probe hour is outside the frozen window")
        hours = [probe_hour]
    script_sha = sha(Path(__file__).read_bytes())
    plan = {"protocol_sha256": protocol_sha, "script_sha256": script_sha,
            "start": clocks(start), "end_exclusive": clocks(end),
            "requested_hours": len(hours), "workers": args.workers,
            "maximum_attempts_per_hour_this_run": MAX_RETRIES + 1,
            "first_request": hour_url(hours[0]), "last_request": hour_url(hours[-1])}
    if args.dry_run:
        print(json.dumps({"dry_run": True, "network_requests": 0, **plan}, indent=2))
        return 0
    target = args.target.resolve()
    ensure_identity(target, raw, protocol_sha, script_sha)
    lock = target / "RUN.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("RUN.lock exists; verify the prior process ended before removing that lock") from None
    os.write(lock_fd, json.dumps({"pid": os.getpid(), "started_at": now()}).encode())
    os.close(lock_fd)
    try:
        run_started = now()
        print(json.dumps({"state": "ACQUISITION_STARTED", **plan}), flush=True)
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = [pool.submit(acquire_hour, target, epoch, protocol_sha,
                                   script_sha, args.timeout) for epoch in hours]
            for future in concurrent.futures.as_completed(pending):
                results.append(future.result())
                if len(results) % 24 == 0:
                    print(json.dumps({"state": "ACQUISITION_PROGRESS", "completed_hours": len(results),
                                      "requested_hours": len(hours),
                                      "hour_states": dict(collections.Counter(r[0]["state"] for r in results))}),
                          flush=True)
        manifest, rows = metadata_manifest(results, start, end, protocol_sha, script_sha,
                                           run_started, bool(args.probe_hour))
        if not args.probe_hour:
            body = csv_bytes(rows)
            atomic_bytes(target / "native-1m.csv", body)
            manifest["csv"] = {"path": "native-1m.csv", "sha256": sha(body), "bytes": len(body)}
        name = "PROBE_MANIFEST.json" if args.probe_hour else "MANIFEST.json"
        atomic_json(target / name, manifest)
        print(json.dumps({"manifest": str(target / name), "hour_states": manifest["hour_states"],
                          "minute_count": manifest["minute_count"],
                          "missing_minutes": manifest["missing_minutes"],
                          "scope": manifest["scope"], "price_values_printed": False}, indent=2))
        if args.probe_hour:
            return 0 if all(r[0]["state"] in GOOD_STATES for r in results) else 2
        return 0 if manifest["all_hours_validated_or_explicit_empty_200"] else 2
    finally:
        lock.unlink()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(json.dumps({"state": "ACQUISITION_STOPPED", "reason": str(exc)}))
        raise SystemExit(2)
