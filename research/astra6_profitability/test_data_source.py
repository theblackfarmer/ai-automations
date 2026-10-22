"""Small synthetic source-contract tests; never fabricate a full-month study."""
import copy
from dataclasses import FrozenInstanceError
import json
import lzma
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

try:
    from . import data_source as source
except ImportError:
    import data_source as source


class SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((Path(__file__).parent / "PROFIT_PROTOCOL.json").read_bytes())
        cls.driver, cls.codec, cls.month, cls.captured = source._load_sources(cls.protocol, [])
        cls.epoch = cls.driver.parse_hour("2026-09-01T13:00:00Z")
        cls.psha = source.FIXED[source.TOOLS + "FROZEN_MONTH_PROTOCOL.json"]
        cls.ssha = source.FIXED[source.TOOLS + "acquire_month_v4.py"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="astra-data-source-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = value if isinstance(value, bytes) else source._canonical(value)
        path.write_bytes(data)
        return path

    def fixture(self, records=None, empty=False):
        if records is None:
            records = [(1000, 100250, 100000, 1.0, 2.0),
                       (1000, 100350, 100100, 1.0, 2.0),
                       (60000, 99850, 99600, 1.0, 2.0),
                       (3599999, 100250, 100000, 1.0, 2.0)]
        body = b"" if empty else lzma.compress(b"".join(struct.pack(">IIIff", *r) for r in records))
        rows, detail = self.codec.decode_hour(body, self.epoch)
        digest = source._sha(body)
        relative = "blobs/" + digest + ".bi5"
        self.write(relative, body)
        attempt = dict(requested_url=self.codec.hour_url(self.epoch),
                       final_url="https://datafeed.dukascopy.com" +
                                 self.codec.hour_url(self.epoch).split("www.dukascopy.com", 1)[1],
                       status=200, body_file=relative, body_sha256=digest, body_bytes=len(body),
                       content_length=str(len(body)), received_via_plain_http=False,
                       state="OK" if rows else "EMPTY_200", **detail)
        attempt.update(self.driver.routing_metadata(self.codec.hour_url(self.epoch), "direct-https"))
        receipt = dict(hour_start=self.epoch, hour_end_exclusive=self.epoch + 3600,
                       protocol_sha256=self.psha, script_sha256=self.ssha,
                       requested_url=self.codec.hour_url(self.epoch), state=attempt["state"],
                       minute_count=len(rows), missing_minutes=60-len(rows),
                       missing_minute_ranges=self.codec.missing_ranges(
                           self.epoch, self.epoch+3600, {r[0] for r in rows}),
                       attempts=[attempt], synthetic_fixture=True)
        self.save_receipt(receipt)
        return receipt, rows, body

    def save_receipt(self, receipt):
        return self.write("hours/" + self.codec.hour_key(self.epoch) + ".json", receipt)

    def validate_hour(self):
        return source._validate_hour(self.root, self.epoch, self.driver, self.codec,
                                     self.psha, {self.ssha}, "direct-https")

    def replace_body(self, receipt, body):
        last = receipt["attempts"][-1]
        digest = source._sha(body)
        last.update(body_file="blobs/"+digest+".bi5", body_sha256=digest,
                    body_bytes=len(body), content_length=str(len(body)))
        self.write(last["body_file"], body)
        self.save_receipt(receipt)

    def bound_fixture(self):
        _, _, _ = self.fixture()
        rows, hour, _ = self.validate_hour()
        parsed = source._reconcile(self.codec.csv_bytes(rows), rows, self.codec, self.month)
        bound = source._BoundIteration(str(self.root), (hour,), (), self.codec,
                                       source._sha(source._canonical(parsed)))
        return dict(rows=parsed, raw_receipts=(hour,), _iteration=bound)

    def test_incomplete_manifest_blocks_before_source_loading(self):
        self.write("MANIFEST.json", {"all_hours_validated_or_explicit_empty_200": False})
        with patch.object(source, "_load_sources", side_effect=AssertionError("heavy source work")):
            with self.assertRaisesRegex(ValueError, "BLOCKED_FULL_MONTH_ACQUISITION"):
                source.validate_acquisition(self.root, None)

    def test_nonboolean_complete_flag_is_not_accepted(self):
        self.write("MANIFEST.json", {"all_hours_validated_or_explicit_empty_200": 1})
        with self.assertRaisesRegex(ValueError, "BLOCKED_FULL_MONTH_ACQUISITION"):
            source.validate_acquisition(self.root / "MANIFEST.json", self.protocol)

    def test_one_hour_flag_cannot_impersonate_full_month(self):
        self.write("MANIFEST.json", dict(all_hours_validated_or_explicit_empty_200=True,
                                         expected_hours=1, processed_hours=1))
        with self.assertRaisesRegex(ValueError, "complete frozen"):
            source.validate_acquisition(self.root, self.protocol)

    def test_wrong_market_and_source_pin_are_rejected(self):
        self.write("MANIFEST.json", {"all_hours_validated_or_explicit_empty_200": True})
        wrong = copy.deepcopy(self.protocol)
        wrong["market"]["provider"] = "Other index feed"
        with self.assertRaisesRegex(ValueError, "frozen market"):
            source.validate_acquisition(self.root, wrong)
        wrong = copy.deepcopy(self.protocol)
        wrong["source_sha256"][source.TOOLS + "legacy_acquire.py"] = "0"*64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            source.validate_acquisition(self.root, wrong)

    def test_missing_source_pin_is_rejected(self):
        wrong = copy.deepcopy(self.protocol)
        del wrong["source_sha256"][source.STUDY + "month_replay.py"]
        with self.assertRaisesRegex(ValueError, "Missing required"):
            source._load_sources(wrong, [])

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                source._json(data)

    def test_pinned_codec_ignores_poisoned_global_import(self):
        poison = ModuleType("legacy_acquire")
        poison.SCALE = 1
        with patch.dict(sys.modules, {"legacy_acquire": poison}):
            driver, codec, _, _ = source._load_sources(self.protocol, [])
        self.assertIs(driver.codec, codec)
        self.assertEqual(codec.SCALE, 1000)
        self.assertIsNot(codec, poison)

    def test_native_order_sides_scale_and_source_ids(self):
        validated = self.bound_fixture()
        quotes = list(source.iter_quotes(validated))
        self.assertEqual([q.timestamp_ms for q in quotes],
                         [self.epoch*1000+1000, self.epoch*1000+1000,
                          self.epoch*1000+60000, self.epoch*1000+3599999])
        self.assertEqual([q.bid for q in quotes], [100.0, 100.1, 99.6, 100.0])
        self.assertEqual([q.ask for q in quotes], [100.25, 100.35, 99.85, 100.25])
        digest = validated["raw_receipts"][0].body_sha256
        self.assertEqual([q.source_id for q in quotes], [digest+":"+str(i) for i in range(4)])
        self.assertEqual(validated["rows"][self.epoch]["close"], 100.1)

    def test_raw_receipt_is_immutable(self):
        item = self.bound_fixture()["raw_receipts"][0]
        with self.assertRaises(FrozenInstanceError):
            item.body_sha256 = "0"*64

    def test_empty_200_produces_no_filled_minutes_or_quotes(self):
        self.fixture(empty=True)
        rows, hour, _ = self.validate_hour()
        self.assertEqual(rows, [])
        self.assertEqual(hour.tick_count, 0)
        self.assertEqual(list(source._iter_hour_quotes(self.root, hour, self.codec)), [])

    def test_missing_receipt_and_corrupt_body_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing or unreadable"):
            self.validate_hour()
        receipt, _, _ = self.fixture()
        (self.root / receipt["attempts"][-1]["body_file"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "body hash"):
            self.validate_hour()

    def test_rehashed_invalid_and_trailing_lzma_rejected(self):
        for kind in ("truncated", "trailing", "crossed", "outside_hour"):
            with self.subTest(kind=kind):
                receipt, _, body = self.fixture()
                if kind == "truncated":
                    replacement = body[:-2]
                elif kind == "trailing":
                    replacement = body + b"trailer"
                elif kind == "crossed":
                    replacement = lzma.compress(struct.pack(">IIIff", 1000, 99, 100, 1.0, 1.0))
                else:
                    replacement = lzma.compress(struct.pack(">IIIff", 3600000, 101, 100, 1.0, 1.0))
                self.replace_body(receipt, replacement)
                with self.assertRaises(ValueError):
                    self.validate_hour()

    def test_decompression_limit_is_enforced(self):
        self.fixture()
        with patch.object(self.codec, "MAX_DECOMPRESSED", 60):
            with self.assertRaisesRegex(ValueError, "size limit"):
                self.validate_hour()

    def test_arbitrary_body_path_and_symlink_rejected(self):
        receipt, _, body = self.fixture()
        receipt["attempts"][-1]["body_file"] = "../outside.bi5"
        self.save_receipt(receipt)
        with self.assertRaisesRegex(ValueError, "body path"):
            self.validate_hour()
        receipt, _, body = self.fixture()
        blob = self.root / receipt["attempts"][-1]["body_file"]
        other = self.write("elsewhere.bi5", body)
        blob.unlink()
        blob.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.validate_hour()

    def test_wrong_hour_route_and_boolean_count_rejected(self):
        for key in ("hour", "route", "count"):
            with self.subTest(key=key):
                receipt, _, _ = self.fixture()
                if key == "hour":
                    receipt["hour_start"] += 3600
                elif key == "route":
                    receipt["attempts"][-1]["wire_requested_url"] = "https://example.invalid/ticks"
                else:
                    receipt["attempts"][-1]["tick_count"] = True
                self.save_receipt(receipt)
                with self.assertRaises(ValueError):
                    self.validate_hour()

    def test_csv_must_reconcile_to_exact_raw_bid_ohlc(self):
        _, rows, _ = self.fixture()
        data = self.codec.csv_bytes(rows)
        parsed = source._reconcile(data, rows, self.codec, self.month)
        self.assertEqual(len(parsed), 3)
        altered = data.replace(b"100.100", b"100.101")
        with self.assertRaisesRegex(ValueError, "exactly reconcile"):
            source._reconcile(altered, rows, self.codec, self.month)

    def test_changed_body_receipt_and_rows_fail_second_iteration(self):
        for change in ("body", "receipt", "rows"):
            with self.subTest(change=change):
                validated = self.bound_fixture()
                hour = validated["raw_receipts"][0]
                if change == "body":
                    (self.root / hour.body_path).write_bytes(b"changed")
                elif change == "receipt":
                    with (self.root / hour.receipt_path).open("ab") as f:
                        f.write(b" ")
                else:
                    validated["rows"][self.epoch]["close"] += 1
                with self.assertRaisesRegex(ValueError, "changed after validation"):
                    list(source.iter_quotes(validated))

    def imported_fixture(self, side="BID", original_minute_delta=0):
        receipt, _, _ = self.fixture()
        original = copy.deepcopy(receipt)
        original["script_sha256"] = self.driver.CODEC_SHA
        original["protocol_sha256"] = "a"*64
        original["minute_count"] += original_minute_delta
        identity = dict(symbol="USATECHIDXUSD", scale=1000, side=side,
                        script_sha256=self.driver.CODEC_SHA, protocol_sha256="a"*64)
        original_raw, identity_raw = source._canonical(original), source._canonical(identity)
        receipt["import_provenance"] = dict(
            source_cache="/unavailable/original/cache/is/metadata/only",
            original_receipt=original, original_receipt_sha256=source._sha(original_raw),
            original_receipt_bytes_hex=original_raw.hex(), original_identity=identity,
            original_identity_sha256=source._sha(identity_raw), original_identity_bytes_hex=identity_raw.hex(),
            original_protocol_sha256="a"*64)
        self.save_receipt(receipt)
        return receipt

    def test_verified_legacy_import_is_read_without_original_path(self):
        self.imported_fixture()
        rows, hour, _ = self.validate_hour()
        self.assertEqual(len(rows), 3)
        self.assertEqual(hour.tick_count, 4)

    def test_legacy_import_wrong_feed_and_original_metadata_rejected(self):
        self.imported_fixture(side="ASK")
        with self.assertRaisesRegex(ValueError, "pinned BID"):
            self.validate_hour()
        self.imported_fixture(original_minute_delta=1)
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.validate_hour()

    def test_tampered_import_proof_is_rejected(self):
        receipt = self.imported_fixture()
        receipt["import_provenance"]["original_receipt_bytes_hex"] += "20"
        self.save_receipt(receipt)
        with self.assertRaisesRegex(ValueError, "provenance altered"):
            self.validate_hour()

    def identity_fixture(self):
        start, end = self.driver.parse_hour(source.START_UTC), self.driver.parse_hour(source.END_UTC)
        for name in ("FROZEN_MONTH_PROTOCOL.json", "TRANSPORT_AMENDMENT_V4.json"):
            target = "FROZEN_PROTOCOL.json" if name.startswith("FROZEN") else name
            self.write(target, self.captured[source.TOOLS + name])
        identity = dict(protocol_sha256=self.psha, script_sha256=self.ssha,
                        codec_sha256=self.driver.CODEC_SHA, symbol="USATECHIDXUSD", scale=1000,
                        raw_sides=["BID", "ASK"], derived_side="BID", start=start, end_exclusive=end,
                        parallel_requests=1, transport_amendment_sha256=source.FIXED[source.TOOLS + "TRANSPORT_AMENDMENT_V4.json"],
                        ordinary_attempts_per_hour_per_run=2, wire_route="direct-https")
        self.write("MONTH_IDENTITY.json", identity)
        return identity, start, end

    def test_direct_v4_identity_and_wrong_side(self):
        identity, start, end = self.identity_fixture()
        allowed, route = source._identity(self.root, {"wire_route":"direct-https"}, start, end,
                                          self.driver, self.captured, [])
        self.assertEqual(allowed, {self.ssha})
        self.assertEqual(route, "direct-https")
        identity["raw_sides"] = ["BID"]
        self.write("MONTH_IDENTITY.json", identity)
        with self.assertRaisesRegex(ValueError, "identity changed"):
            source._identity(self.root, {"wire_route":"direct-https"}, start, end,
                              self.driver, self.captured, [])

    def test_v3_to_v4_lineage_requires_exact_preserved_proof(self):
        identity, start, end = self.identity_fixture()
        old = {k:v for k,v in identity.items() if k != "wire_route"}
        old.update(script_sha256=self.driver.V3_SCRIPT_SHA,
                   transport_amendment_sha256=self.driver.V3_AMENDMENT_SHA)
        old_raw = source._canonical(old)
        self.write("MONTH_IDENTITY.json", old_raw)
        self.write("TRANSPORT_AMENDMENT_V3.json", (source.REPOSITORY / source.TOOLS /
                                                   "preserved-v3/TRANSPORT_AMENDMENT_V3.json").read_bytes())
        lineage = dict(schema="month-acquisition-code-lineage-v4", prior_identity_sha256=source._sha(old_raw),
                       prior_v3_lineage_sha256=None, prior_receipt_script_sha256=[self.driver.V3_SCRIPT_SHA],
                       current_identity=identity, does_not_retroactively_clear_prior_runs=True)
        self.write("TRANSPORT_IDENTITY_V4.json", lineage)
        allowed, _ = source._identity(self.root, {"wire_route":"direct-https"}, start, end,
                                      self.driver, self.captured, [])
        self.assertEqual(allowed, {self.ssha, self.driver.V3_SCRIPT_SHA})
        lineage["does_not_retroactively_clear_prior_runs"] = False
        self.write("TRANSPORT_IDENTITY_V4.json", lineage)
        with self.assertRaisesRegex(ValueError, "V4 identity lineage"):
            source._identity(self.root, {"wire_route":"direct-https"}, start, end,
                              self.driver, self.captured, [])

    def test_explicit_synthetic_manifest_cannot_be_promoted(self):
        self.write("MANIFEST.json", dict(all_hours_validated_or_explicit_empty_200=True,
                                         synthetic_fixture=True))
        with self.assertRaisesRegex(ValueError, "Synthetic fixtures"):
            source.validate_acquisition(self.root, self.protocol)

    def test_imported_synthetic_marker_cannot_be_hidden_by_wrapper(self):
        receipt = self.imported_fixture()
        del receipt["synthetic_fixture"]
        self.assertTrue(source._synthetic_evidence(receipt))
        self.assertFalse(source._synthetic_evidence({"import_provenance": {
            "original_receipt": {"synthetic_fixture": False}, "original_identity": {}}}))


if __name__ == "__main__":
    unittest.main()
