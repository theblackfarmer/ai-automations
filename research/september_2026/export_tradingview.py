#!/usr/bin/env python3
"""Recorded research events -> CSV, bounded Pine playback, and dry-run-first drawings.

No detector, orders, network client, or implicit chart mutation. See EXPORT_GUIDE.txt.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DISCLAIMER = "RECORDED DECISION PLAYBACK | NOT LIVE DETECTOR | RESEARCH ONLY"
HEX = re.compile(r"[0-9a-f]{64}\Z")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, field):
    require(type(value) is int and value >= 0, f"{field}: nonnegative integer UTC seconds required")
    return value


def price(value, field):
    require(type(value) in (int, float) and math.isfinite(value) and value > 0,
            f"{field}: finite positive numeric price required")
    return value


def clock_text(seconds):
    dt = datetime.fromtimestamp(seconds, timezone.utc)
    return " / ".join(dt.astimezone(ZoneInfo(z)).strftime("%Y-%m-%d %H:%M:%S %Z")
                      for z in ("America/Chicago", "America/New_York"))


def validate_events(events, expected_spec_hash):
    seen, last, zones = set(), -1, {}
    for event in events:
        require(isinstance(event, dict), "event must be object")
        require(isinstance(event.get("id"), str) and event["id"], "event id required")
        require(event["id"] not in seen, "duplicate event id")
        seen.add(event["id"])
        require(isinstance(event.get("kind"), str) and event["kind"], "event kind required")
        known = integer(event.get("known_at"), "known_at")
        require(known % 60 == 0, "known_at must align to a completed UTC minute")
        require(known >= last, "event journal is not chronological; refusing to silently sort")
        last = known
        cross_tf = event["kind"] in ("HTF_LINK", "SCHEDULED_COVERAGE_BRIDGE", "UNCLASSIFIED_COVERAGE_GAP")
        require("timeframe" in event, "explicit timeframe field required")
        require((type(event.get("timeframe")) is int and event["timeframe"] in (1, 60, 240)) or
                (cross_tf and event.get("timeframe") is None), "timeframe must be 1/60/240 minutes or explicit cross-TF event")
        require(event.get("direction") in (None, "long", "short"), "invalid direction")
        require("source_id" in event and (event["source_id"] is None or
                (isinstance(event["source_id"], str) and event["source_id"])), "native event source_id must be string or explicit null")
        require("bar_start" not in event or event["source_id"] is not None, "candle-backed event requires native source_id")
        require(event.get("spec_hash") == expected_spec_hash, "event canonical spec_hash mismatch")
        require(event.get("execution_allowed") is False, "execution_allowed must explicitly be false")
        require(event.get("authority") == "ASSUMED_RESEARCH_ENCODING", "research authority required")
        for field in ("bar_start", "origin_start", "origin_end", "selected_known_at"):
            if field in event:
                require(integer(event[field], field) <= known, f"{field} is after known_at")
        if "origin_start" in event and "origin_end" in event:
            require(event["origin_start"] <= event["origin_end"], "reversed origin interval")
        if "member_times" in event:
            members = event["member_times"]
            require(isinstance(members, list), "member_times must be list")
            for t in members:
                require(integer(t, "member time") <= known, "future zone member")
            require(members == sorted(set(members)), "zone members must be unique and ordered")
        for field in ("reference", "pullback"):
            ref = event.get(field)
            if ref is not None:
                require(isinstance(ref, dict), f"{field} must be object")
                require(integer(ref.get("time"), field + ".time") <= integer(ref.get("known_at"), field + ".known_at") <= known,
                        f"future or reversed {field} clocks")
                price(ref.get("price"), field + ".price")
        has_low, has_high = "zone_low" in event, "zone_high" in event
        require(has_low == has_high, "both zone bounds required")
        if has_low:
            bounds = (price(event["zone_low"], "zone_low"), price(event["zone_high"], "zone_high"))
            require(bounds[0] <= bounds[1], "invalid zone bounds")
            if event.get("zone_id") is not None:
                zid = event["zone_id"]
                require(isinstance(zid, str) and zid, "zone_id must be nonempty string")
                require(zid not in zones or zones[zid] == bounds, "same zone_id changed immutable bounds")
                zones[zid] = bounds
    return events


def event_price(event):
    if "reference" in event and event["reference"] is not None:
        return event["reference"]["price"]
    if "zone_low" in event:
        return event["zone_low"] + (event["zone_high"] - event["zone_low"]) / 2
    return None


def label(event):
    timeframe = str(event["timeframe"]) + "M" if event["timeframe"] is not None else "CROSS-TF"
    return (f"RECORDED {event['kind']} | {timeframe} | {event.get('direction') or 'none'}\n"
            f"Known: {clock_text(event['known_at'])}\nID: {event['id']}")


def render_pine(events, metadata, chunk_index):
    """One <=150-record view. No derived detection or historical origin anchoring."""
    require(0 < len(events) <= 150, "Pine chunk requires 1..150 records")
    header = ["//@version=6", f'// {DISCLAIMER}',
              "// Exported static decisions: chart candle similarity is not source or detector parity.",
              "// Only the exact known-time closed 1M bar reveals each record; absent bars remain absent.",
              "// No future outcome colors, original-candle labels, or origin-time boxes.",
              "// Manifest: " + encode(metadata),
              f'indicator("Recorded research decisions {chunk_index:04d} - NOT LIVE", overlay=true, max_boxes_count=150, max_labels_count=150)',
              'expectedTicker = input.string(' + json.dumps(metadata["chart_symbol"]) + ', "Expected chart tickerid (required)")',
              'if barstate.isfirst and (not chart.is_standard or timeframe.in_seconds() != 60 or expectedTicker == "" or syminfo.tickerid != expectedTicker)',
              '    runtime.error("Use the declared ordinary 1M chart ticker. Recorded source feed is in the provenance table.")',
              'var table info = table.new(position.top_right, 1, 2)',
              'if barstate.islast',
              '    table.cell(info, 0, 0, ' + json.dumps(DISCLAIMER) + ')',
              '    table.cell(info, 0, 1, ' + json.dumps(metadata["provider"] + " | " + metadata["symbol"] + " | " + metadata["side"] + " | UTC phase 0 | " + metadata["event_sha256"][:12]) + ')']
    for idx, event in enumerate(events):
        known = event["known_at"] * 1000
        p = event_price(event)
        direction_color = "color.teal" if event.get("direction") == "long" else "color.orange" if event.get("direction") == "short" else "color.gray"
        header += [f"var bool emitted_{idx} = false",
                   f"if barstate.isconfirmed and not emitted_{idx} and time_close == {known}",
                   f"    emitted_{idx} := true",
                   f"    label.new({known}, {repr(p) if p is not None else 'na'}, {json.dumps(label(event))}, xloc=xloc.bar_time, yloc={'yloc.price' if p is not None else 'yloc.abovebar'}, style=label.style_label_down, color={direction_color}, textcolor=color.white, size=size.tiny)"]
        if "zone_low" in event:
            # A vertical box snapshot at the known timestamp, never extended into future bars.
            header += [f"    box.new({known}, {repr(event['zone_high'])}, {known}, {repr(event['zone_low'])}, xloc=xloc.bar_time, border_color={direction_color}, bgcolor=color.new({direction_color}, 85))"]
    return "\n".join(header) + "\n"


def draw_operations(event):
    """Canonical intended points are UTC seconds; convert only after adapter unit confirmation."""
    p = event_price(event)
    if p is None:
        return []  # No invented level for geometry-free records; retained in CSV and Pine abovebar labels.
    known = event["known_at"]
    args = [{"shape": "text", "point": {"time": known, "price": p}, "text": label(event)}]
    if "zone_low" in event:
        # Vertical range marker records the event snapshot without implying future validity.
        args.append({"shape": "trend_line", "point": {"time": known, "price": event["zone_low"]},
                     "point2": {"time": known, "price": event["zone_high"]}})
    return [{"type": "draw", "operation_id": sha((event["id"] + ":" + str(i)).encode()),
             "event_id": event["id"], "known_at": known, "canonical_time_unit": "seconds", "args": a}
            for i, a in enumerate(args)]


def export_bundle(events_path, spec_path, provenance, output_dir, *, case_size=150, chart_symbol="", cases=None):
    require(type(case_size) is int and 1 <= case_size <= 150, "case_size must be 1..150")
    raw, spec_raw = Path(events_path).read_bytes(), Path(spec_path).read_bytes()
    events = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    spec = json.loads(spec_raw)
    require(spec.get("id") == "SEPTEMBER_4H_1H_1M_V1" and spec.get("execution_allowed") is False, "unexpected frozen research spec")
    require("UTC-phase0" in spec["mechanics"]["coverage"], "explicit frozen phase 0 missing")
    for field in ("source_sha256", "engine_sha256"):
        require(isinstance(provenance.get(field), str) and HEX.fullmatch(provenance[field]), field + " required")
    require(isinstance(provenance.get("source_id"), str) and provenance["source_id"], "source_id required")
    canonical_spec_hash = sha(encode(spec).encode())
    validate_events(events, canonical_spec_hash)
    cases = {} if cases is None else cases
    require(isinstance(cases, dict), "cases must map case names to explicit event-id lists")
    by_id = {event["id"]: i for i, event in enumerate(events)}
    for case_id, selected in cases.items():
        require(isinstance(case_id, str) and case_id, "nonempty case name required")
        require(isinstance(selected, list) and 1 <= len(selected) <= 150, "case must contain 1..150 explicit event ids")
        require(all(isinstance(eid, str) and eid in by_id for eid in selected), "unknown case event id")
        indices = [by_id[eid] for eid in selected]
        require(indices == sorted(set(indices)), "case events must follow journal order without duplicates")
    output = Path(output_dir)
    require(not output.exists(), "output directory already exists; preserve prior exports")
    metadata = {"schema": "recorded-tv-export-v1", "disclaimer": DISCLAIMER,
                "spec_id": spec["id"], "spec_sha256": sha(spec_raw), "event_sha256": sha(raw),
                "canonical_spec_hash": canonical_spec_hash,
                "exporter_sha256": sha(Path(__file__).read_bytes()),
                "source_id": provenance["source_id"], "source_sha256": provenance["source_sha256"],
                "event_source_id_semantics": "native bar identity, or explicit null for derived events; not dataset identity",
                "engine_sha256": provenance["engine_sha256"], "provider": spec["scope"]["provider"],
                "symbol": spec["scope"]["symbol"], "side": spec["scope"]["side"],
                "timeframes_minutes": spec["scope"]["timeframes_minutes"], "utc_phase_seconds": 0,
                "chart_symbol": chart_symbol, "scope": spec["scope"], "event_count": len(events),
                "full_input_exported": True, "detector_reimplemented": False, "chart_transfer_tested": False,
                "pine_compiled": False, "execution_allowed": False, "teacher_fidelity": False,
                "source_and_engine_hashes_supplied_by_caller": True,
                "source_market_bytes_read_by_exporter": False,
                "coverage_validated_by_exporter": False, "event_causality_validated_by_exporter": False,
                "bridge_display_mode": "STATIC_CUTOFF_SNAPSHOT_NOT_REPLAY_AUTOMATION"}
    output.mkdir(parents=True)
    (output / "events.original.jsonl").write_bytes(raw)
    (output / "SPEC.original.json").write_bytes(spec_raw)
    with (output / "events.csv").open("w", newline="") as handle:
        fields = ["id", "kind", "known_at", "known_ct_et", "timeframe", "direction", "source_id", "physical_origin_id", "zone_low", "zone_high", "reference", "member_times", "raw_event_json"]
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        for event in events:
            row = {k: event.get(k, "") for k in fields}
            row.update(known_ct_et=clock_text(event["known_at"]), raw_event_json=encode(event))
            for k in ("reference", "member_times"):
                row[k] = encode(event[k]) if k in event else ""
            writer.writerow(row)
    chunks = []
    for start in range(0, len(events), case_size):
        part = events[start:start + case_size]
        name = f"recorded_{len(chunks) + 1:04d}.pine"
        data = render_pine(part, metadata, len(chunks) + 1).encode()
        (output / name).write_bytes(data)
        chunks.append({"file": name, "sha256": sha(data), "first_event_index": start,
                       "count": len(part), "first_known_at": part[0]["known_at"], "last_known_at": part[-1]["known_at"]})
    case_exports = []
    for number, (case_id, selected) in enumerate(cases.items(), 1):
        name = f"case_{number:04d}.pine"
        part = [events[by_id[eid]] for eid in selected]
        case_meta = {**metadata, "case_id": case_id, "case_event_ids": selected,
                     "case_selection": "EXPLICIT_CALLER_SELECTION_NOT_OUTCOME_RANKED_BY_EXPORTER"}
        data = render_pine(part, case_meta, number).encode()
        (output / name).write_bytes(data)
        case_bridge_name = f"draw_bridge_case_{number:04d}.jsonl"
        case_operations = [op for event in part for op in draw_operations(event)]
        case_bridge = "\n".join(encode(x) for x in [{"type": "manifest", "metadata": case_meta}] + case_operations) + "\n"
        (output / case_bridge_name).write_text(case_bridge)
        case_exports.append({"case_id": case_id, "file": name, "sha256": sha(data), "event_ids": selected,
                             "bridge_file": case_bridge_name, "bridge_sha256": sha(case_bridge.encode())})
    operations = [operation for event in events for operation in draw_operations(event)]
    bridge = "\n".join(encode(x) for x in [{"type": "manifest", "metadata": metadata}] + operations) + "\n"
    (output / "draw_bridge.jsonl").write_text(bridge)
    metadata.update(pine_chunks=chunks, pine_cases=case_exports, drawable_operations=len(operations),
                    geometry_free_records=sum(event_price(e) is None for e in events),
                    bridge_sha256=sha(bridge.encode()))
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


class CommandAdapter:
    """Explicit caller-supplied JSON request/response shim; no shell or bundled TV connection."""
    def __init__(self, command):
        self.command = shlex.split(command)
        require(bool(self.command), "adapter command missing")

    def call(self, method, args):
        result = subprocess.run(self.command, input=encode({"method": method, "args": args}),
                                text=True, capture_output=True, check=True, timeout=30)
        return json.loads(result.stdout)


def transfer(package_path, receipts_path, *, cutoff, dry_run=True, adapter=None,
             validated_package_sha256=None, chart_id=None, chart_symbol=None, time_unit=None):
    """Append real observations or dry-run intentions. Pending/uncertain writes cannot auto-retry."""
    integer(cutoff, "cutoff")
    raw = Path(package_path).read_bytes()
    package_hash = sha(raw)
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    require(rows and rows[0].get("type") == "manifest", "bridge manifest required")
    metadata = rows[0]["metadata"]
    require(metadata.get("execution_allowed") is False and metadata.get("detector_reimplemented") is False,
            "recorded research package required")
    operations, ids = [], set()
    for op in rows[1:]:
        require(op.get("type") == "draw" and op.get("canonical_time_unit") == "seconds", "invalid operation")
        require(isinstance(op.get("operation_id"), str) and op["operation_id"] not in ids, "duplicate operation")
        ids.add(op["operation_id"])
        known = integer(op.get("known_at"), "known_at")
        a = op.get("args", {})
        require(a.get("shape") in ("text", "trend_line"), "only recorded text/range markers supported")
        require("point" in a, "point missing")
        for point in [a["point"]] + ([a["point2"]] if "point2" in a else []):
            require(integer(point.get("time"), "point time") == known, "backpaint/future point refused")
            price(point.get("price"), "point price")
        if known <= cutoff:
            operations.append(op)
    def check_chart():
        state = adapter.call("chart_state", {})
        require(state.get("chart_id") == chart_id and state.get("symbol") == chart_symbol and state.get("timeframe_minutes") == 1,
                "chart identity mismatch")
        require(state.get("bar_type") == "ordinary" and state.get("cutoff") == cutoff, "ordinary chart with exact requested visible cutoff required")

    if not dry_run:
        require(validated_package_sha256 == package_hash, "explicit validated package hash required")
        require(adapter is not None and chart_id and chart_symbol, "adapter and chart identity required")
        require(time_unit in ("seconds", "milliseconds"), "confirmed adapter time unit required")
        check_chart()
        require(metadata.get("chart_symbol") == chart_symbol, "package chart symbol mismatch")
    receipts = Path(receipts_path)
    require(receipts.resolve() != Path(package_path).resolve() and
            not (receipts.exists() and os.path.samefile(receipts, package_path)), "receipts cannot overwrite package")
    results = []
    with receipts.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        prior = [json.loads(line) for line in handle if line.strip()]
        if not dry_run:
            previous = [x for x in prior if x.get("status") != "DRY_RUN" and x.get("chart_id") == chart_id]
            require(not any(x["known_at"] > cutoff for x in previous),
                    "rewind would expose existing future markers; clear owned snapshot or use fresh chart")
            require(not any(x["operation_id"] in ids and x["package_sha256"] != package_hash for x in previous),
                    "operation already belongs to another package; reconcile instead of duplicating")
        statuses = {(x["package_sha256"], x["operation_id"]): x for x in prior if x.get("status") != "DRY_RUN"}

        def persist(row):
            handle.seek(0, 2)
            handle.write(encode(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            results.append(row)

        for op in operations:
            if not dry_run:
                check_chart()
            key = (package_hash, op["operation_id"])
            old = statuses.get(key)
            if not dry_run and old:
                require(old["status"] == "READBACK_MATCH", "prior unresolved draw intent requires reconciliation")
                require(old.get("chart_id") == chart_id, "prior receipt belongs to different chart")
                readback = adapter.call("get_properties", {"entity_id": old["entity_id"]})
                check_chart()
                require(readback == old["readback"], "existing entity readback changed; no duplicate redraw")
                results.append({**old, "status": "ALREADY_PRESENT_READBACK_MATCH"})
                continue
            row = {"package_sha256": package_hash, "operation_id": op["operation_id"], "event_id": op["event_id"],
                   "known_at": op["known_at"], "cutoff": cutoff, "intended_points_unit": "seconds",
                   "intended_args": op["args"], "readback": None, "chart_id": chart_id,
                   "recorded_at": datetime.now(timezone.utc).isoformat(), "status": "DRY_RUN" if dry_run else "INTENT",
                   "chart_transfer_proven": False, "execution_allowed": False}
            persist(row)
            if dry_run:
                continue
            created = None
            try:
                args = json.loads(encode(op["args"]))
                factor = 1000 if time_unit == "milliseconds" else 1
                for point_key in ("point", "point2"):
                    if point_key in args:
                        args[point_key]["time"] *= factor
                created = adapter.call("draw_shape", args)
                require(isinstance(created.get("entity_id"), str) and created["entity_id"], "adapter did not return real entity id")
                persist({**row, "status": "CREATED", "entity_id": created["entity_id"], "adapter_response": created})
                readback = adapter.call("get_properties", {"entity_id": created["entity_id"]})
                check_chart()
                expected = [args[k] for k in ("point", "point2") if k in args]
                matched = (readback.get("entity_id") == created["entity_id"] and readback.get("shape") == args["shape"]
                           and readback.get("points") == expected and readback.get("time_unit") == time_unit
                           and (args["shape"] != "text" or readback.get("text") == args["text"]))
                result = {**row, "entity_id": created["entity_id"], "readback": readback,
                          "status": "READBACK_MATCH" if matched else "READBACK_MISMATCH"}
                persist(result)
                require(matched, "drawing readback differs from requested geometry/text; halted")
            except Exception as exc:
                persist({**row, "status": "UNCERTAIN", "error": str(exc), "adapter_response": created})
                raise
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Create immutable recorded-event export directory")
    for name in ("events", "spec", "provenance", "output"):
        export.add_argument("--" + name, required=True)
    export.add_argument("--case-size", type=int, default=150)
    export.add_argument("--chart-symbol", default="")
    export.add_argument("--cases", help="JSON object mapping case names to explicit ordered event-id lists, max 150 each")
    bridge = commands.add_parser("transfer", help="Dry-run by default; execution needs explicit hash, shim and chart gates")
    bridge.add_argument("--package", required=True)
    bridge.add_argument("--receipts", required=True)
    bridge.add_argument("--cutoff", type=int, required=True)
    bridge.add_argument("--execute", action="store_true")
    bridge.add_argument("--adapter-command")
    bridge.add_argument("--validated-package-sha256")
    bridge.add_argument("--chart-id")
    bridge.add_argument("--chart-symbol")
    bridge.add_argument("--time-unit", choices=("seconds", "milliseconds"))
    args = parser.parse_args()
    if args.command == "export":
        result = export_bundle(args.events, args.spec, json.loads(Path(args.provenance).read_text()), args.output,
                               case_size=args.case_size, chart_symbol=args.chart_symbol,
                               cases=json.loads(Path(args.cases).read_text()) if args.cases else None)
    else:
        result = transfer(args.package, args.receipts, cutoff=args.cutoff, dry_run=not args.execute,
                          adapter=CommandAdapter(args.adapter_command) if args.execute and args.adapter_command else None,
                          validated_package_sha256=args.validated_package_sha256, chart_id=args.chart_id,
                          chart_symbol=args.chart_symbol, time_unit=args.time_unit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
