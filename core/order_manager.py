"""
core/order_manager.py
------------------------
The single choke-point through which ALL orders flow, regardless of
instrument type (Equity Intraday/Delivery, Futures, Options). It decides,
based on the global TradingMode, whether to hit the real Dhan API or the
in-memory paper engine — and either way, logs the resulting fill into the
TradeJournal so the Trade Journal / Performance / Super Intelligence tabs
all see a single consistent history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import TradingMode
from database import TradeJournal, TradeRecord
from core.dhan_client import DhanClient, DhanAPIError
from core.paper_trading import PaperTradingEngine


@dataclass
class OrderRequest:
    instrument: str
    security_id: str
    exchange_segment: str
    side: str                 # BUY / SELL
    quantity: int
    order_type: str = "MARKET"
    product_type: str = "INTRADAY"
    instrument_type: str = "EQUITY"   # EQUITY / FUTURE / OPTION
    price: float = 0.0
    trigger_price: float = 0.0
    strategy_tag: str = "manual"


class OrderManager:
    def __init__(
        self,
        journal: TradeJournal,
        paper_engine: PaperTradingEngine,
        dhan_client: Optional[DhanClient] = None,
    ):
        self.journal = journal
        self.paper_engine = paper_engine
        self.dhan_client = dhan_client

    # ------------------------------------------------------------------
    def execute(self, mode: TradingMode, order: OrderRequest, ltp_hint: float = 0.0) -> Dict[str, Any]:
        if mode == TradingMode.PAPER:
            return self._execute_paper(order, ltp_hint)
        return self._execute_live(order)

    # ------------------------------------------------------------------
    def _execute_paper(self, order: OrderRequest, ltp_hint: float) -> Dict[str, Any]:
        if ltp_hint <= 0:
            raise ValueError("A live/reference LTP is required to simulate a paper fill.")

        result = self.paper_engine.place_order(
            instrument=order.instrument,
            security_id=order.security_id,
            side=order.side,
            quantity=order.quantity,
            ltp=ltp_hint,
            product_type=order.product_type,
            exchange_segment=order.exchange_segment,
            instrument_type=order.instrument_type,
        )

        record = TradeRecord(
            instrument=order.instrument,
            security_id=order.security_id,
            exchange_segment=order.exchange_segment,
            side=order.side,
            quantity=order.quantity,
            mode=TradingMode.PAPER.value,
            product_type=order.product_type,
            instrument_type=order.instrument_type,
            entry_price=result["fill_price"],
            order_type=order.order_type,
            status="OPEN" if order.security_id in self.paper_engine.positions else "CLOSED",
            pnl=result["realized_pnl"] or None,
            strategy_tag=order.strategy_tag,
            order_id=result["order_id"],
        )
        trade_id = self.journal.log_trade(record)
        result["trade_id"] = trade_id
        return result

    # ------------------------------------------------------------------
    def _execute_live(self, order: OrderRequest) -> Dict[str, Any]:
        if self.dhan_client is None or not self.dhan_client.connected:
            raise DhanAPIError(
                "Live Trading is selected but the Dhan client is not connected. "
                "Enter valid credentials in the sidebar first."
            )
        try:
            response = self.dhan_client.place_order(
                security_id=order.security_id,
                exchange_segment=order.exchange_segment,
                transaction_type=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                product_type=order.product_type,
                price=order.price,
                trigger_price=order.trigger_price,
            )
        except DhanAPIError:
            # Log the rejection so a failed live attempt still shows in the journal.
            self.journal.log_trade(
                TradeRecord(
                    instrument=order.instrument,
                    security_id=order.security_id,
                    exchange_segment=order.exchange_segment,
                    side=order.side,
                    quantity=order.quantity,
                    mode=TradingMode.LIVE.value,
                    product_type=order.product_type,
                    instrument_type=order.instrument_type,
                    order_type=order.order_type,
                    status="REJECTED",
                    strategy_tag=order.strategy_tag,
                )
            )
            raise

        order_id = str(response.get("data", {}).get("orderId", response.get("orderId", "")))
        record = TradeRecord(
            instrument=order.instrument,
            security_id=order.security_id,
            exchange_segment=order.exchange_segment,
            side=order.side,
            quantity=order.quantity,
            mode=TradingMode.LIVE.value,
            product_type=order.product_type,
            instrument_type=order.instrument_type,
            entry_price=order.price or None,
            order_type=order.order_type,
            status="OPEN",
            strategy_tag=order.strategy_tag,
            order_id=order_id,
        )
        trade_id = self.journal.log_trade(record)
        response["trade_id"] = trade_id
        return response

    # ------------------------------------------------------------------
    def get_positions(self, mode: TradingMode) -> List[Dict[str, Any]]:
        if mode == TradingMode.PAPER:
            return [
                {
                    "instrument": p.instrument,
                    "security_id": p.security_id,
                    "side": p.side,
                    "quantity": p.quantity,
                    "avg_price": round(p.avg_price, 2),
                    "product_type": p.product_type,
                }
                for p in self.paper_engine.positions.values()
            ]
        if self.dhan_client is None or not self.dhan_client.connected:
            return []
        return self.dhan_client.get_positions()

    def get_margin(self, mode: TradingMode) -> Dict[str, Any]:
        if mode == TradingMode.PAPER:
            return {
                "availableBalance": round(self.paper_engine.cash, 2),
                "sodLimit": round(self.paper_engine.starting_capital, 2),
                "utilizedAmount": round(self.paper_engine.starting_capital - self.paper_engine.cash, 2),
            }
        if self.dhan_client is None or not self.dhan_client.connected:
            return {}
        return self.dhan_client.get_fund_limits()

    def get_order_book(self, mode: TradingMode) -> List[Dict[str, Any]]:
        if mode == TradingMode.PAPER:
            df = self.journal.fetch_all()
            df = df[df["mode"] == TradingMode.PAPER.value]
            return df.to_dict("records")
        if self.dhan_client is None or not self.dhan_client.connected:
            return []
        return self.dhan_client.get_order_list()
