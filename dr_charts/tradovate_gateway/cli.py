from __future__ import annotations

from .broadcasters import BroadcasterRegistry, DiscordBroadcaster, TelegramBroadcaster
from .config import Settings
from .journal import Journal
from .service import RelayService
from .tradovate import run_stream


def main() -> None:
    settings = Settings.from_env()
    settings.validate_runtime()
    journal = Journal(settings.sqlite_path)
    journal.init()

    outputs = []
    if settings.discord_webhook_url:
        outputs.append(DiscordBroadcaster(settings.discord_webhook_url))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        outputs.append(TelegramBroadcaster(settings.telegram_bot_token, settings.telegram_chat_id))

    registry = BroadcasterRegistry(outputs)
    service = RelayService(journal, registry, settings.account_ids)
    run_stream(settings, service.on_entity, service.on_snapshot, service.drain_outbox)


if __name__ == "__main__":
    main()
