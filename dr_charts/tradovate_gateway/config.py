from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet
from urllib.parse import urlparse


def _csv_ints(value: str) -> FrozenSet[int]:
    return frozenset(int(x.strip()) for x in value.split(",") if x.strip())


def _valid_discord_webhook(url: str) -> bool:
    if not url:
        return True
    p = urlparse(url)
    return (
        p.scheme == "https"
        and p.hostname in {"discord.com", "discordapp.com"}
        and p.path.startswith("/api/webhooks/")
    )


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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            environment=os.getenv("DRCHARTS_TRADOVATE_ENV", "demo").lower(),
            access_token=os.getenv("DRCHARTS_TRADOVATE_ACCESS_TOKEN", ""),
            user_id=int(os.getenv("DRCHARTS_TRADOVATE_USER_ID", "0")),
            account_ids=_csv_ints(os.getenv("DRCHARTS_TRADOVATE_ACCOUNT_IDS", "")),
            discord_webhook_url=os.getenv("DRCHARTS_DISCORD_WEBHOOK_URL", ""),
            telegram_bot_token=os.getenv("DRCHARTS_TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("DRCHARTS_TELEGRAM_CHAT_ID", ""),
            sqlite_path=os.getenv("DRCHARTS_GATEWAY_DB", "runtime/drcharts_gateway.sqlite3"),
        )

    @property
    def ws_url(self) -> str:
        host = "live.tradovateapi.com" if self.environment == "live" else "demo.tradovateapi.com"
        return f"wss://{host}/v1/websocket"

    def validate_runtime(self) -> None:
        if self.environment not in {"demo", "live"}:
            raise ValueError("DRCHARTS_TRADOVATE_ENV must be demo or live")
        if not self.access_token:
            raise ValueError("Tradovate access token is required")
        if self.user_id <= 0:
            raise ValueError("Tradovate user id must be positive")
        if not self.account_ids or any(x <= 0 for x in self.account_ids):
            raise ValueError("At least one positive Tradovate account id is required")
        if not _valid_discord_webhook(self.discord_webhook_url):
            raise ValueError("Discord webhook must use an approved Discord HTTPS host/path")
        telegram_ok = bool(self.telegram_bot_token and self.telegram_chat_id)
        if not (self.discord_webhook_url or telegram_ok):
            raise ValueError("Configure Discord and/or Telegram output")
