"""
analytics/performance.py
--------------------------
Vectorized (NumPy/Pandas) performance metrics computed from the trade
journal. Every function accepts the raw trades DataFrame from
`TradeJournal.fetch_all()` and returns plain floats / a DataFrame, so the
UI layer stays free of calculation logic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PerformanceSummary:
    total_trades: int
    closed_trades: int
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    max_drawdown_value: float
    total_pnl: float
    avg_win: float
    avg_loss: float


def _closed(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["status"] == "CLOSED") & df["pnl"].notna()].copy()


def build_equity_curve(df: pd.DataFrame, starting_capital: float = 0.0) -> pd.DataFrame:
    """Cumulative realized PnL over time -> equity curve for drawdown/Sharpe."""
    closed = _closed(df)
    if closed.empty:
        return pd.DataFrame(columns=["timestamp", "pnl", "equity"])
    closed = closed.sort_values("timestamp")
    closed["equity"] = starting_capital + closed["pnl"].cumsum()
    return closed[["timestamp", "pnl", "equity"]].reset_index(drop=True)


def compute_win_rate(df: pd.DataFrame) -> float:
    closed = _closed(df)
    if closed.empty:
        return 0.0
    wins = (closed["pnl"] > 0).sum()
    return float(wins / len(closed) * 100.0)


def compute_profit_factor(df: pd.DataFrame) -> float:
    closed = _closed(df)
    if closed.empty:
        return 0.0
    gross_profit = closed.loc[closed["pnl"] > 0, "pnl"].sum()
    gross_loss = -closed.loc[closed["pnl"] < 0, "pnl"].sum()
    if gross_loss == 0:
        return float(gross_profit) if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def compute_sharpe_ratio(
    df: pd.DataFrame,
    risk_free_rate_annual: float = 0.065,
    periods_per_year: int = 252,
) -> float:
    """
    Sharpe computed on the daily-aggregated realized-PnL return series.
    Vectorized entirely via pandas groupby + numpy — no per-row loops.
    """
    closed = _closed(df)
    if closed.empty or len(closed) < 2:
        return 0.0

    daily = closed.set_index("timestamp")["pnl"].resample("D").sum().fillna(0.0)
    if daily.std(ddof=0) == 0 or len(daily) < 2:
        return 0.0

    daily_rf = risk_free_rate_annual / periods_per_year
    excess_returns = daily - daily_rf
    sharpe = (excess_returns.mean() / excess_returns.std(ddof=0)) * np.sqrt(periods_per_year)
    return float(sharpe)


def compute_max_drawdown(equity_curve: pd.DataFrame) -> tuple[float, float]:
    """Returns (max_drawdown_pct, max_drawdown_value) using a vectorized running-max."""
    if equity_curve.empty:
        return 0.0, 0.0
    equity = equity_curve["equity"].to_numpy()
    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    drawdown_pct = np.divide(
        drawdown, running_max, out=np.zeros_like(drawdown, dtype=float), where=running_max != 0
    )
    max_dd_value = float(drawdown.min())
    max_dd_pct = float(drawdown_pct.min() * 100.0)
    return max_dd_pct, max_dd_value


def summarize(df: pd.DataFrame, starting_capital: float, risk_free_rate_annual: float) -> PerformanceSummary:
    closed = _closed(df)
    equity_curve = build_equity_curve(df, starting_capital)
    dd_pct, dd_val = compute_max_drawdown(equity_curve)

    avg_win = float(closed.loc[closed["pnl"] > 0, "pnl"].mean()) if (closed["pnl"] > 0).any() else 0.0
    avg_loss = float(closed.loc[closed["pnl"] < 0, "pnl"].mean()) if (closed["pnl"] < 0).any() else 0.0

    return PerformanceSummary(
        total_trades=int(len(df)),
        closed_trades=int(len(closed)),
        win_rate=compute_win_rate(df),
        profit_factor=compute_profit_factor(df),
        sharpe_ratio=compute_sharpe_ratio(df, risk_free_rate_annual),
        max_drawdown_pct=dd_pct,
        max_drawdown_value=dd_val,
        total_pnl=float(closed["pnl"].sum()) if not closed.empty else 0.0,
        avg_win=avg_win,
        avg_loss=avg_loss,
    )
