from __future__ import annotations
import json, time
from typing import Dict, Tuple
from .broadcasters import MultiBroadcaster
from .engine import SignalEngine
from .journal import Journal
from .models import Fill

class RelayService:
    def __init__(self, journal:Journal, broadcaster:MultiBroadcaster, allowed_accounts:set[int]):
        self.journal=journal; self.engine=SignalEngine(journal); self.broadcaster=broadcaster
        self.allowed_accounts=set(allowed_accounts); self.orders:Dict[int,Tuple[int,int]]={}

    def on_entity(self, entity_type:str, event_type:str, entity:dict)->None:
        if entity_type=="contract" and entity.get("id") and entity.get("name"):
            self.engine.hydrate_contract(int(entity["id"]),str(entity["name"])); return
        if entity_type=="order" and entity.get("id"):
            aid=entity.get("accountId"); cid=entity.get("contractId")
            if aid and cid: self.orders[int(entity["id"])]=(int(aid),int(cid))
            return
        if entity_type=="position":
            aid=entity.get("accountId"); cid=entity.get("contractId")
            if aid in self.allowed_accounts and cid:
                qty=int(entity.get("netPos", entity.get("netQty", 0)) or 0)
                self.engine.hydrate_position(int(aid),int(cid),qty)
            return
        if entity_type!="fill" or event_type not in {"Created","Updated"}: return
        oid=int(entity.get("orderId",0) or 0)
        resolved=self.orders.get(oid)
        if not resolved: return
        aid,cid=resolved
        if aid not in self.allowed_accounts: return
        fill=Fill(int(entity["id"]),aid,int(entity.get("contractId") or cid),str(entity["action"]),int(entity["qty"]),float(entity["price"]),str(entity["timestamp"]))
        self.engine.on_fill(fill)

    def drain_outbox(self)->None:
        from .models import SignalKind, TradeSignal
        for key,payload,_attempts in self.journal.pending():
            raw=json.loads(payload); raw["kind"]=SignalKind(raw["kind"])
            signal=TradeSignal(**raw)
            try:
                self.broadcaster.send(signal)
                self.journal.delivered(key)
            except Exception:
                self.journal.failed_attempt(key)
