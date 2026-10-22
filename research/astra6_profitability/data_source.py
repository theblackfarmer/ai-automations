"""Read-only admission of the frozen Dukascopy raw-tick acquisition.

An HTTP-200 receipt for every hour is required before any profit study. It is
not proof that missing minutes were closed or that the source was authentic.
The pinned collector validates each captured body; no alternate feed, filled
minute, native-daily transport, or OHLC-only execution fallback is accepted.
"""
from __future__ import annotations

import builtins
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from types import ModuleType

try:
    from .quote_execution import Quote
except ImportError:
    from quote_execution import Quote


REPOSITORY = Path(__file__).resolve().parents[2]
STUDY = "research/september_2026/"
TOOLS = STUDY + "acquisition_tools/"
START_UTC = "2026-08-01T00:00:00Z"
STUDY_START_UTC = "2026-09-01T00:00:00Z"
END_UTC = "2026-10-01T00:00:00Z"
HOURS = 1464
METADATA_LIMIT = 16 * 1024 * 1024
CSV_LIMIT = 32 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
FIXED = {
    TOOLS + "acquire_month_v4.py": "34cb44cc3c370138a07b21b01d72a8fb039212c76365ac172cc2a570d585054c",
    TOOLS + "legacy_acquire.py": "3f19fb017d267691cfc98e26874152a642bd59486d43f642bb6036f3f8acb5ed",
    TOOLS + "FROZEN_MONTH_PROTOCOL.json": "6c00c80649bfdff559e43b1ca698f7cb9874668223fe535b2498bed9ad3eab74",
    TOOLS + "TRANSPORT_AMENDMENT_V4.json": "fed4c0ade1009e7a06af55dda4c4b30b0b17060b0f450ca0a30843215a4448c9",
}
REQUIRED_PINS = (
    STUDY + "engine.py", STUDY + "SPEC.json",
    STUDY + "IMPLEMENTATION_CLARIFICATIONS.json", STUDY + "month_replay.py",
    TOOLS + "acquire_month_v4.py", TOOLS + "legacy_acquire.py",
    TOOLS + "FROZEN_MONTH_PROTOCOL.json",
)


@dataclass(frozen=True)
class RawHour:
    hour_start: int
    receipt_path: str
    receipt_sha256: str
    body_path: str
    body_sha256: str
    body_bytes: int
    tick_count: int
    minute_count: int
    state: str
    first_tick_ms: int | None
    last_tick_ms: int | None


@dataclass(frozen=True)
class _FileBinding:
    root: str
    relative: str
    sha256: str
    limit: int


@dataclass(frozen=True)
class _BoundIteration:
    root: str
    hours: tuple[RawHour, ...]
    files: tuple[_FileBinding, ...]
    codec: ModuleType
    rows_sha256: str


class _CapturedBody:
    """Give the unchanged validator only the bounded bytes already captured."""
    def __init__(self, relative, body):
        self.relative, self.body = relative, body

    def __truediv__(self, relative):
        if relative != self.relative:
            raise ValueError("Unexpected captured body path")
        return self

    def read_bytes(self):
        return self.body


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _same(left, right):
    return _canonical(left) == _canonical(right)


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Non-finite JSON value: " + value)

    result = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def _read(root, relative, limit=METADATA_LIMIT):
    """Only regular files at canonical relative paths; never follow symlinks."""
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Invalid relative input path")
    name = PurePosixPath(relative)
    if (name.is_absolute() or not name.parts or any(p in (".", "..") for p in name.parts)
            or str(name) != relative):
        raise ValueError("Noncanonical or escaping input path")
    path = Path(root)
    for part in name.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlink input is not accepted: " + relative)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Input is not a bounded regular file: " + relative)
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Input exceeds size limit: " + relative)
        return data
    except OSError as exc:
        raise ValueError("Missing or unreadable input: " + relative) from exc


def _capture(root, relative, bindings, limit=METADATA_LIMIT, expected=None):
    data = _read(root, relative, limit)
    digest = _sha(data)
    if expected is not None and digest != expected:
        raise ValueError("Source/input hash mismatch: " + relative)
    bindings.append(_FileBinding(str(root), relative, digest, limit))
    return data


