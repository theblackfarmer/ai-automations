from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .models import TradeSignal


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
            c.execute("CREATE TABLE IF NOT EXISTS processed_events (event_key TEXT PRIMARY KEY, processed_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS positions (account_id INTEGER NOT NULL, contract_id INTEGER NOT NULL, net_qty INTEGER NOT NULL, PRIMARY KEY(account_id, contract_id))")
            c.execute("CREATE TABLE IF NOT EXISTS contracts (contract_id INTEGER PRIMARY KEY, symbol TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS outbox (event_key TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0)")

    def seen(self, event_key: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM processed_events WHERE event_key=?", (event_key,)).fetchone() is not None

    def get_position(self, account_id: int, contract_id: int) -> int:
        with self._conn() as c:
            row=c.execute("SELECT net_qty FROM positions WHERE account_id=? AND contract_id=?", (account_id, contract_id)).fetchone()
            return int(row[0]) if row else 0

    def set_position(self, account_id: int, contract_id: int, net_qty: int) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO positions(account_id,contract_id,net_qty) VALUES(?,?,?) ON CONFLICT(account_id,contract_id) DO UPDATE SET net_qty=excluded.net_qty",(account_id,contract_id,int(net_qty)))

    def set_contract(self, contract_id: int, symbol: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO contracts(contract_id,symbol) VALUES(?,?) ON CONFLICT(contract_id) DO UPDATE SET symbol=excluded.symbol",(contract_id,symbol))

    def get_symbol(self, contract_id: int) -> Optional[str]:
        with self._conn() as c:
            row=c.execute("SELECT symbol FROM contracts WHERE contract_id=?",(contract_id,)).fetchone()
            return str(row[0]) if row else None

    def commit_signal(self, signal: TradeSignal) -> bool:
        with self._conn() as c:
            try:
                c.execute("INSERT INTO processed_events(event_key) VALUES(?)",(signal.event_key,))
            except sqlite3.IntegrityError:
                return False
            c.execute("INSERT INTO positions(account_id,contract_id,net_qty) VALUES(?,?,?) ON CONFLICT(account_id,contract_id) DO UPDATE SET net_qty=excluded.net_qty",(signal.account_id,signal.contract_id,signal.new_position))
            c.execute("INSERT INTO outbox(event_key,payload) VALUES(?,?)",(signal.event_key,json.dumps(signal.__dict__, default=lambda x: x.value)))
            return True

    def pending(self):
        with self._conn() as c:
            return c.execute("SELECT event_key,payload,attempts FROM outbox WHERE delivered=0 ORDER BY rowid").fetchall()

    def delivered(self,event_key:str)->None:
        with self._conn() as c:
            c.execute("UPDATE outbox SET delivered=1 WHERE event_key=?",(event_key,))

    def failed_attempt(self,event_key:str)->None:
        with self._conn() as c:
            c.execute("UPDATE outbox SET attempts=attempts+1 WHERE event_key=?",(event_key,))
