import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import TradeJournal
from core.paper_trading import PaperTradingEngine
from core.order_manager import OrderManager


@pytest.fixture
def tmp_journal():
    db_path = os.path.join(tempfile.mkdtemp(), "test_trades.db")
    return TradeJournal(db_path)


@pytest.fixture
def paper_engine():
    return PaperTradingEngine(starting_capital=1_000_000.0, slippage_pct=0.05)


@pytest.fixture
def order_manager(tmp_journal, paper_engine):
    return OrderManager(journal=tmp_journal, paper_engine=paper_engine, dhan_client=None)
