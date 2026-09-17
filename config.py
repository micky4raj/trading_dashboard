"""
config.py
---------
Central configuration for the dashboard.

SECURITY NOTE
-------------
Credentials are NEVER hardcoded. They are read from environment variables
(loaded from a local, git-ignored `.env` file via python-dotenv), or from
Streamlit's session_state if the user pastes them into the sidebar for a
session-only login. Nothing is ever written to disk in plaintext by this
app. See README.md for setup instructions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv

# Load .env if present (no-op if it doesn't exist). Never commit a real .env.
load_dotenv()


class TradingMode(str, Enum):
    """Global execution mode toggle used across the whole app."""

    PAPER = "Paper Trading"
    LIVE = "Live Trading"


class ProductType(str, Enum):
    INTRADAY = "INTRADAY"
    DELIVERY = "CNC"
    MARGIN = "MARGIN"
    MTF = "MTF"
    CO = "CO"
    BO = "BO"


class SegmentType(str, Enum):
    EQUITY = "NSE_EQ"
    FNO = "NSE_FNO"
    CURRENCY = "NSE_CURRENCY"
    BSE_EQUITY = "BSE_EQ"
    MCX = "MCX_COMM"


@dataclass
class DhanCredentials:
    """
    Holds the two secrets DhanHQ requires. Populated either from environment
    variables (recommended for anything long-running / server-hosted) or
    from an in-memory Streamlit widget (session only, never persisted).
    """

    client_id: str = field(default_factory=lambda: os.getenv("DHAN_CLIENT_ID", ""))
    access_token: str = field(default_factory=lambda: os.getenv("DHAN_ACCESS_TOKEN", ""))

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.access_token)

    def masked(self) -> str:
        """Safe-to-display representation for the UI (never show the raw token)."""
        if not self.access_token:
            return "not set"
        return f"{self.access_token[:4]}...{self.access_token[-4:]}"


@dataclass
class AppSettings:
    """Non-secret, tunable application settings."""

    db_path: str = os.getenv("TRADING_DB_PATH", "data/trading_journal.db")
    default_slippage_pct: float = float(os.getenv("DEFAULT_SLIPPAGE_PCT", "0.05"))
    paper_starting_capital: float = float(os.getenv("PAPER_STARTING_CAPITAL", "1000000"))
    market_feed_reconnect_base_delay: float = 2.0
    market_feed_reconnect_max_delay: float = 60.0
    risk_free_rate_annual: float = float(os.getenv("RISK_FREE_RATE_ANNUAL", "0.065"))
    trading_days_per_year: int = 252
    # App-level access control (separate from Dhan credentials). If unset, the
    # app has NO login gate — fine for `localhost` only. Set this before
    # deploying anywhere network-reachable (Docker, cloud host, etc.), since
    # Dhan credentials alone don't stop a stranger from using YOUR session
    # once you've connected.
    app_password: str = os.getenv("APP_PASSWORD", "")


SETTINGS = AppSettings()
