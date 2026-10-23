# ASTRA 6 Tradovate Signal Relay

Read-only live relay for broadcasting Tradovate fills to private chat destinations. It does not place, modify, cancel, or copy orders.

## Reliability model
- Tradovate WebSocket `user/syncrequest` for real-time user events.
- Initial snapshot hydrates contracts, orders and positions without replaying historical fills.
- Fill -> parent order resolution attributes each fill to the correct account.
- Explicit account allow-list.
- SQLite WAL journal for crash-safe state.
- Durable outbox: a fill and its notification are committed together.
- Fill ID idempotency prevents duplicate broadcasts after reconnect/replay.
- Exponential reconnect backoff.
- Discord and Telegram adapters are independent; one can fail without blocking the other.

## Setup
1. `python -m pip install -r requirements-live.txt`
2. Copy `.env.tradovate.example` values into your secret manager/environment.
3. Start in `TRADOVATE_ENV=demo`.
4. Run `python -m integrations.tradovate_relay.cli`.

## Credentials intentionally not committed
The live access token, user ID, account IDs and chat credentials must stay outside GitHub. Tradovate recommends keeping API credentials out of public source and using 2FA.

## Before live activation
Run the unit tests, connect a demo account, execute entry/add/reduce/exit/reversal cases, intentionally disconnect/reconnect the network, and confirm exactly-once chat delivery. Only after that should `TRADOVATE_ENV` be changed to `live`.

This package is intentionally separate from ASTRA 6 strategy logic. A future ASTRA execution adapter should publish through a separate permissioned interface so a bug in chat broadcasting cannot place trades.
