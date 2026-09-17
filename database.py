"""
database.py
------------
Lightweight, dependency-free trade journal backed by SQLite.

Every fill (paper or live) is written here. This is the single source of
truth that the Performance Analysis and Super Intelligence tabs read from.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    mode            TEXT NOT NULL,             -- Paper Trading / Live Trading
    instrument      TEXT NOT NULL,
    security_id     TEXT,
    exchange_segment TEXT,
    product_type    TEXT,                      -- INTRADAY / CNC / MTF / CO / BO
    instrument_type TEXT,                      -- EQUITY / FUTURE / OPTION
    side            TEXT NOT NULL,              -- BUY / SELL
    quantity        REAL NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    order_type      TEXT,
    status          TEXT,                       -- OPEN / CLOSED / REJECTED
    pnl             REAL,
    strategy_tag    TEXT,
    order_id        TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    action          TEXT NOT NULL,              -- BUY / SELL / HOLD
    confidence      TEXT NOT NULL,              -- LOW / MEDIUM / HIGH
    score           REAL,
    rationale       TEXT,                       -- newline-joined list of contributing signals
    human_decision  TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING / APPROVED / DISMISSED
    decided_at      TEXT,
    resulting_trade_id INTEGER                  -- linked TradeRecord.id if the human approved and it was executed
);
"""

_LOCK = threading.Lock()


@dataclass
class TradeRecord:
    instrument: str
    side: str
    quantity: float
    mode: str
    security_id: str = ""
    exchange_segment: str = ""
    product_type: str = ""
    instrument_type: str = "EQUITY"
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    order_type: str = "MARKET"
    status: str = "OPEN"
    pnl: Optional[float] = None
    strategy_tag: str = "manual"
    order_id: str = ""
    notes: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")


@dataclass
class RecommendationRecord:
    """
    An AI-generated (Super Intelligence) trade suggestion. Logging every
    recommendation — approved, dismissed, or never acted on — creates an
    audit trail showing the human always made the final call, which is the
    whole point of a human-in-the-loop design: the model advises, it never
    executes on its own.
    """

    instrument: str
    action: str            # BUY / SELL / HOLD
    confidence: str        # LOW / MEDIUM / HIGH
    score: float = 0.0
    rationale: str = ""    # newline-joined explanation strings
    human_decision: str = "PENDING"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")


class TradeJournal:
    """Thread-safe wrapper around a SQLite trades table."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    def log_trade(self, record: TradeRecord) -> int:
        """Insert a new trade row. Returns the new row's id."""
        with _LOCK, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades
                (timestamp, mode, instrument, security_id, exchange_segment,
                 product_type, instrument_type, side, quantity, entry_price,
                 exit_price, order_type, status, pnl, strategy_tag, order_id, notes)
                VALUES (:timestamp, :mode, :instrument, :security_id, :exchange_segment,
                        :product_type, :instrument_type, :side, :quantity, :entry_price,
                        :exit_price, :order_type, :status, :pnl, :strategy_tag, :order_id, :notes)
                """,
                asdict(record),
            )
            conn.commit()
            return cur.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, pnl: float, status: str = "CLOSED") -> None:
        with _LOCK, self._connect() as conn:
            conn.execute(
                "UPDATE trades SET exit_price = ?, pnl = ?, status = ? WHERE id = ?",
                (exit_price, pnl, status, trade_id),
            )
            conn.commit()

    def update_status_by_order_id(
        self,
        order_id: str,
        status: str,
        traded_price: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """
        Used by the live OrderUpdate WebSocket listener to keep the journal
        in sync with the broker's view of an order (e.g. PENDING -> TRADED,
        or -> REJECTED / CANCELLED) without waiting for a manual refresh.
        Returns True if a matching row was found and updated.
        """
        with _LOCK, self._connect() as conn:
            row = conn.execute("SELECT id, entry_price FROM trades WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                return False
            trade_id, entry_price = row
            new_entry_price = entry_price if entry_price not in (None, 0) else traded_price
            conn.execute(
                "UPDATE trades SET status = ?, entry_price = COALESCE(?, entry_price), "
                "notes = COALESCE(?, notes) WHERE id = ?",
                (status, new_entry_price, notes, trade_id),
            )
            conn.commit()
            return True

    def get_trade_by_order_id(self, order_id: str) -> Optional[dict]:
        """Minimal lookup used by the FIFO tracker to resolve a fill back to its journal row."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, security_id, side, quantity, mode FROM trades WHERE order_id = ?", (order_id,)
            ).fetchone()
            return dict(row) if row else None

    def accumulate_realized_pnl(
        self, trade_id: int, realized_delta: float, exit_price: float, close: bool = False
    ) -> None:
        """
        Adds `realized_delta` to a lot's running PnL (a single live BUY lot
        may be closed across several partial opposite-side fills) and
        records the latest exit price. Only flips status to CLOSED once the
        lot is fully offset — callers (the FIFO tracker) decide that.
        """
        with _LOCK, self._connect() as conn:
            conn.execute(
                "UPDATE trades SET pnl = COALESCE(pnl, 0) + ?, exit_price = ?, status = ? WHERE id = ?",
                (realized_delta, exit_price, "CLOSED" if close else "OPEN", trade_id),
            )
            conn.commit()

    def fetch_all(self) -> pd.DataFrame:
        with self._connect() as conn:
            df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    def fetch_open_positions(self, mode: Optional[str] = None) -> pd.DataFrame:
        df = self.fetch_all()
        if df.empty:
            return df
        df = df[df["status"] == "OPEN"]
        if mode:
            df = df[df["mode"] == mode]
        return df

    def export_csv(self, path: str) -> str:
        df = self.fetch_all()
        df.to_csv(path, index=False)
        return path

    # ---- Human-in-the-loop recommendation audit trail ----------------------
    def log_recommendation(self, record: "RecommendationRecord") -> int:
        """Records a Super Intelligence suggestion the instant it's shown to the trader."""
        with _LOCK, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO recommendations
                (timestamp, instrument, action, confidence, score, rationale, human_decision)
                VALUES (:timestamp, :instrument, :action, :confidence, :score, :rationale, :human_decision)
                """,
                asdict(record),
            )
            conn.commit()
            return cur.lastrowid

    def decide_recommendation(self, recommendation_id: int, decision: str, resulting_trade_id: Optional[int] = None) -> None:
        """Records the human's call — APPROVED or DISMISSED — closing the audit loop."""
        with _LOCK, self._connect() as conn:
            conn.execute(
                "UPDATE recommendations SET human_decision = ?, decided_at = ?, "
                "resulting_trade_id = COALESCE(?, resulting_trade_id) WHERE id = ?",
                (decision, datetime.now().isoformat(timespec="seconds"), resulting_trade_id, recommendation_id),
            )
            conn.commit()

    def fetch_recommendations(self) -> pd.DataFrame:
        with self._connect() as conn:
            df = pd.read_sql_query("SELECT * FROM recommendations ORDER BY timestamp DESC, id DESC", conn)
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
