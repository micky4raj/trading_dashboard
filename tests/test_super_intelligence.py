import pandas as pd

from analytics import super_intelligence as si


def test_generate_mock_trades_shape():
    df = si.generate_mock_trades(n=20)
    assert len(df) == 20
    assert {"instrument", "pnl", "status", "quantity"}.issubset(df.columns)


def test_generate_mock_ohlc_shape():
    df = si.generate_mock_ohlc(periods=60)
    assert len(df) == 60
    assert {"timestamp", "open", "high", "low", "close", "volume"}.issubset(df.columns)
    assert (df["high"] >= df["low"]).all()


def test_trade_insights_handles_empty_df():
    df = pd.DataFrame(columns=["status", "pnl", "instrument", "timestamp"])
    insights = si.trade_insights(df)
    assert isinstance(insights, list)
    assert len(insights) >= 1


def test_detect_trade_anomalies_flags_outlier():
    # 20 normal trades clustered near 0, one wildly out of distribution
    rows = []
    for i in range(20):
        rows.append({"id": i + 1, "instrument": "X", "status": "CLOSED", "pnl": 10.0, "quantity": 5})
    rows.append({"id": 21, "instrument": "X", "status": "CLOSED", "pnl": 100000.0, "quantity": 5})
    df = pd.DataFrame(rows)

    anomalies = si.detect_trade_anomalies(df)
    assert any(a.trade_id == 21 for a in anomalies)


def test_detect_trade_anomalies_short_history_returns_empty():
    df = pd.DataFrame(
        [{"id": 1, "instrument": "X", "status": "CLOSED", "pnl": 10.0, "quantity": 5}]
    )
    assert si.detect_trade_anomalies(df) == []


def test_analyze_technical_patterns_returns_expected_keys():
    ohlc = si.generate_mock_ohlc(periods=120)
    result = si.analyze_technical_patterns(ohlc)
    for key in ("verdict", "rsi14", "sma20", "sma50", "macd_cross"):
        assert key in result
    assert result["verdict"] in {"BULLISH", "BEARISH", "NEUTRAL"}


def test_risk_recommendations_flags_high_drawdown():
    class FakeSummary:
        max_drawdown_pct = -20.0
        win_rate = 50.0
        profit_factor = 1.5
        sharpe_ratio = 1.0
        closed_trades = 20

    recs = si.risk_recommendations(FakeSummary(), open_positions_count=2, capital=1_000_000)
    assert any("halving" in r.lower() or "drawdown" in r.lower() for r in recs)


def test_risk_recommendations_no_signals_when_healthy():
    class FakeSummary:
        max_drawdown_pct = -2.0
        win_rate = 60.0
        profit_factor = 2.0
        sharpe_ratio = 1.5
        closed_trades = 20

    recs = si.risk_recommendations(FakeSummary(), open_positions_count=1, capital=1_000_000)
    assert any("no elevated risk" in r.lower() for r in recs)
