"""
core/live_fifo_tracker.py
----------------------------
Closes the gap flagged in the README: Dhan's OrderUpdate WebSocket confirms
that an order filled, but not whether that fill opened a new position or
closed an existing one. This module does that netting itself, the same way
a real position book works — first-in-first-out (FIFO) lot matching per
instrument.

Design:
- Each live fill becomes a "lot" (trade_id, side, remaining_qty, price).
- A new fill is matched against opposite-side lots for the same
  security_id, oldest first. Each match realizes PnL on that lot's
  original journal row (`TradeJournal.accumulate_realized_pnl`) and is
  marked CLOSED once fully offset.
- Any quantity left over after exhausting opposite lots opens a new lot in
  the fill's own direction (this naturally handles a position flip, e.g.
  selling 15 against a 10-lot long results in the 10-lot closing and a new
  5-lot short opening).
- This mirrors `core/paper_trading.py`'s averaging/flip logic conceptually,
  but works at the lot level (true FIFO) rather than a single blended
  average price, because live fills arrive as discrete broker events and
  we want per-lot PnL attribution in the journal.

Purely in-memory per session — if the app restarts mid-session, in-flight
lots from before the restart won't be reconstructed automatically. For a
persistent multi-day book, seed `self.lots` from the journal's open rows
at startup (`TradeJournal.fetch_open_positions`) before the first fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from database import TradeJournal


@dataclass
class Lot:
    trade_id: int
    security_id: str
    side: str          # BUY / SELL
    remaining_qty: float
    price: float


class LiveFIFOTracker:
    """
    NOT internally locked: `self.lots` is only ever mutated from the single
    background thread that `OrderUpdateManager` runs its `on_update`
    callback on (see app.py's connect-to-Dhan handler), plus one call to
    `seed_from_open_positions()` at session startup on the main thread
    before that background thread exists. If a future change calls
    `process_fill` from more than one thread concurrently, add a lock here
    the same way `TradeJournal` already does for its own table.
    """

    def __init__(self, journal: TradeJournal):
        self.journal = journal
        self.lots: Dict[str, List[Lot]] = {}

    def seed_from_open_positions(self) -> None:
        """Reconstruct in-memory lots from journal rows left OPEN by a prior session."""
        df = self.journal.fetch_open_positions(mode="Live Trading")
        self.lots.clear()
        for _, row in df.iterrows():
            book = self.lots.setdefault(str(row["security_id"]), [])
            book.append(
                Lot(
                    trade_id=int(row["id"]),
                    security_id=str(row["security_id"]),
                    side=row["side"],
                    remaining_qty=float(row["quantity"]),
                    price=float(row["entry_price"]) if row["entry_price"] else 0.0,
                )
            )

    def process_fill(self, trade_id: int, security_id: str, side: str, quantity: float, price: float) -> float:
        """
        Matches a new confirmed fill against existing opposite-side lots
        FIFO. Returns the total realized PnL produced by this single fill
        (0.0 if it only opened/extended a position with no offset).
        """
        book = self.lots.setdefault(security_id, [])
        opposite_side = "SELL" if side == "BUY" else "BUY"
        remaining_to_fill = quantity
        realized_total = 0.0

        i = 0
        while remaining_to_fill > 1e-9 and i < len(book):
            lot = book[i]
            if lot.side != opposite_side:
                i += 1
                continue

            matched_qty = min(lot.remaining_qty, remaining_to_fill)
            direction = 1 if lot.side == "BUY" else -1   # closing a long realizes (exit - entry); a short the inverse
            realized = direction * (price - lot.price) * matched_qty
            realized_total += realized

            lot.remaining_qty -= matched_qty
            remaining_to_fill -= matched_qty

            fully_closed = lot.remaining_qty <= 1e-9
            self.journal.accumulate_realized_pnl(lot.trade_id, realized, exit_price=price, close=fully_closed)

            if fully_closed:
                book.pop(i)
            else:
                i += 1

        if remaining_to_fill > 1e-9:
            # No (or insufficient) opposite exposure to net against — opens a new lot.
            book.append(
                Lot(trade_id=trade_id, security_id=security_id, side=side, remaining_qty=remaining_to_fill, price=price)
            )

        return realized_total

    def net_position(self, security_id: str) -> float:
        """Signed net quantity for an instrument (positive = net long, negative = net short)."""
        book = self.lots.get(security_id, [])
        return sum(lot.remaining_qty if lot.side == "BUY" else -lot.remaining_qty for lot in book)
