import pytest

from core.live_fifo_tracker import LiveFIFOTracker
from database import TradeRecord


def _open_lot(journal, security_id, side, quantity, price, mode="Live Trading"):
    return journal.log_trade(
        TradeRecord(
            instrument="TEST", security_id=security_id, side=side, quantity=quantity,
            mode=mode, entry_price=price, status="OPEN", order_id=f"ORD-{security_id}-{side}-{price}",
        )
    )


def test_opening_fill_creates_a_new_lot_no_realized_pnl(tmp_journal):
    tracker = LiveFIFOTracker(tmp_journal)
    trade_id = _open_lot(tmp_journal, "1333", "BUY", 10, 100.0)

    realized = tracker.process_fill(trade_id=trade_id, security_id="1333", side="BUY", quantity=10, price=100.0)

    assert realized == 0.0
    assert tracker.net_position("1333") == 10


def test_full_offsetting_fill_closes_lot_and_realizes_pnl(tmp_journal):
    tracker = LiveFIFOTracker(tmp_journal)
    buy_id = _open_lot(tmp_journal, "1333", "BUY", 10, 100.0)
    tracker.process_fill(trade_id=buy_id, security_id="1333", side="BUY", quantity=10, price=100.0)

    sell_id = _open_lot(tmp_journal, "1333", "SELL", 10, 110.0)
    realized = tracker.process_fill(trade_id=sell_id, security_id="1333", side="SELL", quantity=10, price=110.0)

    assert realized == pytest.approx(100.0)  # (110-100)*10
    assert tracker.net_position("1333") == 0

    df = tmp_journal.fetch_all()
    buy_row = df[df["id"] == buy_id].iloc[0]
    assert buy_row["status"] == "CLOSED"
    assert buy_row["pnl"] == pytest.approx(100.0)
    assert buy_row["exit_price"] == 110.0


def test_partial_offset_leaves_remaining_lot_open(tmp_journal):
    tracker = LiveFIFOTracker(tmp_journal)
    buy_id = _open_lot(tmp_journal, "1333", "BUY", 10, 100.0)
    tracker.process_fill(trade_id=buy_id, security_id="1333", side="BUY", quantity=10, price=100.0)

    sell_id = _open_lot(tmp_journal, "1333", "SELL", 4, 110.0)
    realized = tracker.process_fill(trade_id=sell_id, security_id="1333", side="SELL", quantity=4, price=110.0)

    assert realized == pytest.approx(40.0)  # (110-100)*4
    assert tracker.net_position("1333") == 6  # 10 - 4 remaining long

    df = tmp_journal.fetch_all()
    buy_row = df[df["id"] == buy_id].iloc[0]
    assert buy_row["status"] == "OPEN"          # not fully closed yet
    assert buy_row["pnl"] == pytest.approx(40.0)  # partial realized PnL recorded


def test_oversized_opposite_fill_closes_lot_and_flips_position(tmp_journal):
    tracker = LiveFIFOTracker(tmp_journal)
    buy_id = _open_lot(tmp_journal, "1333", "BUY", 10, 100.0)
    tracker.process_fill(trade_id=buy_id, security_id="1333", side="BUY", quantity=10, price=100.0)

    sell_id = _open_lot(tmp_journal, "1333", "SELL", 15, 110.0)
    realized = tracker.process_fill(trade_id=sell_id, security_id="1333", side="SELL", quantity=15, price=110.0)

    assert realized == pytest.approx(100.0)     # only 10 units offset against the long
    assert tracker.net_position("1333") == -5   # flipped to a 5-unit net short

    df = tmp_journal.fetch_all()
    buy_row = df[df["id"] == buy_id].iloc[0]
    assert buy_row["status"] == "CLOSED"


def test_fifo_order_closes_oldest_lot_first(tmp_journal):
    tracker = LiveFIFOTracker(tmp_journal)
    first_id = _open_lot(tmp_journal, "1333", "BUY", 5, 100.0)
    tracker.process_fill(trade_id=first_id, security_id="1333", side="BUY", quantity=5, price=100.0)

    second_id = _open_lot(tmp_journal, "1333", "BUY", 5, 120.0)
    tracker.process_fill(trade_id=second_id, security_id="1333", side="BUY", quantity=5, price=120.0)

    sell_id = _open_lot(tmp_journal, "1333", "SELL", 5, 130.0)
    tracker.process_fill(trade_id=sell_id, security_id="1333", side="SELL", quantity=5, price=130.0)

    df = tmp_journal.fetch_all()
    first_row = df[df["id"] == first_id].iloc[0]
    second_row = df[df["id"] == second_id].iloc[0]
    assert first_row["status"] == "CLOSED"      # oldest lot closed first (FIFO)
    assert second_row["status"] == "OPEN"       # newer lot untouched


def test_seed_from_open_positions_rebuilds_lots(tmp_journal):
    _open_lot(tmp_journal, "1333", "BUY", 10, 100.0)  # left OPEN in the journal, e.g. after a restart

    tracker = LiveFIFOTracker(tmp_journal)
    tracker.seed_from_open_positions()

    assert tracker.net_position("1333") == 10
