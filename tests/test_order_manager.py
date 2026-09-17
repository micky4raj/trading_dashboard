import pytest

from config import TradingMode
from core.dhan_client import DhanAPIError
from core.order_manager import OrderRequest


def test_paper_order_requires_ltp_hint(order_manager):
    order = OrderRequest(
        instrument="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
        side="BUY", quantity=10,
    )
    with pytest.raises(ValueError):
        order_manager.execute(TradingMode.PAPER, order, ltp_hint=0.0)


def test_paper_order_logs_to_journal(order_manager, tmp_journal):
    order = OrderRequest(
        instrument="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
        side="BUY", quantity=10,
    )
    result = order_manager.execute(TradingMode.PAPER, order, ltp_hint=2500.0)

    assert result["status"] == "TRADED"
    df = tmp_journal.fetch_all()
    assert len(df) == 1
    assert df.iloc[0]["mode"] == TradingMode.PAPER.value
    assert df.iloc[0]["instrument"] == "RELIANCE"


def test_live_order_without_connected_client_raises(order_manager, tmp_journal):
    order = OrderRequest(
        instrument="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
        side="BUY", quantity=10,
    )
    with pytest.raises(DhanAPIError):
        order_manager.execute(TradingMode.LIVE, order)

    # Live mode was never attempted against a broker, so nothing should be journaled.
    assert tmp_journal.fetch_all().empty


def test_get_positions_reflects_paper_engine(order_manager):
    order = OrderRequest(
        instrument="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
        side="BUY", quantity=10,
    )
    order_manager.execute(TradingMode.PAPER, order, ltp_hint=2500.0)

    positions = order_manager.get_positions(TradingMode.PAPER)
    assert len(positions) == 1
    assert positions[0]["security_id"] == "1333"


def test_get_margin_reflects_paper_cash(order_manager):
    margin = order_manager.get_margin(TradingMode.PAPER)
    assert margin["availableBalance"] == order_manager.paper_engine.starting_capital
