import time

from core.dhan_client import DhanClient, segment_to_marketfeed_code
import core.dhan_client as dhan_client_module


class FakeSDKForLTP:
    """Matches the documented `ticker_data` LTP-only market-quote method."""

    def ticker_data(self, securities):
        return {
            "data": {
                "NSE_EQ": {"1333": {"last_price": 1522.4}},
                "IDX_I": {"13": {"last_price": 24500.0}},
            },
            "status": "success",
        }


class FakeSDKForLTPFallback:
    """No ticker_data — should fall back to ohlc_data."""

    def ohlc_data(self, securities):
        return {"data": {"NSE_EQ": {"1333": {"last_price": 1522.4}}}, "status": "success"}


class FakeSDKForHistorical:
    """Matches the documented `intraday_minute_data` / `historical_daily_data` methods."""

    def intraday_minute_data(self, security_id, exchange_segment, instrument_type, from_date, to_date, interval=None):
        now = int(time.time())
        n = 5
        return {
            "status": "success",
            "data": {
                "start_Time": [now - i * 300 for i in range(n)][::-1],
                "open": [100 + i for i in range(n)],
                "high": [101 + i for i in range(n)],
                "low": [99 + i for i in range(n)],
                "close": [100.5 + i for i in range(n)],
                "volume": [1000 + i for i in range(n)],
            },
        }

    def historical_daily_data(self, security_id, exchange_segment, instrument_type, expiry_code, from_date, to_date):
        return {
            "status": "success",
            "data": {
                "start_Time": [1700000000, 1700086400, 1700172800],
                "open": [95.0, 96.0, 97.0],
                "high": [96.0, 97.0, 98.0],
                "low": [94.0, 95.0, 96.0],
                "close": [95.5, 96.5, 97.5],
                "volume": [50000, 51000, 52000],
            },
        }


class FakeSDKEmpty:
    def intraday_minute_data(self, security_id, exchange_segment, instrument_type, from_date, to_date, interval=None):
        return {"data": {}, "status": "success"}

    def historical_daily_data(self, security_id, exchange_segment, instrument_type, expiry_code, from_date, to_date):
        return {"data": {}, "status": "success"}


class FakeSDKFailureStatus:
    """Simulates the documented failure response shape (status: 'failure')."""

    def historical_daily_data(self, security_id, exchange_segment, instrument_type, expiry_code, from_date, to_date):
        return {"status": "failure", "remarks": "Expecting value: line 1 column 1 (char 0)", "data": ""}


def _connected_client(fake_sdk):
    client = DhanClient("cid", "token")
    client._dhan = fake_sdk
    client.connected = True
    return client


# ---------------------------------------------------------------------------
# get_ltp_batch
# ---------------------------------------------------------------------------

def test_get_ltp_batch_uses_ticker_data_and_flattens_segments():
    client = _connected_client(FakeSDKForLTP())
    result = client.get_ltp_batch({"NSE_EQ": ["1333"], "IDX_I": ["13"]})
    assert result == {"1333": 1522.4, "13": 24500.0}


def test_get_ltp_batch_falls_back_to_ohlc_data_when_ticker_data_missing():
    client = _connected_client(FakeSDKForLTPFallback())
    result = client.get_ltp_batch({"NSE_EQ": ["1333"]})
    assert result == {"1333": 1522.4}


def test_get_ltp_batch_empty_input_returns_empty():
    client = _connected_client(FakeSDKForLTP())
    assert client.get_ltp_batch({}) == {}


# ---------------------------------------------------------------------------
# get_historical_ohlc
# ---------------------------------------------------------------------------

def test_get_historical_ohlc_intraday_uses_intraday_minute_data():
    client = _connected_client(FakeSDKForHistorical())
    df = client.get_historical_ohlc(
        security_id="1333", exchange_segment="NSE_EQ", instrument_type="EQUITY",
        interval_minutes=5, from_date="2026-09-09", to_date="2026-09-14",
    )
    assert df is not None
    assert len(df) == 5
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_get_historical_ohlc_daily_uses_historical_daily_data():
    client = _connected_client(FakeSDKForHistorical())
    df = client.get_historical_ohlc(
        security_id="1333", exchange_segment="NSE_EQ", from_date="2026-09-01", to_date="2026-09-14"
    )
    assert df is not None
    assert len(df) == 3


def test_get_historical_ohlc_intraday_requires_from_and_to_date():
    # intraday_minute_data needs a date range; without one this must return
    # None rather than calling the SDK with missing required arguments.
    client = _connected_client(FakeSDKForHistorical())
    df = client.get_historical_ohlc(security_id="1333", exchange_segment="NSE_EQ", interval_minutes=5)
    assert df is None


def test_get_historical_ohlc_returns_none_on_empty_payload():
    client = _connected_client(FakeSDKEmpty())
    df = client.get_historical_ohlc(security_id="1333", exchange_segment="NSE_EQ", from_date="2026-09-01", to_date="2026-09-14")
    assert df is None


def test_get_historical_ohlc_returns_none_on_failure_status():
    client = _connected_client(FakeSDKFailureStatus())
    df = client.get_historical_ohlc(security_id="1333", exchange_segment="NSE_EQ", from_date="2026-09-01", to_date="2026-09-14")
    assert df is None


def test_get_historical_ohlc_returns_none_without_matching_method():
    class NoHistoricalMethod:
        pass

    client = _connected_client(NoHistoricalMethod())
    df = client.get_historical_ohlc(security_id="1333", exchange_segment="NSE_EQ", from_date="2026-09-01", to_date="2026-09-14")
    assert df is None


# ---------------------------------------------------------------------------
# segment_to_marketfeed_code
# ---------------------------------------------------------------------------

class _FakeMarketFeedConstants:
    NSE = 1
    BSE = 4
    NSE_FNO = 2


def test_segment_to_marketfeed_code_translates_confirmed_segments():
    original_available, original_cls = dhan_client_module._MARKETFEED_AVAILABLE, dhan_client_module._MarketFeed
    try:
        dhan_client_module._MARKETFEED_AVAILABLE = True
        dhan_client_module._MarketFeed = _FakeMarketFeedConstants
        assert segment_to_marketfeed_code("NSE_EQ") == 1
        assert segment_to_marketfeed_code("BSE_EQ") == 4
        assert segment_to_marketfeed_code("NSE_FNO") == 2
    finally:
        dhan_client_module._MARKETFEED_AVAILABLE = original_available
        dhan_client_module._MarketFeed = original_cls


def test_segment_to_marketfeed_code_raises_for_unmapped_attribute():
    original_available, original_cls = dhan_client_module._MARKETFEED_AVAILABLE, dhan_client_module._MarketFeed
    try:
        dhan_client_module._MARKETFEED_AVAILABLE = True
        dhan_client_module._MarketFeed = _FakeMarketFeedConstants
        try:
            segment_to_marketfeed_code("MCX_COMM")
            assert False, "expected AttributeError"
        except AttributeError:
            pass
    finally:
        dhan_client_module._MARKETFEED_AVAILABLE = original_available
        dhan_client_module._MarketFeed = original_cls


def test_segment_to_marketfeed_code_raises_when_sdk_unavailable():
    original_available = dhan_client_module._MARKETFEED_AVAILABLE
    try:
        dhan_client_module._MARKETFEED_AVAILABLE = False
        try:
            segment_to_marketfeed_code("NSE_EQ")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass
    finally:
        dhan_client_module._MARKETFEED_AVAILABLE = original_available
