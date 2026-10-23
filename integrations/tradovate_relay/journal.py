from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple


class Journal:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS processed_events (
                    event_key TEXT PRIMARY KEY,
                    processed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS positions (
                    account_id INTEGER NOT NULL,
                    contract_id INTEGER NOT NULL,
                    net_qty INTEGER NOT NULL,
                    PRIMARY KEY(account_id, contract_id)
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS contracts (
                    contract_id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL
                )"""
            )

    def seen(self, event_key: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM processed_events WHERE event_key=?", (event_key,)
            ).fetchone()
            return row is not None

    def mark_seen(self, event_key: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO processed_events(event_key) VALUES (?)",
                (event_key,),
            )

    def get_position(self, account_id: int, contract_id: int) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT net_qty FROM positions WHERE account_id=? AND contract_id=?",
                (account_id, contract_id),
            ).fetchone()
            return int(row[0]) if row else 0

    def set_position(self, account_id: int, contract_id: int, net_qty: int) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO positions(account_id, contract_id, net_qty)
                   VALUES (?, ?, ?)
                   ON CONFLICT(account_id, contract_id)
                   DO UPDATE SET net_qty=excluded.net_qty""",
                (account_id, contract_id, int(net_qty)),
            )

    def set_contract(self, contract_id: int, symbol: str) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO contracts(contract_id, symbol) VALUES (?, ?)
                   ON CONFLICT(contract_id) DO UPDATE SET symbol=excluded.symbol""",
                (contract_id, symbol),
            )

    def get_symbol(self, contract_id: int) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT symbol FROM contracts WHERE contract_id=?", (contract_id,)
            ).fetchone()
            return str(row[0]) if row else None