def _module(name, data, path, codec=None):
    module = ModuleType(name)
    module.__file__ = str(path)
    if codec is not None:
        original_import = builtins.__import__

        def bound_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "legacy_acquire" and level == 0:
                return codec
            return original_import(name, globals, locals, fromlist, level)

        module.__dict__["__builtins__"] = dict(vars(builtins), __import__=bound_import)
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module


def _load_sources(protocol, bindings):
    if not isinstance(protocol, dict) or not isinstance(protocol.get("source_sha256"), dict):
        raise ValueError("Missing source hash bindings in profit protocol")
    pins = protocol["source_sha256"]
    if not set(REQUIRED_PINS).issubset(pins):
        raise ValueError("Missing required source hash binding")
    captured = {}
    for relative, digest in pins.items():
        if not isinstance(digest, str) or not SHA.fullmatch(digest):
            raise ValueError("Invalid source digest")
        captured[relative] = _capture(REPOSITORY, relative, bindings, expected=digest)
    for relative, digest in FIXED.items():
        if relative in captured:
            if _sha(captured[relative]) != digest:
                raise ValueError("Pinned acquisition source changed: " + relative)
        else:
            captured[relative] = _capture(REPOSITORY, relative, bindings, expected=digest)
    codec = _module("_astra_profit_codec", captured[TOOLS + "legacy_acquire.py"],
                    REPOSITORY / (TOOLS + "legacy_acquire.py"))
    driver = _module("_astra_profit_v4", captured[TOOLS + "acquire_month_v4.py"],
                     REPOSITORY / (TOOLS + "acquire_month_v4.py"), codec)
    driver.verify_codec()
    month = _module("_astra_profit_month", captured[STUDY + "month_replay.py"],
                    REPOSITORY / (STUDY + "month_replay.py"))
    return driver, codec, month, captured


def _market(protocol):
    if not isinstance(protocol, dict) or not isinstance(protocol.get("market"), dict):
        raise ValueError("Missing frozen market binding in profit protocol")
    market = protocol.get("market", {})
    expected = dict(symbol="NAS100 CFD", provider="Dukascopy USATECHIDXUSD",
                    raw_sides=["BID", "ASK"], structure_side="BID", raw_scale=1000,
                    warmup_start_utc=START_UTC, study_start_utc=STUDY_START_UTC,
                    end_exclusive_utc=END_UTC)
    if any(type(market.get(k)) is not type(v) or market.get(k) != v for k, v in expected.items()):
        raise ValueError("Profit protocol does not bind the frozen market/window")


