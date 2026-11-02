from __future__ import annotations

from typing import Iterable, Optional
from .journal import Journal
from .models import Fill, SignalKind, TradeSignal


def _classify(old: int, new: int) -> SignalKind:
    if old == 0 and new != 0:
        return SignalKind.ENTRY
    if new == 0:
        return SignalKind.EXIT
    if (old > 0 > new) or (old < 0 < new):
        return SignalKind.REVERSE
    if abs(new) > abs(old):
        return SignalKind.ADD
    return SignalKind.REDUCE


class SignalEngine:
    def __init__(self, journal: Journal, targets: Iterable[str]):
        self.journal = journal
        self.targets = tuple(targets)

    def hydrate_position(self, account_id: int, contract_id: int, net_qty: int) -> None:
        self.journal.set_position(account_id, contract_id, net_qty)

    def hydrate_contract(self, contract_id: int, symbol: str) -> None:
        self.journal.set_contract(contract_id, symbol)

    def on_fill(self, fill: Fill) -> Optional[TradeSignal]:
        event_key = f"fill:{fill.fill_id}"
        if self.journal.seen(event_key):
            return None
        symbol = self.journal.get_symbol(fill.contract_id)
        if not symbol:
            raise RuntimeError(f"contract {fill.contract_id} is unresolved")
        old = self.journal.get_position(fill.account_id, fill.contract_id)
        new = old + fill.signed_quantity
        signal = TradeSignal(
            event_key=event_key,
            kind=_classify(old, new),
            account_id=fill.account_id,
            contract_id=fill.contract_id,
            symbol=symbol,
            side="LONG" if fill.signed_quantity > 0 else "SHORT",
            fill_quantity=abs(fill.signed_quantity),
            fill_price=fill.price,
            old_position=old,
            new_position=new,
            timestamp=fill.timestamp,
        )
        return signal if self.journal.commit_signal(signal, self.targets) else None
