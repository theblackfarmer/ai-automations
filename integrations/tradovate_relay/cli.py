from __future__ import annotations
from .broadcasters import DiscordBroadcaster, MultiBroadcaster, TelegramBroadcaster
from .config import Settings
from .journal import Journal
from .service import RelayService
from .tradovate import run_stream

def main()->None:
    s=Settings.from_env(); s.validate_runtime()
    j=Journal(s.sqlite_path); j.init()
    outs=[]
    if s.discord_webhook_url: outs.append(DiscordBroadcaster(s.discord_webhook_url))
    if s.telegram_bot_token and s.telegram_chat_id: outs.append(TelegramBroadcaster(s.telegram_bot_token,s.telegram_chat_id))
    svc=RelayService(j,MultiBroadcaster(outs),set(s.account_ids))
    run_stream(s,svc.on_entity,svc.on_snapshot,svc.drain_outbox)

if __name__=="__main__": main()
