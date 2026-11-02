# Dr. Charts Gateway Threat Model

## Protected invariants

1. A fill is never attributed to an account unless its parent order resolves to that account.
2. A fill's contract must match its parent order contract.
3. Only configured account IDs can generate notifications.
4. Historical fills on first attachment are baseline only.
5. Reconnect recovery processes unseen fills before accepting the new position snapshot.
6. Any local-vs-Tradovate position mismatch fails closed.
7. A fill ID can mutate local trading state at most once.
8. Delivery state is tracked independently for each destination.
9. Invalid trade fields never fall through to guessed defaults.
10. The gateway exposes no order-placement/cancel/modify method.

## Attack classes covered

- Duplicate/replayed events
- Fill-before-order and fill-before-contract ordering
- WebSocket disconnect/reconnect
- Partial downstream outages
- Process crash/restart
- Malformed or poisoned fill payloads
- Wrong-account events
- Fill/order contract disagreement
- Unauthorized webhook exfiltration host
- Snapshot/local state divergence

## Intentionally unresolved until live account connection

- Tradovate access-token acquisition and renewal parameters tied to the actual API account.
- Exact production account IDs and user ID.
- Production destination credentials.
- Infrastructure deployment choice and secret manager.

## External-system limitation

A durable local outbox cannot mathematically guarantee exactly-once presentation in Discord/Telegram when the destination does not provide a usable idempotency contract. The gateway therefore implements at-least-once delivery with stable event IDs and independent per-destination acknowledgement. A deduplicating intermediary can be added if strict downstream uniqueness is required.
