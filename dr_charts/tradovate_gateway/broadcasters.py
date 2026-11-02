from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Protocol
from urllib import request

from .models import TradeSignal


class Broadcaster(Protocol):
    name: str
    def send(self, signal: TradeSignal) -> None: ...


def _post_json(url: str, payload: dict, attempts: int = 3) -> None:
    data = json.dumps(payload).encode("utf-8")
    last_error = None
    for attempt in range(attempts):
        try:
            req = request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "dr-charts-gateway/0.2"}, method="POST")
            with request.urlopen(req, timeout=8) as resp:
                if 200 <= resp.status < 300:
                    return
                raise RuntimeError(f"HTTP {resp.status}")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"delivery failed: {type(last_error).__name__}")


@dataclass
class DiscordBroadcaster:
    webhook_url: str
    name: str = "discord"
    def send(self, signal: TradeSignal) -> None:
        _post_json(self.webhook_url, {"content": signal.text()})


@dataclass
class TelegramBroadcaster:
    bot_token: str
    chat_id: str
    name: str = "telegram"
    def send(self, signal: TradeSignal) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        _post_json(url, {"chat_id": self.chat_id, "text": signal.text()})


class BroadcasterRegistry:
    def __init__(self, broadcasters: Iterable[Broadcaster]):
        self.by_name: Dict[str, Broadcaster] = {}
        for broadcaster in broadcasters:
            if broadcaster.name in self.by_name:
                raise ValueError(f"duplicate broadcaster name: {broadcaster.name}")
            self.by_name[broadcaster.name] = broadcaster
        if not self.by_name:
            raise ValueError("at least one broadcaster is required")

    @property
    def targets(self):
        return tuple(sorted(self.by_name))

    def send(self, target: str, signal: TradeSignal) -> None:
        if target not in self.by_name:
            raise KeyError(f"unknown delivery target: {target}")
        self.by_name[target].send(signal)
