import pandas as pd
import pytest

from analytics import performance as perf


def make_trades(pnls):
    rows = []
    ts = pd.Timestamp("2026-01-01")
    for i, pnl in enumerate(pnls):
        rows.append(
            {
                "id": i + 1,
                "timestamp": ts + pd.Timedelta(days=i),
                "instrument": "TEST",
                "status": "CLOSED",
                "pnl": pnl,
                "quantity": 10,
            }
        )
    return pd.DataFrame(rows)


def test_win_rate_all_wins():
    df = make_trades([100, 200, 300])
    assert perf.compute_win_rate(df) == 100.0


def test_win_rate_mixed():
    df = make_trades([100, -50, 100, -50])
    assert perf.compute_win_rate(df) == 50.0


def test_win_rate_empty_df_is_zero():
    df = pd.DataFrame(columns=["status", "pnl"])
    assert perf.compute_win_rate(df) == 0.0


def test_profit_factor_computed_correctly():
    # gross profit = 300, gross loss = 100 -> profit factor = 3.0
    df = make_trades([100, 200, -100])
    assert perf.compute_profit_factor(df) == pytest.approx(3.0)


def test_profit_factor_no_losses_returns_gross_profit():
    df = make_trades([100, 200])
    assert perf.compute_profit_factor(df) == pytest.approx(300.0)


def test_max_drawdown_on_monotonic_gains_is_zero():
    df = make_trades([100, 100, 100])
    equity_curve = perf.build_equity_curve(df, starting_capital=0)
    dd_pct, dd_val = perf.compute_max_drawdown(equity_curve)
    assert dd_pct == 0.0
    assert dd_val == 0.0


def test_max_drawdown_detects_a_drop():
    # equity path: 100 -> 300 -> 100  => drawdown of -200 from the peak of 300
    df = make_trades([100, 200, -200])
    equity_curve = perf.build_equity_curve(df, starting_capital=0)
    dd_pct, dd_val = perf.compute_max_drawdown(equity_curve)
    assert dd_val == pytest.approx(-200.0)
    assert dd_pct == pytest.approx(-200 / 300 * 100)


def test_summarize_returns_consistent_fields():
    df = make_trades([100, -50, 200, -25, 150])
    summary = perf.summarize(df, starting_capital=100000, risk_free_rate_annual=0.065)
    assert summary.total_trades == 5
    assert summary.closed_trades == 5
    assert summary.total_pnl == pytest.approx(375.0)
    assert 0 <= summary.win_rate <= 100
