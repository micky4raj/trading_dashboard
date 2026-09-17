import pytest


def test_new_long_position_opens_at_slipped_price(paper_engine):
    result = paper_engine.place_order(
        instrument="RELIANCE", security_id="1333", side="BUY", quantity=10,
        ltp=2500.0, product_type="INTRADAY", exchange_segment="NSE_EQ",
    )
    assert result["status"] == "TRADED"
    assert result["fill_price"] > 2500.0          # BUY fills worse (higher) than LTP
    assert "1333" in paper_engine.positions
    assert paper_engine.positions["1333"].quantity == 10


def test_sell_fills_below_ltp(paper_engine):
    result = paper_engine.place_order(
        instrument="RELIANCE", security_id="1333", side="SELL", quantity=10,
        ltp=2500.0, product_type="INTRADAY", exchange_segment="NSE_EQ",
    )
    assert result["fill_price"] < 2500.0


def test_closing_a_position_realizes_pnl_and_updates_cash(paper_engine):
    paper_engine.slippage_pct = 0.0  # remove slippage/jitter noise for a deterministic assertion
    paper_engine.place_order(
        instrument="RELIANCE", security_id="1333", side="BUY", quantity=10,
        ltp=2500.0, product_type="INTRADAY", exchange_segment="NSE_EQ",
    )
    starting_cash = paper_engine.cash
    result = paper_engine.place_order(
        instrument="RELIANCE", security_id="1333", side="SELL", quantity=10,
        ltp=2600.0, product_type="INTRADAY", exchange_segment="NSE_EQ",
    )
    assert result["realized_pnl"] == pytest.approx(1000.0, abs=0.5)  # (2600-2500)*10
    assert paper_engine.cash > starting_cash
    assert "1333" not in paper_engine.positions  # fully closed


def test_partial_close_leaves_remaining_quantity(paper_engine):
    paper_engine.slippage_pct = 0.0
    paper_engine.place_order(
        instrument="TCS", security_id="11536", side="BUY", quantity=10,
        ltp=3500.0, product_type="CNC", exchange_segment="NSE_EQ",
    )
    paper_engine.place_order(
        instrument="TCS", security_id="11536", side="SELL", quantity=4,
        ltp=3600.0, product_type="CNC", exchange_segment="NSE_EQ",
    )
    assert paper_engine.positions["11536"].quantity == 6


def test_adding_to_same_direction_averages_price(paper_engine):
    paper_engine.slippage_pct = 0.0
    paper_engine.place_order(
        instrument="TCS", security_id="11536", side="BUY", quantity=10,
        ltp=100.0, product_type="CNC", exchange_segment="NSE_EQ",
    )
    paper_engine.place_order(
        instrument="TCS", security_id="11536", side="BUY", quantity=10,
        ltp=200.0, product_type="CNC", exchange_segment="NSE_EQ",
    )
    pos = paper_engine.positions["11536"]
    assert pos.quantity == 20
    assert pos.avg_price == pytest.approx(150.0)


def test_mark_to_market_sums_unrealized_pnl(paper_engine):
    paper_engine.slippage_pct = 0.0
    paper_engine.place_order(
        instrument="TCS", security_id="11536", side="BUY", quantity=10,
        ltp=100.0, product_type="CNC", exchange_segment="NSE_EQ",
    )
    mtm = paper_engine.mark_to_market({"11536": 110.0})
    assert mtm == pytest.approx(100.0)  # (110-100)*10


def test_reset_restores_starting_state(paper_engine):
    paper_engine.place_order(
        instrument="TCS", security_id="11536", side="BUY", quantity=10,
        ltp=100.0, product_type="CNC", exchange_segment="NSE_EQ",
    )
    paper_engine.reset()
    assert paper_engine.cash == paper_engine.starting_capital
    assert paper_engine.positions == {}
    assert paper_engine.realized_pnl_total == 0.0
