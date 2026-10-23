from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet


def _csv_ints(value: str) -> FrozenSet[int]:
    return frozenset(int(x.strip()) for x in value.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    environment: str
    access_token: str
    user_id: int
    account_ids: FrozenSet[int]
    discord_webhook_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    sqlite_path: str
    stale_event_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            environment=os.getenv("TRADOVATE_ENV", "demo").lower(),
            access_token=os.getenv("TRADOVATE_ACCESS_TOKEN", ""),
            user_id=int(os.getenv("TRADOVATE_USER_ID", "0")),
            account_ids=_csv_ints(os.getenv("TRADOVATE_ACCOUNT_IDS", "")),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            sqlite_path=os.getenv("TRADOVATE_RELAY_DB", "tradovate_relay.sqlite3"),
            stale_event_seconds=int(os.getenv("TRADOVATE_STALE_EVENT_SECONDS", "300")),
        )

    @property
    def ws_url(self) -> str:
        host = "live.tradovateapi.com" if self.environment == "live" else "demo.tradovateapi.com"
        return f"wss://{host}/v1/websocket"

    def validate_runtime(self) -> None:
        if self.environment not in {"demo", "live"}:
            raise ValueError("TRADOVATE_ENV must be demo or live")
        if not self.access_token:
            raise ValueError("TRADOVATE_ACCESS_TOKEN is required")
        if self.user_id <= 0:
            raise ValueError("TRADOVATE_USER_ID must be set")
        if not self.account_ids:
            raise ValueError("TRADOVATE_ACCOUNT_IDS must contain at least one account id")
        if not (self.discord_webhook_url or (self.telegram_bot_token and self.telegram_chat_id)):
            raise ValueError("Configure Discord and/or Telegram output")
