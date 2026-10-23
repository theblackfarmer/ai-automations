from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SignalKind(str, Enum):
    ENTRY = "ENTRY"
    ADD = "ADD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    REVERSE = "REVERSE"


@dataclass(frozen=True)
class Fill:
    fill_id: int
    account_id: int
    contract_id: int
    action: str
    quantity: int
    price: float
    timestamp: str

    @property
    def signed_quantity(self) -> int:
        q = abs(int(self.quantity))
        return q if self.action.lower() == "buy" else -q


@dataclass(frozen=True)
class TradeSignal:
    event_key: str
    kind: SignalKind
    account_id: int
    contract_id: int
    symbol: str
    side: str
    fill_quantity: int
    fill_price: float
    old_position: int
    new_position: int
    timestamp: str
    realized_hint: Optional[str] = None

    def text(self) -> str:
        pos = f"{self.new_position:+d}"
        return (
            f"{self.kind.value} {self.symbol} | {self.side} "
            f"{self.fill_quantity} @ {self.fill_price:g} | position {pos}"
        )
