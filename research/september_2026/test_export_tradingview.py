"""Synthetic exporter/adapter contract tests only. No actual TradingView success claims."""
import copy
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

import export_tradingview as tv

SPEC = {"id": "SEPTEMBER_4H_1H_1M_V1", "execution_allowed": False,
        "scope": {"provider": "SYNTHETIC TEST", "symbol": "FAKE", "side": "BID", "timeframes_minutes": [240, 60, 1]},
        "mechanics": {"coverage": "Frozen UTC-phase0 aggregation"}}
SPEC_HASH = tv.sha(tv.encode(SPEC).encode())


def event(i=0, **updates):
    e = {"id": "synthetic-" + str(i), "kind": "ZONE_CANDIDATE", "known_at": 600 + i * 60,
         "timeframe": 1, "bar_start": 540 + i * 60, "direction": "long", "source_id": "synthetic",
         "execution_allowed": False, "authority": "ASSUMED_RESEARCH_ENCODING",
         "spec_hash": SPEC_HASH,
         "zone_id": "zone-" + str(i), "zone_low": 100, "zone_high": 102,
         "member_times": [300, 360], "origin_start": 300, "origin_end": 360,
         "selected_known_at": 600 + i * 60}
    e.update(updates)
    return e


class FakeAdapter:
    """A deterministic unit-test stub, not a transport, authentication or actual chart."""
    def __init__(self, cutoff=600, unit="seconds"):
        self.state = {"chart_id": "fake-chart", "symbol": "FAKE:TEST", "timeframe_minutes": 1,
                      "bar_type": "ordinary", "cutoff": cutoff}
        self.unit, self.entities, self.draws = unit, {}, 0
        self.mismatch, self.raise_readback = False, False

    def call(self, method, args):
        if method == "chart_state":
            return copy.deepcopy(self.state)
        if method == "draw_shape":
            self.draws += 1
            key = "FAKE-ENTITY-" + str(self.draws)
            self.entities[key] = {"entity_id": key, "shape": args["shape"],
                                  "points": [copy.deepcopy(args[k]) for k in ("point", "point2") if k in args],
                                  "time_unit": self.unit}
            if "text" in args:
                self.entities[key]["text"] = args["text"]
            return {"entity_id": key}
        if method == "get_properties":
            if self.raise_readback:
                raise RuntimeError("SYNTHETIC readback failure")
            r = copy.deepcopy(self.entities[args["entity_id"]])
            if self.mismatch:
                r["points"][0]["time"] -= 60
            return r
        raise AssertionError(method)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.events, self.spec = self.root / "events.jsonl", self.root / "spec.json"
        self.spec.write_text(json.dumps(SPEC))
        self.provenance = {"source_id": "synthetic", "source_sha256": "a" * 64, "engine_sha256": "b" * 64}

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, records=None, **kw):
        records = [event()] if records is None else records
        self.events.write_text("".join(json.dumps(e) + "\n" for e in records))
        meta = tv.export_bundle(self.events, self.spec, self.provenance, self.root / "out", chart_symbol="FAKE:TEST", **kw)
        self.package = self.root / "out/draw_bridge.jsonl"
        self.receipts = self.root / "receipts.jsonl"
        return meta

    def execute(self, adapter, **kw):
        args = {"cutoff": 600, "dry_run": False, "adapter": adapter,
                "validated_package_sha256": tv.sha(self.package.read_bytes()), "chart_id": "fake-chart",
                "chart_symbol": "FAKE:TEST", "time_unit": adapter.unit}
        args.update(kw)
        return tv.transfer(self.package, self.receipts, **args)

    def test_full_csv_retains_raw_records_and_context(self):
        original = event(link_id="exact-context", reference={"id": "ref", "kind": "high", "time": 240, "known_at": 480, "price": 104})
        meta = self.export([original])
        with (self.root / "out/events.csv").open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(json.loads(rows[0]["raw_event_json"]), original)
        self.assertEqual((self.root / "out/events.original.jsonl").read_bytes(), self.events.read_bytes())
        self.assertEqual(meta["event_sha256"], tv.sha(self.events.read_bytes()))
        self.assertEqual(meta["spec_sha256"], tv.sha(self.spec.read_bytes()))
        self.assertFalse(meta["coverage_validated_by_exporter"])

    def test_chunk_cap_and_all_rows_no_silent_drawing_gc(self):
        meta = self.export([event(i) for i in range(301)])
        self.assertEqual([c["count"] for c in meta["pine_chunks"]], [150, 150, 1])
        for chunk in meta["pine_chunks"]:
            text = (self.root / "out" / chunk["file"]).read_text()
            self.assertEqual(text.count("label.new("), chunk["count"])
            self.assertLessEqual(text.count("box.new("), 150)
        self.assertEqual(meta["event_count"], 301)

    def test_visibility_at_exact_known_close_and_no_origin_anchor(self):
        self.export()
        pine = (self.root / "out/recorded_0001.pine").read_text()
        self.assertIn("barstate.isconfirmed", pine)
        self.assertIn("time_close == 600000", pine)
        self.assertIn("label.new(600000", pine)
        self.assertIn("box.new(600000, 102, 600000, 100", pine)
        self.assertNotIn("label.new(300000", pine)
        self.assertIn("not chart.is_standard", pine)
        self.assertIn(tv.DISCLAIMER, pine)
        self.assertNotIn("request.security", pine)
        self.assertNotIn("strategy(", pine)

    def test_nongeometric_record_kept_without_inventing_price(self):
        e = event(kind="HTF_LINK")
        for key in ("zone_low", "zone_high"):
            del e[key]
        meta = self.export([e])
        self.assertEqual(meta["geometry_free_records"], 1)
        self.assertEqual(meta["drawable_operations"], 0)
        self.assertIn("yloc=yloc.abovebar", (self.root / "out/recorded_0001.pine").read_text())

    def test_duplicate_id_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.export([event(), event()])

    def test_out_of_order_refused(self):
        with self.assertRaisesRegex(ValueError, "chronological"):
            self.export([event(1), event()])

    def test_future_reference_refused(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.export([event(reference={"time": 480, "known_at": 660, "price": 103})])

    def test_future_member_refused(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.export([event(member_times=[660])])

    def test_missing_candle_source_refused(self):
        with self.assertRaisesRegex(ValueError, "source_id"):
            self.export([event(source_id=None)])

    def test_native_bar_ids_and_derived_null_remain_distinct_from_dataset(self):
        derived = event(1, kind="HTF_LINK", source_id=None, timeframe=None)
        del derived["bar_start"]
        meta = self.export([event(source_id="SYNTHETIC:1:540"), derived])
        self.assertEqual(meta["source_id"], "synthetic")
        self.assertEqual(meta["event_count"], 2)
        self.assertIn("CROSS-TF", (self.root / "out/recorded_0001.pine").read_text())

    def test_event_spec_hash_mismatch_refused(self):
        with self.assertRaisesRegex(ValueError, "spec_hash"):
            self.export([event(spec_hash="0" * 64)])

    def test_unknown_research_authority_refused(self):
        with self.assertRaisesRegex(ValueError, "authority"):
            self.export([event(authority="teacher_verified")])

    def test_execution_true_refused(self):
        with self.assertRaisesRegex(ValueError, "execution_allowed"):
            self.export([event(execution_allowed=True)])

    def test_bad_clocks_and_prices_refused(self):
        for change in ({"known_at": True}, {"known_at": 600.0}, {"zone_low": float("nan")},
                       {"known_at": 601}, {"zone_low": 103}, {"zone_high": None}, {"origin_start": 660}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                tv.validate_events([event(**change)], SPEC_HASH)

    def test_output_rewrite_refused(self):
        self.export()
        with self.assertRaisesRegex(ValueError, "preserve"):
            tv.export_bundle(self.events, self.spec, self.provenance, self.root / "out")

    def test_default_dryrun_no_adapter_calls_and_no_success(self):
        self.export([event(), event(1)])
        rows = tv.transfer(self.package, self.receipts, cutoff=600)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["known_at"] == 600 and r["status"] == "DRY_RUN" and r["readback"] is None for r in rows))
        self.assertTrue(all(r["chart_transfer_proven"] is False for r in rows))

    def test_earlier_cutoff_has_no_draws(self):
        self.export()
        self.assertEqual(tv.transfer(self.package, self.receipts, cutoff=599), [])

    def test_missing_validation_hash_blocks_before_mutation(self):
        self.export()
        adapter = FakeAdapter()
        with self.assertRaisesRegex(ValueError, "validated package"):
            self.execute(adapter, validated_package_sha256=None)
        self.assertEqual(adapter.draws, 0)

    def test_wrong_identity_cutoff_or_unit_blocks(self):
        self.export()
        for key, val in (("symbol", "WRONG"), ("chart_id", "WRONG"), ("timeframe_minutes", 60), ("bar_type", "heikinashi"), ("cutoff", 660)):
            adapter = FakeAdapter()
            adapter.state[key] = val
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.execute(adapter)
            self.assertEqual(adapter.draws, 0)
        with self.assertRaisesRegex(ValueError, "time unit"):
            self.execute(FakeAdapter(), time_unit=None)

    def test_exact_synthetic_readback_and_idempotence(self):
        self.export()
        adapter = FakeAdapter()
        rows = self.execute(adapter)
        self.assertEqual([r["status"] for r in rows], ["INTENT", "CREATED", "READBACK_MATCH", "INTENT", "CREATED", "READBACK_MATCH"])
        self.assertTrue(all(r["chart_transfer_proven"] is False for r in rows))
        second = self.execute(adapter)
        self.assertEqual(adapter.draws, 2)
        self.assertTrue(all(r["status"] == "ALREADY_PRESENT_READBACK_MATCH" for r in second))

    def test_milliseconds_conversion_and_receipt_canonical_seconds(self):
        self.export()
        rows = self.execute(FakeAdapter(unit="milliseconds"))
        matched = next(r for r in rows if r["status"] == "READBACK_MATCH")
        self.assertEqual(matched["intended_args"]["point"]["time"], 600)
        self.assertEqual(matched["readback"]["points"][0]["time"], 600000)

    def test_coordinate_shift_blocks_and_prevents_duplicate_retry(self):
        self.export()
        adapter = FakeAdapter()
        adapter.mismatch = True
        with self.assertRaisesRegex(ValueError, "differs"):
            self.execute(adapter)
        self.assertEqual(adapter.draws, 1)
        adapter.mismatch = False
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.execute(adapter)
        self.assertEqual(adapter.draws, 1)

    def test_lost_readback_preserves_intent_and_blocks_retry(self):
        self.export()
        adapter = FakeAdapter()
        adapter.raise_readback = True
        with self.assertRaisesRegex(RuntimeError, "SYNTHETIC"):
            self.execute(adapter)
        adapter.raise_readback = False
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.execute(adapter)
        self.assertEqual(adapter.draws, 1)

    def test_changed_existing_shape_not_silently_redrawn(self):
        self.export()
        adapter = FakeAdapter()
        self.execute(adapter)
        adapter.mismatch = True
        with self.assertRaisesRegex(ValueError, "changed"):
            self.execute(adapter)
        self.assertEqual(adapter.draws, 2)

    def test_backpaint_modified_package_rejected_even_in_dryrun(self):
        self.export()
        rows = [json.loads(x) for x in self.package.read_text().splitlines()]
        rows[1]["args"]["point"]["time"] = 300
        self.package.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
        with self.assertRaisesRegex(ValueError, "backpaint"):
            tv.transfer(self.package, self.receipts, cutoff=600)

    def test_receipt_path_aliases_cannot_mutate_package(self):
        self.export()
        before = self.package.read_bytes()
        for name, kind in (("same", "same"), ("symlink", "symlink"), ("hardlink", "hardlink")):
            dest = self.package if kind == "same" else self.root / name
            if kind == "symlink":
                dest.symlink_to(self.package)
            elif kind == "hardlink":
                os.link(self.package, dest)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "overwrite package"):
                tv.transfer(self.package, dest, cutoff=600)
            self.assertEqual(before, self.package.read_bytes())

    def test_empty_input_exports_explicit_zero_not_fake_chart(self):
        meta = self.export([])
        self.assertEqual(meta["event_count"], 0)
        self.assertEqual(meta["pine_chunks"], [])
        self.assertFalse(meta["chart_transfer_tested"])

    def test_explicit_case_projection_is_bound_and_chronological(self):
        meta = self.export([event(i) for i in range(4)], cases={"first-case": ["synthetic-0", "synthetic-2"]})
        self.assertEqual(meta["pine_cases"][0]["event_ids"], ["synthetic-0", "synthetic-2"])
        self.assertEqual((self.root / "out/case_0001.pine").read_text().count("label.new("), 2)
        self.assertEqual(meta["event_count"], 4)
        package = self.root / "out" / meta["pine_cases"][0]["bridge_file"]
        rows = [json.loads(x) for x in package.read_text().splitlines()]
        self.assertEqual({x["event_id"] for x in rows[1:]}, {"synthetic-0", "synthetic-2"})

    def test_case_unknown_or_reordered_ids_refused(self):
        for selected in (["missing"], ["synthetic-1", "synthetic-0"], ["synthetic-0", "synthetic-0"]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.export([event(), event(1)], cases={"case": selected})

    def test_same_zone_identity_cannot_change_bounds(self):
        with self.assertRaisesRegex(ValueError, "immutable bounds"):
            self.export([event(), event(1, zone_id="zone-0", zone_high=103)])

    def test_created_id_persisted_even_when_readback_lost(self):
        self.export()
        adapter = FakeAdapter()
        adapter.raise_readback = True
        with self.assertRaises(RuntimeError):
            self.execute(adapter)
        records = [json.loads(x) for x in self.receipts.read_text().splitlines()]
        self.assertEqual(records[1]["status"], "CREATED")
        self.assertEqual(records[1]["entity_id"], "FAKE-ENTITY-1")

    def test_rewind_existing_static_snapshot_is_not_certified(self):
        self.export([event(), event(1)])
        adapter = FakeAdapter(cutoff=660)
        self.execute(adapter, cutoff=660)
        adapter.state["cutoff"] = 600
        with self.assertRaisesRegex(ValueError, "rewind"):
            self.execute(adapter, cutoff=600)
        self.assertEqual(adapter.draws, 4)

    def test_same_events_in_repacked_bytes_do_not_duplicate(self):
        self.export()
        adapter = FakeAdapter()
        self.execute(adapter)
        self.package.write_text(self.package.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "another package"):
            self.execute(adapter)
        self.assertEqual(adapter.draws, 2)

    def test_chart_drift_after_create_is_uncertain(self):
        self.export()
        adapter = FakeAdapter()
        original = adapter.call
        def drift(method, args):
            response = original(method, args)
            if method == "draw_shape":
                adapter.state["symbol"] = "OTHER:CHART"
            return response
        adapter.call = drift
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.execute(adapter)
        self.assertEqual(adapter.draws, 1)
        self.assertEqual(json.loads(self.receipts.read_text().splitlines()[-1])["status"], "UNCERTAIN")

    def test_chart_rewind_during_existing_entity_readback_blocks_retry(self):
        self.export()
        adapter = FakeAdapter()
        self.execute(adapter)
        original = adapter.call
        def rewind(method, args):
            response = original(method, args)
            if method == "get_properties":
                adapter.state["cutoff"] = 540
            return response
        adapter.call = rewind
        with self.assertRaisesRegex(ValueError, "cutoff"):
            self.execute(adapter)
        self.assertEqual(adapter.draws, 2)


if __name__ == "__main__":
    unittest.main()
