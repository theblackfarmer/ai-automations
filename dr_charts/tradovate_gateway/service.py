from __future__ import annotations

import json
from typing import Dict, Tuple

from .broadcasters import BroadcasterRegistry
from .engine import SignalEngine
from .journal import Journal
from .models import Fill, SignalKind, TradeSignal


class ReconciliationError(RuntimeError):
    pass


class RelayService:
    MAX_PENDING_FILLS = 1000

    def __init__(self, journal: Journal, broadcasters: BroadcasterRegistry, allowed_accounts):
        self.journal = journal
        self.broadcasters = broadcasters
        self.allowed_accounts = set(allowed_accounts)
        self.engine = SignalEngine(journal, broadcasters.targets)
        self.orders: Dict[int, Tuple[int, int]] = {}
        self.pending_fills: Dict[int, dict] = {}

    def _remember_order(self, entity: dict) -> None:
        oid = int(entity.get("id", 0) or 0)
        aid = int(entity.get("accountId", 0) or 0)
        cid = int(entity.get("contractId", 0) or 0)
        if oid > 0 and aid > 0 and cid > 0:
            self.orders[oid] = (aid, cid)

    def _remember_contract(self, entity: dict) -> None:
        cid = int(entity.get("id", 0) or 0)
        name = str(entity.get("name", "") or "")
        if cid > 0 and name:
            self.engine.hydrate_contract(cid, name)

    def _queue_fill(self, entity: dict) -> None:
        fid = int(entity.get("id", 0) or 0)
        if fid <= 0:
            raise ValueError("fill id must be positive")
        self.pending_fills[fid] = dict(entity)
        if len(self.pending_fills) > self.MAX_PENDING_FILLS:
            self.journal.record_anomaly("pending_fill_overflow", str(len(self.pending_fills)))
            raise ReconciliationError("too many unresolved fills; failing closed")

    def _try_fill(self, entity: dict) -> bool:
        fid = int(entity.get("id", 0) or 0)
        oid = int(entity.get("orderId", 0) or 0)
        resolved = self.orders.get(oid)
        if not resolved:
            self._queue_fill(entity)
            return False
        aid, order_cid = resolved
        if aid not in self.allowed_accounts:
            self.pending_fills.pop(fid, None)
            return True
        cid = int(entity.get("contractId", 0) or order_cid)
        if cid != order_cid:
            self.journal.record_anomaly("contract_mismatch", f"fill={fid} order={oid} fill_contract={cid} order_contract={order_cid}")
            raise ReconciliationError("fill/order contract mismatch")
        if not self.journal.get_symbol(cid):
            self._queue_fill(entity)
            return False
        try:
            fill = Fill(
                fill_id=fid,
                account_id=aid,
                contract_id=cid,
                action=str(entity.get("action", "")),
                quantity=int(entity.get("qty", 0) or 0),
                price=float(entity.get("price", 0) or 0),
                timestamp=str(entity.get("timestamp", "") or ""),
            )
        except (TypeError, ValueError) as exc:
            self.journal.record_anomaly("invalid_fill", f"fill={fid}: {exc}")
            raise
        self.engine.on_fill(fill)
        self.pending_fills.pop(fid, None)
        return True

    def _drain_pending(self) -> None:
        progress = True
        while progress and self.pending_fills:
            progress = False
            for fid, entity in list(self.pending_fills.items()):
                before = fid in self.pending_fills
                if self._try_fill(entity) and before:
                    progress = True

    def on_snapshot(self, snapshot: dict) -> None:
        for entity in snapshot.get("contracts", []) or []:
            self._remember_contract(entity)
        for entity in snapshot.get("orders", []) or []:
            self._remember_order(entity)

        fills = sorted(
            snapshot.get("fills", []) or [],
            key=lambda x: (str(x.get("timestamp", "")), int(x.get("id", 0) or 0)),
        )
        positions = snapshot.get("positions", []) or []

        if not self.journal.is_bootstrapped():
            # First attachment is baseline-only: never broadcast historical fills.
            # But do not silently accept a snapshot whose fills cannot be attributed.
            for entity in fills:
                fid = int(entity.get("id", 0) or 0)
                oid = int(entity.get("orderId", 0) or 0)
                resolved = self.orders.get(oid)
                if fid <= 0:
                    self.journal.record_anomaly("invalid_baseline_fill", f"order={oid}")
                    raise ReconciliationError("baseline contains invalid fill id")
                if not resolved:
                    self.journal.record_anomaly("unresolved_baseline_fill", f"fill={fid} order={oid}")
                    raise ReconciliationError("baseline contains unresolved fill; failing closed")
                if resolved[0] in self.allowed_accounts:
                    self.journal.mark_seen(f"fill:{fid}")
            for p in positions:
                aid = int(p.get("accountId", 0) or 0)
                cid = int(p.get("contractId", 0) or 0)
                if aid in self.allowed_accounts and cid > 0:
                    self.engine.hydrate_position(aid, cid, int(p.get("netPos", 0) or 0))
            self.journal.set_bootstrapped()
            return

        # Reconnect recovery: replay only unseen fills against persisted pre-outage state.
        for entity in fills:
            fid = int(entity.get("id", 0) or 0)
            if fid > 0 and not self.journal.seen(f"fill:{fid}"):
                self._try_fill(entity)
        self._drain_pending()
        if self.pending_fills:
            unresolved = sorted(self.pending_fills)[:20]
            self.journal.record_anomaly("unresolved_snapshot_fills", json.dumps(unresolved))
            raise ReconciliationError("snapshot contains unresolved fills; failing closed")

        expected = {}
        for p in positions:
            aid = int(p.get("accountId", 0) or 0)
            cid = int(p.get("contractId", 0) or 0)
            if aid in self.allowed_accounts and cid > 0:
                expected[(aid, cid)] = int(p.get("netPos", 0) or 0)
        actual = self.journal.all_positions()
        keys = set(expected) | {k for k in actual if k[0] in self.allowed_accounts}
        mismatches = {k: (actual.get(k, 0), expected.get(k, 0)) for k in keys if actual.get(k, 0) != expected.get(k, 0)}
        if mismatches:
            self.journal.record_anomaly("position_reconciliation_failed", json.dumps({str(k): v for k, v in mismatches.items()}))
            raise ReconciliationError("position reconciliation failed; refusing to classify later fills")

    def on_entity(self, entity_type: str, event_type: str, entity: dict) -> None:
        if entity_type == "contract" and event_type != "Deleted":
            self._remember_contract(entity)
            self._drain_pending()
            return
        if entity_type == "order" and event_type != "Deleted":
            self._remember_order(entity)
            self._drain_pending()
            return
        if entity_type == "fill" and event_type in {"Created", "Updated"}:
            fid = int(entity.get("id", 0) or 0)
            if fid > 0 and self.journal.seen(f"fill:{fid}"):
                return
            self._try_fill(entity)
            return
        # Position events are not used to mutate classification state. Snapshot
        # reconciliation is authoritative and catches gaps without double-counting fills.

    def drain_outbox(self) -> None:
        for key, target, payload, _attempts in self.journal.pending():
            raw = json.loads(payload)
            raw["kind"] = SignalKind(raw["kind"])
            signal = TradeSignal(**raw)
            try:
                self.broadcasters.send(target, signal)
                self.journal.delivered(key, target)
            except Exception as exc:
                self.journal.failed_attempt(key, target, type(exc).__name__)
