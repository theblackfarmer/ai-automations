from __future__ import annotations

import math
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

    def __post_init__(self) -> None:
        if self.fill_id <= 0 or self.account_id <= 0 or self.contract_id <= 0:
            raise ValueError("fill/account/contract ids must be positive")
        if self.action not in {"Buy", "Sell"}:
            raise ValueError("fill action must be Buy or Sell")
        if int(self.quantity) <= 0:
            raise ValueError("fill quantity must be positive")
        if not math.isfinite(float(self.price)) or float(self.price) <= 0:
            raise ValueError("fill price must be positive and finite")
        if not self.timestamp:
            raise ValueError("fill timestamp is required")

    @property
    def signed_quantity(self) -> int:
        q = int(self.quantity)
        return q if self.action == "Buy" else -q


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
        return (
            f"{self.kind.value} {self.symbol} | {self.side} "
            f"{self.fill_quantity} @ {self.fill_price:g} | position {self.new_position:+d} "
            f"| event {self.event_key}"
        )
