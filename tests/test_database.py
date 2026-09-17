from database import TradeRecord


def test_log_and_fetch_trade(tmp_journal):
    trade_id = tmp_journal.log_trade(
        TradeRecord(instrument="RELIANCE", side="BUY", quantity=10, mode="Paper Trading", entry_price=2500)
    )
    assert trade_id == 1

    df = tmp_journal.fetch_all()
    assert len(df) == 1
    assert df.iloc[0]["instrument"] == "RELIANCE"
    assert df.iloc[0]["status"] == "OPEN"


def test_close_trade_updates_pnl(tmp_journal):
    trade_id = tmp_journal.log_trade(
        TradeRecord(instrument="TCS", side="BUY", quantity=5, mode="Paper Trading", entry_price=3500)
    )
    tmp_journal.close_trade(trade_id, exit_price=3600, pnl=500.0)

    df = tmp_journal.fetch_all()
    row = df[df["id"] == trade_id].iloc[0]
    assert row["status"] == "CLOSED"
    assert row["pnl"] == 500.0
    assert row["exit_price"] == 3600


def test_update_status_by_order_id_updates_matching_row(tmp_journal):
    tmp_journal.log_trade(
        TradeRecord(
            instrument="TCS", side="BUY", quantity=5, mode="Live Trading",
            order_id="ORD123", status="PENDING",
        )
    )
    found = tmp_journal.update_status_by_order_id("ORD123", "OPEN", traded_price=3550.5)
    assert found is True

    df = tmp_journal.fetch_all()
    row = df[df["order_id"] == "ORD123"].iloc[0]
    assert row["status"] == "OPEN"
    assert row["entry_price"] == 3550.5


def test_update_status_by_order_id_preserves_existing_entry_price(tmp_journal):
    tmp_journal.log_trade(
        TradeRecord(
            instrument="TCS", side="BUY", quantity=5, mode="Live Trading",
            order_id="ORD456", status="PENDING", entry_price=3500.0,
        )
    )
    tmp_journal.update_status_by_order_id("ORD456", "OPEN", traded_price=9999.0)

    df = tmp_journal.fetch_all()
    row = df[df["order_id"] == "ORD456"].iloc[0]
    # Existing non-zero entry_price should NOT be clobbered by a later traded_price.
    assert row["entry_price"] == 3500.0


def test_update_status_by_order_id_returns_false_for_unknown_order(tmp_journal):
    assert tmp_journal.update_status_by_order_id("NOPE", "CANCELLED") is False


def test_fetch_open_positions_filters_by_mode(tmp_journal):
    tmp_journal.log_trade(TradeRecord(instrument="A", side="BUY", quantity=1, mode="Paper Trading"))
    tmp_journal.log_trade(TradeRecord(instrument="B", side="BUY", quantity=1, mode="Live Trading"))

    paper_open = tmp_journal.fetch_open_positions(mode="Paper Trading")
    assert len(paper_open) == 1
    assert paper_open.iloc[0]["instrument"] == "A"


def test_export_csv_roundtrip(tmp_journal, tmp_path):
    tmp_journal.log_trade(TradeRecord(instrument="X", side="SELL", quantity=2, mode="Paper Trading"))
    out_path = tmp_journal.export_csv(str(tmp_path / "export.csv"))

    import pandas as pd
    df = pd.read_csv(out_path)
    assert len(df) == 1
    assert df.iloc[0]["instrument"] == "X"
