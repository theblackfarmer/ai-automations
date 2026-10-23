from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

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
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS processed_events (event_key TEXT PRIMARY KEY, processed_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS positions (account_id INTEGER NOT NULL, contract_id INTEGER NOT NULL, net_qty INTEGER NOT NULL, PRIMARY KEY(account_id, contract_id))")
            c.execute("CREATE TABLE IF NOT EXISTS contracts (contract_id INTEGER PRIMARY KEY, symbol TEXT NOT NULL)")
            c.execute("""CREATE TABLE IF NOT EXISTS outbox (
                event_key TEXT NOT NULL,
                target TEXT NOT NULL,
                payload TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                PRIMARY KEY(event_key, target)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")

    def is_bootstrapped(self) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT value FROM metadata WHERE key='bootstrapped'").fetchone()
            return bool(row and row[0] == "1")

    def set_bootstrapped(self) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO metadata(key,value) VALUES('bootstrapped','1') ON CONFLICT(key) DO UPDATE SET value='1'")

    def seen(self, event_key: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM processed_events WHERE event_key=?", (event_key,)).fetchone() is not None

    def mark_seen(self, event_key: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO processed_events(event_key) VALUES(?)", (event_key,))

    def get_position(self, account_id: int, contract_id: int) -> int:
        with self._conn() as c:
            row = c.execute("SELECT net_qty FROM positions WHERE account_id=? AND contract_id=?", (account_id, contract_id)).fetchone()
            return int(row[0]) if row else 0

    def set_position(self, account_id: int, contract_id: int, net_qty: int) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO positions(account_id,contract_id,net_qty) VALUES(?,?,?) ON CONFLICT(account_id,contract_id) DO UPDATE SET net_qty=excluded.net_qty", (account_id, contract_id, int(net_qty)))

    def all_positions(self) -> dict:
        with self._conn() as c:
            return {(int(a), int(cid)): int(q) for a, cid, q in c.execute("SELECT account_id,contract_id,net_qty")}

    def set_contract(self, contract_id: int, symbol: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO contracts(contract_id,symbol) VALUES(?,?) ON CONFLICT(contract_id) DO UPDATE SET symbol=excluded.symbol", (contract_id, symbol))

    def get_symbol(self, contract_id: int) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT symbol FROM contracts WHERE contract_id=?", (contract_id,)).fetchone()
            return str(row[0]) if row else None

    def commit_signal(self, signal: TradeSignal, targets: Iterable[str]) -> bool:
        targets = tuple(sorted(set(targets)))
        if not targets:
            raise ValueError("At least one delivery target is required")
        with self._conn() as c:
            try:
                c.execute("INSERT INTO processed_events(event_key) VALUES(?)", (signal.event_key,))
            except sqlite3.IntegrityError:
                return False
            c.execute("INSERT INTO positions(account_id,contract_id,net_qty) VALUES(?,?,?) ON CONFLICT(account_id,contract_id) DO UPDATE SET net_qty=excluded.net_qty", (signal.account_id, signal.contract_id, signal.new_position))
            payload = json.dumps(signal.__dict__, default=lambda x: x.value, separators=(",", ":"))
            for target in targets:
                c.execute("INSERT INTO outbox(event_key,target,payload) VALUES(?,?,?)", (signal.event_key, target, payload))
            return True

    def pending(self) -> List[Tuple[str, str, str, int]]:
        with self._conn() as c:
            return c.execute("SELECT event_key,target,payload,attempts FROM outbox WHERE delivered=0 ORDER BY rowid").fetchall()

    def delivered(self, event_key: str, target: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE outbox SET delivered=1,last_error=NULL WHERE event_key=? AND target=?", (event_key, target))

    def failed_attempt(self, event_key: str, target: str, error: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE outbox SET attempts=attempts+1,last_error=? WHERE event_key=? AND target=?", (error[:500], event_key, target))

    def record_anomaly(self, kind: str, details: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO anomalies(kind,details) VALUES(?,?)", (kind, details[:2000]))
