"""
core/paper_trading.py
-----------------------
A self-contained simulated execution engine. Used whenever the global
Trading Mode toggle is set to "Paper Trading" — no real orders ever reach
Dhan in this path.

Fill model: a MARKET order fills instantly at the current LTP adjusted by a
configurable slippage percentage (worse for the trader in the direction of
the trade — BUY fills higher, SELL fills lower), which is a reasonable,
transparent approximation of real-world impact without needing a full
order-book simulator.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PaperPosition:
    instrument: str
    security_id: str
    side: str                 # BUY (long) / SELL (short)
    quantity: float
    avg_price: float
    product_type: str
    exchange_segment: str
    instrument_type: str = "EQUITY"
    trade_id: Optional[int] = None   # links back to the journal row

    def unrealized_pnl(self, ltp: float) -> float:
        direction = 1 if self.side == "BUY" else -1
        return direction * (ltp - self.avg_price) * self.quantity


class PaperTradingEngine:
    """
    In-memory simulated broker.

    `capital` tracks available cash; positions are keyed by security_id so
    repeated fills on the same instrument average the price naturally.
    """

    def __init__(self, starting_capital: float = 1_000_000.0, slippage_pct: float = 0.05):
        self.starting_capital = starting_capital
        self.cash = starting_capital
        self.slippage_pct = slippage_pct  # percentage, e.g. 0.05 = 0.05%
        self.positions: Dict[str, PaperPosition] = {}
        self.realized_pnl_total = 0.0

    def _apply_slippage(self, ltp: float, side: str) -> float:
        slip = ltp * (self.slippage_pct / 100.0)
        # random micro-jitter so paper fills don't look suspiciously perfect
        jitter = random.uniform(0.0, slip * 0.2)
        return ltp + slip + jitter if side == "BUY" else ltp - slip - jitter

    def place_order(
        self,
        instrument: str,
        security_id: str,
        side: str,
        quantity: float,
        ltp: float,
        product_type: str,
        exchange_segment: str,
        instrument_type: str = "EQUITY",
    ) -> dict:
        """
        Simulate an immediate market fill. Returns a dict shaped similarly
        to what a real order response would look like, so downstream code
        (order_manager, journal) can treat paper/live uniformly.
        """
        fill_price = round(self._apply_slippage(ltp, side), 2)
        order_id = f"PAPER-{uuid.uuid4().hex[:10].upper()}"

        existing = self.positions.get(security_id)
        realized = 0.0

        if existing is None:
            self.positions[security_id] = PaperPosition(
                instrument=instrument,
                security_id=security_id,
                side=side,
                quantity=quantity,
                avg_price=fill_price,
                product_type=product_type,
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
            )
        elif existing.side == side:
            # Adding to the same-direction position -> weighted average price
            new_qty = existing.quantity + quantity
            existing.avg_price = (
                (existing.avg_price * existing.quantity) + (fill_price * quantity)
            ) / new_qty
            existing.quantity = new_qty
        else:
            # Opposite-direction order -> reduces or flips the position
            direction = 1 if existing.side == "BUY" else -1
            close_qty = min(existing.quantity, quantity)
            realized = direction * (fill_price - existing.avg_price) * close_qty
            self.realized_pnl_total += realized
            self.cash += realized
            remaining_existing = existing.quantity - close_qty
            remaining_new = quantity - close_qty

            if remaining_existing > 0:
                existing.quantity = remaining_existing
            elif remaining_new > 0:
                # position flipped direction
                self.positions[security_id] = PaperPosition(
                    instrument=instrument,
                    security_id=security_id,
                    side=side,
                    quantity=remaining_new,
                    avg_price=fill_price,
                    product_type=product_type,
                    exchange_segment=exchange_segment,
                    instrument_type=instrument_type,
                )
            else:
                del self.positions[security_id]

        return {
            "order_id": order_id,
            "status": "TRADED",
            "fill_price": fill_price,
            "quantity": quantity,
            "side": side,
            "realized_pnl": realized,
        }

    def mark_to_market(self, ltp_lookup: Dict[str, float]) -> float:
        """Total unrealized MTM across all open paper positions."""
        total = 0.0
        for sec_id, pos in self.positions.items():
            ltp = ltp_lookup.get(sec_id)
            if ltp is not None:
                total += pos.unrealized_pnl(ltp)
        return total

    def reset(self) -> None:
        self.cash = self.starting_capital
        self.positions.clear()
        self.realized_pnl_total = 0.0