def _identity(root, manifest, start, end, driver, captured, bindings):
    route = manifest.get("wire_route")
    if route not in driver.ROUTES:
        raise ValueError("Unsupported acquisition wire route")
    protocol_sha = FIXED[TOOLS + "FROZEN_MONTH_PROTOCOL.json"]
    script_sha = FIXED[TOOLS + "acquire_month_v4.py"]
    amendment_sha = FIXED[TOOLS + "TRANSPORT_AMENDMENT_V4.json"]
    frozen = _capture(root, "FROZEN_PROTOCOL.json", bindings, expected=protocol_sha)
    if frozen != captured[TOOLS + "FROZEN_MONTH_PROTOCOL.json"]:
        raise ValueError("Frozen month bytes differ")
    amendment = _capture(root, "TRANSPORT_AMENDMENT_V4.json", bindings, expected=amendment_sha)
    if amendment != captured[TOOLS + "TRANSPORT_AMENDMENT_V4.json"]:
        raise ValueError("V4 transport amendment differs")
    raw = _capture(root, "MONTH_IDENTITY.json", bindings)
    old = _json(raw)
    expected = dict(protocol_sha256=protocol_sha, script_sha256=script_sha,
                    codec_sha256=driver.CODEC_SHA, symbol="USATECHIDXUSD", scale=1000,
                    raw_sides=["BID", "ASK"], derived_side="BID", start=start,
                    end_exclusive=end, parallel_requests=1,
                    transport_amendment_sha256=amendment_sha,
                    ordinary_attempts_per_hour_per_run=2, wire_route=route)
    if _same(old, expected):
        if (root / "TRANSPORT_IDENTITY_V4.json").exists():
            raise ValueError("Unexpected lineage for a direct V4 identity")
        return {script_sha}, route

    prior = {k: v for k, v in expected.items() if k != "wire_route"}
    v2 = dict(prior, script_sha256=driver.V2_SCRIPT_SHA,
              transport_amendment_sha256=driver.V2_AMENDMENT_SHA)
    v3 = dict(prior, script_sha256=driver.V3_SCRIPT_SHA,
              transport_amendment_sha256=driver.V3_AMENDMENT_SHA)
    allowed, prior_v3_hash = set(), None
    if _same(old, v2):
        _capture(REPOSITORY, TOOLS + "preserved-v2/acquire_month_v2.py", bindings,
                 expected=driver.V2_SCRIPT_SHA)
        _capture(root, "TRANSPORT_AMENDMENT_V2.json", bindings, expected=driver.V2_AMENDMENT_SHA)
        allowed.add(driver.V2_SCRIPT_SHA)
        if (root / "TRANSPORT_IDENTITY_V3.json").exists():
            v3raw = _capture(root, "TRANSPORT_IDENTITY_V3.json", bindings)
            expected_v3 = dict(schema="month-acquisition-code-lineage-v3",
                               prior_identity_sha256=_sha(raw), prior_script_sha256=driver.V2_SCRIPT_SHA,
                               prior_amendment_sha256=driver.V2_AMENDMENT_SHA, current_identity=v3,
                               does_not_retroactively_clear_v2=True)
            if not _same(_json(v3raw), expected_v3):
                raise ValueError("Invalid V3 identity lineage")
            prior_v3_hash = _sha(v3raw)
            allowed.add(driver.V3_SCRIPT_SHA)
    elif _same(old, v3):
        allowed.add(driver.V3_SCRIPT_SHA)
    else:
        raise ValueError("Acquisition feed, sides, scope or identity changed")
    if driver.V3_SCRIPT_SHA in allowed:
        _capture(REPOSITORY, TOOLS + "preserved-v3/acquire_month_v3.py", bindings,
                 expected=driver.V3_SCRIPT_SHA)
        _capture(root, "TRANSPORT_AMENDMENT_V3.json", bindings, expected=driver.V3_AMENDMENT_SHA)
    lineage = dict(schema="month-acquisition-code-lineage-v4", prior_identity_sha256=_sha(raw),
                   prior_v3_lineage_sha256=prior_v3_hash, prior_receipt_script_sha256=sorted(allowed),
                   current_identity=expected, does_not_retroactively_clear_prior_runs=True)
    if not _same(_json(_capture(root, "TRANSPORT_IDENTITY_V4.json", bindings)), lineage):
        raise ValueError("Missing or invalid V4 identity lineage")
    return allowed | {script_sha}, route


def _body_reference(attempt):
    digest = attempt.get("body_sha256")
    relative = attempt.get("body_file")
    if not isinstance(digest, str) or not SHA.fullmatch(digest) or relative != "blobs/" + digest + ".bi5":
        raise ValueError("Invalid content-addressed body path")
    return relative, digest


