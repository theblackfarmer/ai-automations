# Dr. Charts Tradovate Live Gateway

Read-only infrastructure for turning Dr. Charts' own Tradovate account activity into private chat notifications. This package cannot place, modify, cancel, or copy orders.

## Design goals

- Fail closed on ambiguous or inconsistent trading state.
- Never guess an account, side, contract, quantity, or price.
- Survive disconnects and process restarts without silently losing known fills.
- Keep each downstream chat destination independently acknowledged.
- Never rebroadcast historical fills on the first attachment to an account.
- Recover unseen fills after reconnect, then reconcile against Tradovate's authoritative position snapshot.
- Keep ASTRA 6 completely outside this project boundary.

## Reliability model

Tradovate's user synchronization snapshot includes orders, positions and fills. On first startup the gateway records existing fills as baseline and hydrates current positions without broadcasting history. On later reconnects it processes only unseen fills against persisted pre-disconnect state, then compares the resulting local position to Tradovate's position snapshot. A mismatch raises ReconciliationError and stops classification rather than sending possibly wrong alerts.

Real-time fills that arrive before their parent order or contract are buffered instead of discarded. The pending buffer is bounded; overflow fails closed.

SQLite runs in WAL mode with synchronous FULL durability. Signal creation, position mutation, processed-event marking, and per-destination outbox creation occur in one transaction.

## Delivery semantics

The gateway is durable at-least-once delivery to external chat APIs. Discord and Telegram do not provide a general end-to-end idempotency guarantee for this webhook pattern. A process crash after a remote service accepts a message but before the local acknowledgement is committed can therefore produce a duplicate on retry.

Every message includes a stable event fill:<id> key so downstream bots or humans can identify duplicates. No system should claim strict exactly-once external delivery unless the final destination supports an idempotency key or a deduplicating intermediary is added.

## Six adversarial agents

CI runs six independent attack suites:

1. Protocol Breaker - WebSocket framing, heartbeats, close frames, webhook host validation.
2. Event Ordering Breaker - fill-before-order, unresolved dependencies, contract mismatch.
3. Reconnect Breaker - first-start baseline, unseen-fill recovery, position reconciliation failure.
4. Delivery Breaker - partial destination outage and independent retry/ack state.
5. Validation/Security Breaker - bad side, bad quantity, invalid price, unapproved account.
6. Crash-State Breaker - restart persistence, duplicate suppression, pending-delivery survival.

A separate Python 3.11 job reruns the complete suite; the six named agents run on Python 3.9.

## Live-account details intentionally absent

The account token, user ID, account IDs, Discord webhook and Telegram credentials are environment-only. Tradovate access tokens expire; the final live-account integration must include the account's token-acquisition/renewal lifecycle and should use a dedicated API user with 2FA where supported.

## Start in demo

Install requirements-drcharts-live.txt, populate the DRCHARTS_* environment variables from .env.drcharts.example, and run:

python -m dr_charts.tradovate_gateway.cli

Do not switch to live until demo disconnect/reconnect, partial-fill, reversal, destination-outage, and process-kill drills have all passed with the exact account configuration that will be used in production.
