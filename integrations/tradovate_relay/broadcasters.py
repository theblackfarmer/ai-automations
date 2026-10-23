from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Iterable, Protocol
from urllib import request

from .models import TradeSignal


class Broadcaster(Protocol):
    def send(self, signal: TradeSignal) -> None: ...


def _post_json(url: str, payload: dict, attempts: int = 3) -> None:
    data = json.dumps(payload).encode("utf-8")
    last_error = None
    for attempt in range(attempts):
        try:
            req = request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=8) as resp:
                if 200 <= resp.status < 300:
                    return
                raise RuntimeError(f"Webhook returned HTTP {resp.status}")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"Webhook delivery failed: {last_error}")


@dataclass
class DiscordBroadcaster:
    webhook_url: str

    def send(self, signal: TradeSignal) -> None:
        _post_json(self.webhook_url, {"content": signal.text()})


@dataclass
class TelegramBroadcaster:
    bot_token: str
    chat_id: str

    def send(self, signal: TradeSignal) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        _post_json(url, {"chat_id": self.chat_id, "text": signal.text()})


class MultiBroadcaster:
    def __init__(self, broadcasters: Iterable[Broadcaster]):
        self.broadcasters = list(broadcasters)

    def send(self, signal: TradeSignal) -> None:
        errors = []
        for broadcaster in self.broadcasters:
            try:
                broadcaster.send(signal)
            except Exception as exc:
                errors.append(exc)
        if errors and len(errors) == len(self.broadcasters):
            raise RuntimeError("All broadcasters failed") from errors[0]