def _validate_hour(root, epoch, driver, codec, protocol_sha, allowed, route):
    """Validate one source hour; useful for small explicit synthetic contract tests."""
    relative = "hours/" + codec.hour_key(epoch) + ".json"
    receipt_raw = _read(root, relative)
    receipt = _json(receipt_raw)
    if receipt.get("state") not in codec.GOOD_STATES:
        raise ValueError("BLOCKED_FULL_MONTH_ACQUISITION: unresolved source hour")
    attempts = receipt.get("attempts")
    if not isinstance(attempts, list) or not attempts or not all(isinstance(a, dict) for a in attempts):
        raise ValueError("Missing attempt evidence")
    for attempt in attempts:
        if attempt.get("body_file") is not None:
            _body_reference(attempt)
    last = attempts[-1]
    for value in (receipt.get("hour_start"), receipt.get("hour_end_exclusive"),
                  receipt.get("minute_count"), receipt.get("missing_minutes"),
                  last.get("body_bytes"), last.get("tick_count"), last.get("decompressed_bytes")):
        if type(value) is not int or value < 0:
            raise ValueError("Malformed native integer receipt field")
    if last.get("content_length") is not None and not isinstance(last["content_length"], str):
        raise ValueError("Malformed HTTP Content-Length")
    body_relative, digest = _body_reference(last)
    body = _read(root, body_relative, codec.MAX_COMPRESSED)
    if _sha(body) != digest:
        raise ValueError("Raw body hash mismatch")
    snapshot = _CapturedBody(body_relative, body)
    try:
        rows = driver.validate_prior_receipt(snapshot, receipt, epoch, protocol_sha, allowed,
                                             FIXED[TOOLS + "acquire_month_v4.py"], route)
        if "import_provenance" in receipt:
            proof = receipt["import_provenance"]
            identity = proof["original_identity"]
            original = proof["original_receipt"]
            if (identity.get("symbol") != "USATECHIDXUSD" or type(identity.get("scale")) is not int
                    or identity.get("scale") != 1000 or identity.get("side") != "BID"
                    or identity.get("script_sha256") != driver.CODEC_SHA
                    or proof.get("original_protocol_sha256") != identity.get("protocol_sha256")):
                raise ValueError("Imported legacy identity is not the pinned BID feed")
            original_rows = codec.validated_cache(snapshot, original, epoch,
                                                   identity["protocol_sha256"], driver.CODEC_SHA)
            if original_rows != rows:
                raise ValueError("Imported legacy evidence does not reconcile")
    except (codec.AcquisitionError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid hourly source/lineage evidence: " + str(exc)) from exc
    compact = RawHour(epoch, relative, _sha(receipt_raw), body_relative, digest,
                      len(body), last["tick_count"], len(rows), receipt["state"],
                      last["first_tick_ms"], last["last_tick_ms"])
    return rows, compact, receipt


def _synthetic_evidence(record):
    candidates = [record]
    provenance = record.get("import_provenance")
    if isinstance(provenance, dict):
        candidates.extend(provenance.get(key) for key in ("original_receipt", "original_identity"))
    return any(isinstance(item, dict) and
               (item.get("synthetic_fixture") is True or item.get("data_kind") == "synthetic")
               for item in candidates)


def _check_manifest(manifest, start, end, driver):
    if _synthetic_evidence(manifest):
        raise ValueError("Synthetic fixtures cannot establish a historical acquisition")
    expected = dict(symbol="NAS100 CFD", feed="Dukascopy USATECHIDXUSD", side="BID",
                    expected_hours=HOURS, processed_hours=HOURS, raw_sides=["BID", "ASK"],
                    protocol_sha256=FIXED[TOOLS + "FROZEN_MONTH_PROTOCOL.json"],
                    script_sha256=FIXED[TOOLS + "acquire_month_v4.py"], codec_sha256=driver.CODEC_SHA,
                    transport_amendment_sha256=FIXED[TOOLS + "TRANSPORT_AMENDMENT_V4.json"],
                    scope="FROZEN_FULL_WINDOW", absent_hours_not_assumed_closed=True,
                    execution_allowed=False, performance_computed=False, run_in_progress=False)
    if any(type(manifest.get(k)) is not type(v) or manifest.get(k) != v for k, v in expected.items()):
        raise ValueError("Manifest is not the complete frozen raw-tick feed/window")
    if manifest.get("start") != driver.codec.clocks(start) or manifest.get("end_exclusive") != driver.codec.clocks(end):
        raise ValueError("Manifest window differs from the frozen month")
    if manifest.get("unresolved_hours") != [] or manifest.get("unattempted_hours") != []:
        raise ValueError("BLOCKED_FULL_MONTH_ACQUISITION: unresolved or unattempted hours")
    states = manifest.get("hour_states")
    if (not isinstance(states, dict) or set(states) - driver.GOOD
            or any(type(v) is not int or v < 0 for v in states.values()) or sum(states.values()) != HOURS):
        raise ValueError("Incomplete or malformed source-hour counts")
    csv_info = manifest.get("csv", {})
    if csv_info.get("path") != "native-bid-1m.csv" or csv_info.get("incomplete") is not False:
        raise ValueError("Only the complete collector CSV is accepted")
    if not isinstance(csv_info.get("sha256"), str) or not SHA.fullmatch(csv_info["sha256"]):
        raise ValueError("Missing or malformed CSV content hash")


def _reconcile(csv_data, native_rows, codec, month):
    if csv_data != codec.csv_bytes(native_rows):
        raise ValueError("Raw tick BID OHLC does not exactly reconcile with source CSV")
    return month.parse_csv(csv_data)


def validate_acquisition(acquisition_path, protocol):
    """Return rows, immutable raw-hour descriptors and JSON-safe provenance.

    ``acquisition_path`` is the acquisition directory or its MANIFEST.json.
    The private iteration binding captures the validated hashes; it is not part
    of the JSON summary and must be passed intact to :func:`iter_quotes`.
    """
    path = Path(acquisition_path)
    if path.is_dir():
        root = path.resolve()
    else:
        if path.name != "MANIFEST.json" or path.is_symlink():
            raise ValueError("Expected acquisition directory or MANIFEST.json")
        root = path.parent.resolve()
    bindings = []
    manifest_raw = _capture(root, "MANIFEST.json", bindings)
    manifest = _json(manifest_raw)
    if manifest.get("all_hours_validated_or_explicit_empty_200") is not True:
        raise ValueError("BLOCKED_FULL_MONTH_ACQUISITION: complete frozen raw-hour evidence required")
    _market(protocol)
    driver, codec, month, captured = _load_sources(protocol, bindings)
    start, end = driver.parse_hour(START_UTC), driver.parse_hour(END_UTC)
    _check_manifest(manifest, start, end, driver)
    allowed, route = _identity(root, manifest, start, end, driver, captured, bindings)
    native_rows, raw_hours, states, scripts = [], [], Counter(), Counter()
    imported_count = plain_http_count = 0
    for epoch in range(start, end, 3600):
        values, hour, receipt = _validate_hour(root, epoch, driver, codec,
                                               FIXED[TOOLS + "FROZEN_MONTH_PROTOCOL.json"], allowed, route)
        if _synthetic_evidence(receipt):
            raise ValueError("Synthetic hourly fixtures cannot establish historical coverage")
        native_rows.extend(values)
        raw_hours.append(hour)
        states[receipt["state"]] += 1
        scripts[receipt["script_sha256"]] += 1
        imported_count += "import_provenance" in receipt
        plain_http_count += bool(receipt["attempts"][-1].get("received_via_plain_http"))
    if (dict(states) != manifest["hour_states"]
            or dict(scripts) != manifest.get("receipt_script_sha256_counts")
            or imported_count != manifest.get("cache_import_count")
            or plain_http_count != manifest.get("plain_http_response_hours")):
        raise ValueError("Manifest source counts differ from validated hourly evidence")
    csv_info = manifest["csv"]
    csv_data = _capture(root, csv_info["path"], bindings, CSV_LIMIT, csv_info.get("sha256"))
    if type(csv_info.get("bytes")) is not int or len(csv_data) != csv_info["bytes"]:
        raise ValueError("CSV length differs from manifest")
    rows = _reconcile(csv_data, native_rows, codec, month)
    holes = codec.missing_ranges(start, end, set(rows))
    if (type(manifest.get("minute_count")) is not int or manifest["minute_count"] != len(rows)
            or manifest.get("missing_minute_ranges") != holes
            or manifest.get("missing_minutes") != sum(h["minutes"] for h in holes)
            or manifest.get("first_minute") != (codec.clocks(min(rows)) if rows else None)
            or manifest.get("last_minute") != (codec.clocks(max(rows)) if rows else None)):
        raise ValueError("Manifest minute coverage differs from raw tick evidence")
    if not rows:
        raise ValueError("BLOCKED_NO_OBSERVED_MARKET_QUOTES")
    frozen_hours = tuple(raw_hours)
    canonical_receipts = _canonical([asdict(h) for h in frozen_hours])
    summary = dict(
        status="VALIDATED_FROZEN_RAW_TICK_ACQUISITION", acquisition_directory=str(root),
        manifest_path=str(root / "MANIFEST.json"), csv_path=str(root / csv_info["path"]),
        manifest_sha256=_sha(manifest_raw), csv_sha256=_sha(csv_data),
        frozen_protocol_sha256=FIXED[TOOLS + "FROZEN_MONTH_PROTOCOL.json"],
        symbol="NAS100 CFD", feed="Dukascopy USATECHIDXUSD", raw_sides=["BID", "ASK"],
        structure_side="BID", raw_scale=1000, start_utc=START_UTC,
        study_start_utc=STUDY_START_UTC, end_exclusive_utc=END_UTC,
        expected_hours=HOURS, validated_hours=len(frozen_hours), hour_states=dict(states),
        observed_minutes=len(rows), missing_minutes=manifest["missing_minutes"],
        missing_minute_ranges=holes, raw_tick_count=sum(h.tick_count for h in frozen_hours),
        raw_receipts_sha256=_sha(canonical_receipts),
        first_quote_timestamp_ms=next(h.hour_start * 1000 + h.first_tick_ms for h in frozen_hours if h.tick_count),
        last_quote_timestamp_ms=next(h.hour_start * 1000 + h.last_tick_ms for h in reversed(frozen_hours) if h.tick_count),
        imported_legacy_hours=imported_count, plain_http_response_hours=plain_http_count,
        source_sha256={b.relative: b.sha256 for b in bindings if b.root == str(REPOSITORY)},
        acquisition_input_sha256={b.relative: b.sha256 for b in bindings if b.root == str(root)},
        exact_raw_tick_to_bid_minute_reconciliation=True,
        all_hours_validated_or_explicit_empty_200=True, missing_minutes_assumed_closed=False,
        scheduled_session_coverage_established=False, complete_month_claim=False,
        provider_authenticity_independently_verified=False,
        authenticity_limit="Receipt/body hashes establish local consistency, not independent provider authenticity.",
        execution_allowed=False,
    )
    return dict(rows=rows, raw_receipts=frozen_hours, summary=summary,
                _iteration=_BoundIteration(str(root), frozen_hours, tuple(bindings), codec,
                                           _sha(_canonical(rows))))


def _decompress_verified(body, codec):
    if not body:
        return b""
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
        raw = decoder.decompress(body, max_length=codec.MAX_DECOMPRESSED + 1)
    except lzma.LZMAError as exc:
        raise ValueError("Invalid verified LZMA stream") from exc
    if len(raw) > codec.MAX_DECOMPRESSED or not decoder.eof or decoder.unused_data or len(raw) % 20:
        raise ValueError("Verified tick body is oversized, truncated or has trailing data")
    return raw


def _iter_hour_quotes(root, hour, codec):
    receipt_raw = _read(root, hour.receipt_path)
    if _sha(receipt_raw) != hour.receipt_sha256:
        raise ValueError("Hourly receipt changed after validation")
    body = _read(root, hour.body_path, codec.MAX_COMPRESSED)
    if _sha(body) != hour.body_sha256 or len(body) != hour.body_bytes:
        raise ValueError("Raw body changed after validation")
    raw = _decompress_verified(body, codec)
    if len(raw) // 20 != hour.tick_count:
        raise ValueError("Verified raw tick count changed")
    # Initial codec validation establishes quote/volume validity and monotonic
    # offsets. The captured content hash binds this second bounded iteration.
    for index, (ms, ask, bid, _, _) in enumerate(struct.iter_unpack(">IIIff", raw)):
        yield Quote(hour.hour_start * 1000 + ms, bid / codec.SCALE, ask / codec.SCALE,
                    hour.body_sha256 + ":" + str(index))


def iter_quotes(validated):
    """Yield every native record in provider order, preserving equal-ms ties."""
    bound = validated.get("_iteration") if isinstance(validated, dict) else None
    if not isinstance(bound, _BoundIteration) or validated.get("raw_receipts") != bound.hours:
        raise ValueError("iter_quotes requires an intact validated acquisition")
    if _sha(_canonical(validated.get("rows"))) != bound.rows_sha256:
        raise ValueError("Derived minute rows changed after validation")
    for item in bound.files:
        if _sha(_read(item.root, item.relative, item.limit)) != item.sha256:
            raise ValueError("Bound source/input changed after validation: " + item.relative)
    for hour in bound.hours:
        yield from _iter_hour_quotes(Path(bound.root), hour, bound.codec)
