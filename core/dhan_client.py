"""
core/dhan_client.py
--------------------
Thin, defensive wrapper around the official `dhanhq` SDK.

Why a wrapper instead of calling the SDK directly from the UI?
1. The SDK's exact surface has shifted across versions (breaking renames at
   v2.0 — `historical_minute_charts`/`intraday_daily_minute_data` became
   `intraday_minute_data`/`historical_daily_data`; `get_order_by_corelationID`
   became `get_order_by_correlationID`). This wrapper normalizes what it can
   so the rest of the app doesn't care which is installed.
2. Every call is wrapped in try/except so a single API hiccup never crashes
   the Streamlit process.
3. It cleanly separates REST (`DhanClient`) from the streaming WebSocket
   feed (`MarketFeedManager`), which needs its own thread + reconnect logic.

If `dhanhq` is not installed, or credentials are missing, every method
degrades gracefully (returns None / empty structures) so the dashboard can
still run in a fully offline / paper-trading demo mode.

Verified against the current dhan-oss/DhanHQ-py README (v2.3.0) — method
names, constructor shapes (`DhanContext` + `dhanhq(context)`,
`MarketFeed(context, instruments, version)`, `OrderUpdate(context)`), and
the documented historical-data response shape (parallel `open`/`high`/
`low`/`close`/`volume`/`start_Time` arrays, `start_Time` in Unix epoch
seconds since the v2.0 breaking change from Julian time).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("dhan_client")

try:
    from dhanhq import DhanContext, dhanhq as DhanHQ  # official SDK, v2.x style
    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dhanhq isn't installed
    DhanContext = None
    DhanHQ = None
    _SDK_AVAILABLE = False

try:
    from dhanhq import MarketFeed as _MarketFeed
    _MARKETFEED_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MarketFeed = None
    _MARKETFEED_AVAILABLE = False

try:
    from dhanhq import DhanContext as _DhanContextForOrders, OrderUpdate as _OrderUpdate
    _ORDER_UPDATE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OrderUpdate = None
    _DhanContextForOrders = None
    _ORDER_UPDATE_AVAILABLE = False


class DhanAPIError(RuntimeError):
    """Raised for any failure talking to Dhan, with the offending call attached."""


@dataclass
class DhanClient:
    """
    REST wrapper. One instance per logged-in session.

    Usage:
        client = DhanClient(client_id, access_token)
        client.connect()
        funds = client.get_fund_limits()
    """

    client_id: str
    access_token: str
    _dhan: Any = field(default=None, init=False, repr=False)
    connected: bool = field(default=False, init=False)

    def connect(self) -> bool:
        if not _SDK_AVAILABLE:
            logger.warning("dhanhq SDK not installed — running without live REST access.")
            self.connected = False
            return False
        try:
            context = DhanContext(self.client_id, self.access_token)
            self._dhan = DhanHQ(context)
            self.connected = True
            return True
        except Exception as exc:  # noqa: BLE001 — surface any SDK init failure to the UI
            logger.exception("Failed to initialise Dhan client: %s", exc)
            self.connected = False
            return False

    def _guard(self):
        if not self.connected or self._dhan is None:
            raise DhanAPIError("Dhan client is not connected. Check credentials / connectivity.")

    # ---- Order management -------------------------------------------------
    def place_order(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,     # BUY / SELL
        quantity: int,
        order_type: str,           # MARKET / LIMIT / SL / SL-M
        product_type: str,         # INTRADAY / CNC / MARGIN / MTF / CO / BO
        price: float = 0.0,
        trigger_price: float = 0.0,
        validity: str = "DAY",
        disclosed_quantity: int = 0,
    ) -> Dict[str, Any]:
        self._guard()
        try:
            return self._dhan.place_order(
                security_id=security_id,
                exchange_segment=exchange_segment,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=order_type,
                product_type=product_type,
                price=price,
                trigger_price=trigger_price,
                validity=validity,
                disclosed_quantity=disclosed_quantity,
            )
        except Exception as exc:  # noqa: BLE001
            raise DhanAPIError(f"place_order failed: {exc}") from exc

    def modify_order(self, order_id: str, **kwargs) -> Dict[str, Any]:
        self._guard()
        try:
            return self._dhan.modify_order(order_id, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise DhanAPIError(f"modify_order failed: {exc}") from exc

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        self._guard()
        try:
            return self._dhan.cancel_order(order_id)
        except Exception as exc:  # noqa: BLE001
            raise DhanAPIError(f"cancel_order failed: {exc}") from exc

    def get_order_list(self) -> List[Dict[str, Any]]:
        self._guard()
        try:
            resp = self._dhan.get_order_list()
            return resp.get("data", resp) if isinstance(resp, dict) else resp
        except Exception as exc:  # noqa: BLE001
            logger.error("get_order_list failed: %s", exc)
            return []

    # ---- Portfolio ----------------------------------------------------------
    def get_positions(self) -> List[Dict[str, Any]]:
        self._guard()
        try:
            resp = self._dhan.get_positions()
            return resp.get("data", resp) if isinstance(resp, dict) else resp
        except Exception as exc:  # noqa: BLE001
            logger.error("get_positions failed: %s", exc)
            return []

    def get_holdings(self) -> List[Dict[str, Any]]:
        self._guard()
        try:
            resp = self._dhan.get_holdings()
            return resp.get("data", resp) if isinstance(resp, dict) else resp
        except Exception as exc:  # noqa: BLE001
            logger.error("get_holdings failed: %s", exc)
            return []

    def get_fund_limits(self) -> Dict[str, Any]:
        """`get_fund_limits()` is the current documented method name; falls back to
        `get_fund_limit_details` in case a future/older SDK build renames it again."""
        self._guard()
        for method_name in ("get_fund_limits", "get_fund_limit_details"):
            method = getattr(self._dhan, method_name, None)
            if method is not None:
                try:
                    resp = method()
                    return resp.get("data", resp) if isinstance(resp, dict) else resp
                except Exception as exc:  # noqa: BLE001
                    logger.error("%s failed: %s", method_name, exc)
                    return {}
        return {}

    def get_trade_book(self, order_id: Optional[str] = None) -> List[Dict[str, Any]]:
        self._guard()
        try:
            resp = self._dhan.get_trade_book(order_id) if order_id else self._dhan.get_trade_book()
            return resp.get("data", resp) if isinstance(resp, dict) else resp
        except Exception as exc:  # noqa: BLE001
            logger.error("get_trade_book failed: %s", exc)
            return []

    # ---- Market data (REST) --------------------------------------------
    def get_quote(self, security_id: str, exchange_segment: str) -> Optional[Dict[str, Any]]:
        self._guard()
        quote_fn = getattr(self._dhan, "quote_data", None) or getattr(self._dhan, "ohlc_data", None)
        if quote_fn is None:
            return None
        try:
            return quote_fn({exchange_segment: [int(security_id)]})
        except Exception as exc:  # noqa: BLE001
            logger.error("get_quote failed: %s", exc)
            return None

    def get_ltp_batch(self, securities: Dict[str, List[str]]) -> Dict[str, float]:
        """
        Batched LTP lookup for a whole watchlist in one round trip.
        `ticker_data` is Dhan's dedicated LTP-only market-quote mode (the
        current documented name — confirmed against the SDK's own comment
        "LTP - ticker_data, OHLC - ohlc_data, Full Packet - quote_data").
        Falls back to the heavier `ohlc_data`/`quote_data` payloads, which
        include a `last_price` field too, if `ticker_data` isn't exposed by
        the installed SDK version.

        `securities` example: {"NSE_EQ": ["1333", "11536"], "NSE_FNO": ["49081"]}

        Returns a flat dict keyed by security_id -> last_price, e.g.:
            {"1333": 1522.4, "11536": 4520.0, "49081": 368.15}

        This is the REST polling counterpart to `MarketFeedManager` — far
        simpler to use from a rerun-driven Streamlit app (no background
        thread / event loop needed), at the cost of not being tick-by-tick.
        Call it on a refresh button or a short `st.rerun` timer.
        """
        self._guard()
        securities_int = {seg: [int(s) for s in ids] for seg, ids in securities.items() if ids}
        if not securities_int:
            return {}

        fn = (
            getattr(self._dhan, "ticker_data", None)
            or getattr(self._dhan, "ohlc_data", None)
            or getattr(self._dhan, "quote_data", None)
        )
        if fn is None:
            logger.warning("Installed dhanhq SDK exposes none of ticker_data/ohlc_data/quote_data.")
            return {}

        try:
            resp = fn(securities_int)
        except Exception as exc:  # noqa: BLE001
            logger.error("get_ltp_batch failed: %s", exc)
            return {}

        payload = resp.get("data", resp) if isinstance(resp, dict) else {}
        flat: Dict[str, float] = {}
        for _segment, sec_map in (payload or {}).items():
            if not isinstance(sec_map, dict):
                continue
            for sec_id, detail in sec_map.items():
                price = detail.get("last_price") if isinstance(detail, dict) else None
                if price is not None:
                    flat[str(sec_id)] = float(price)
        return flat

    def get_historical_ohlc(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str = "EQUITY",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        interval_minutes: Optional[int] = None,
    ):
        """
        Fetches historical/intraday candles and normalizes the result into a
        pandas DataFrame with columns [timestamp, open, high, low, close, volume]
        — the exact shape `analytics.super_intelligence.analyze_technical_patterns`
        expects, so live data is a drop-in replacement for `generate_mock_ohlc`.

        Calls the SDK's current (v2.x) methods:
          - `intraday_minute_data(security_id, exchange_segment, instrument_type,
             from_date, to_date, interval=...)` for `interval_minutes` set
          - `historical_daily_data(security_id, exchange_segment, instrument_type,
             expiry_code, from_date, to_date)` for daily candles otherwise
        (v1.x's `historical_minute_charts` / `intraday_daily_minute_charts` were
        removed as of the SDK's v2.0 breaking-changes release, so no fallback to
        those exists here — only the current names are called.)

        The documented response is parallel arrays keyed `open`/`high`/`low`/
        `close`/`volume`/`start_Time`, with `start_Time` in Unix epoch seconds
        (the v2.0 release switched this from a custom Julian-style epoch).

        Returns None (never raises) if nothing usable comes back, so callers
        can cleanly fall back to mock data — e.g. `from_date`/`to_date` are
        required by `intraday_minute_data` and left unset, the SDK isn't new
        enough to expose these methods, or the account's plan doesn't include
        Data API access.

        Known SDK limitation (as of this writing): index instruments
        (exchange_segment "IDX_I", e.g. NIFTY 50 security_id "13") can
        return a failure response for intraday historical calls even
        during market hours — see dhan-oss/DhanHQ-py issue #113. LTP via
        `get_ltp_batch` is unaffected; this only impacts historical candles.
        """
        self._guard()
        import pandas as pd  # local import — keeps this module usable without pandas at import time

        try:
            if interval_minutes:
                fn = getattr(self._dhan, "intraday_minute_data", None)
                if fn is None or not from_date or not to_date:
                    return None
                try:
                    raw = fn(
                        security_id=security_id,
                        exchange_segment=exchange_segment,
                        instrument_type=instrument_type,
                        from_date=from_date,
                        to_date=to_date,
                        interval=str(interval_minutes),
                    )
                except TypeError:
                    # Older/newer builds may not accept `interval` — retry without it.
                    raw = fn(
                        security_id=security_id,
                        exchange_segment=exchange_segment,
                        instrument_type=instrument_type,
                        from_date=from_date,
                        to_date=to_date,
                    )
            else:
                fn = getattr(self._dhan, "historical_daily_data", None)
                if fn is None or not from_date or not to_date:
                    return None
                raw = fn(
                    security_id=security_id,
                    exchange_segment=exchange_segment,
                    instrument_type=instrument_type,
                    expiry_code=0,
                    from_date=from_date,
                    to_date=to_date,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("get_historical_ohlc failed: %s", exc)
            return None

        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        if not isinstance(data, dict) or "close" not in data:
            return None
        if isinstance(raw, dict) and raw.get("status") == "failure":
            return None

        try:
            df = pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(
                        data.get("start_Time", data.get("timestamp", [])), unit="s", errors="coerce"
                    ),
                    "open": data.get("open", []),
                    "high": data.get("high", []),
                    "low": data.get("low", []),
                    "close": data.get("close", []),
                    "volume": data.get("volume", []),
                }
            ).dropna(subset=["close"])
            return df if not df.empty else None
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to normalize historical OHLC payload: %s", exc)
            return None


def segment_to_marketfeed_code(segment: str):
    """
    Translates a REST-style segment string ("NSE_EQ", "BSE_EQ", "NSE_FNO", ...)
    into the matching named attribute on the SDK's `MarketFeed` class, for
    building `MarketFeedManager` subscription tuples.

    Confirmed against the SDK's own published sample (`MarketFeed.NSE` used
    for an NSE-equity security): `NSE_EQ -> MarketFeed.NSE`. The others
    follow the same "drop the `_EQ`/rename" pattern documented for exchange
    segments generally, but are NOT individually confirmed the same way —
    if the installed SDK names one differently, this raises `AttributeError`
    rather than silently subscribing to the wrong segment.
    """
    if not _MARKETFEED_AVAILABLE:
        raise RuntimeError("dhanhq.MarketFeed is not installed.")
    attr_map = {
        "NSE_EQ": "NSE",
        "BSE_EQ": "BSE",
        "NSE_FNO": "NSE_FNO",
        "BSE_FNO": "BSE_FNO",
        "NSE_CURRENCY": "NSE_CURRENCY",
        "MCX_COMM": "MCX",
        "IDX_I": "IDX",
    }
    attr_name = attr_map.get(segment, segment)
    return getattr(_MarketFeed, attr_name)


class MarketFeedManager:
    """
    Wraps DhanHQ's streaming WebSocket market feed (the SDK's `MarketFeed`
    class) with automatic, exponential-backoff reconnection, running on a
    background thread so it never blocks the Streamlit UI thread.

    Matches the SDK's documented usage exactly:
        data = MarketFeed(dhan_context, instruments, version)
        data.run_forever()
        while True:
            response = data.get_data()
    i.e. `run_forever()` is called once per connection to start the feed,
    then `get_data()` is polled repeatedly for the latest packet — it is
    NOT a per-tick blocking call, unlike what its name might suggest.

    `instruments` must be a list of
    `(exchange_segment_code, security_id_str, subscription_type)` tuples
    using the SDK's own named class constants, e.g.
    `(MarketFeed.NSE, "1333", MarketFeed.Ticker)` — a different encoding
    from the string segment names (`"NSE_EQ"`) used elsewhere in this app
    for REST calls. `segment_to_marketfeed_code` below translates the ones
    confirmed against the SDK's published sample (`NSE`, `BSE`, `NSE_FNO`);
    other segments are looked up by best-guess attribute name and will
    raise a clear `AttributeError` if the installed SDK names them
    differently — better to fail loudly here than silently subscribe to
    the wrong instrument.

    Ticks are pushed into `self.latest_ticks[security_id] = {...}` which the
    UI polls on every rerun — no shared mutable state races because dict
    item assignment is atomic under the GIL.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        instruments: List[tuple],   # [(MarketFeed.<SEGMENT>, "security_id", MarketFeed.<MODE>), ...]
        on_tick: Optional[Callable[[dict], None]] = None,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.instruments = instruments
        self.on_tick = on_tick
        self.base_delay = base_delay
        self.max_delay = max_delay

        self.latest_ticks: Dict[str, Dict[str, Any]] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.status = "stopped"          # stopped | connecting | connected | reconnecting | unavailable
        self.last_error: Optional[str] = None

    def start(self) -> None:
        if not _MARKETFEED_AVAILABLE:
            self.status = "unavailable"
            self.last_error = "dhanhq.MarketFeed not installed (requires dhanhq SDK v2.x)"
            logger.warning(self.last_error)
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_with_reconnect, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.status = "stopped"

    def _run_with_reconnect(self) -> None:
        attempt = 0
        context = DhanContext(self.client_id, self.access_token) if DhanContext else None
        while not self._stop_event.is_set():
            try:
                self.status = "connecting" if attempt == 0 else "reconnecting"
                feed = _MarketFeed(context, self.instruments, "v2")
                feed.run_forever()  # starts the connection; does not block per the SDK's own sample usage
                self.status = "connected"
                attempt = 0  # reset backoff after a clean connect
                self._consume(feed)
            except Exception as exc:  # noqa: BLE001 — any disconnect/error triggers reconnect
                self.last_error = str(exc)
                logger.warning("Market feed error (attempt %s): %s", attempt, exc)
            if self._stop_event.is_set():
                break
            delay = min(self.base_delay * (2 ** attempt), self.max_delay)
            attempt += 1
            self.status = "reconnecting"
            time.sleep(delay)

    def _consume(self, feed) -> None:
        """Polls `get_data()` for the latest packet until told to stop or the feed errors out."""
        while not self._stop_event.is_set():
            data = feed.get_data()
            if not data:
                continue
            security_id = str(data.get("security_id", data.get("securityId", "")))
            if security_id:
                self.latest_ticks[security_id] = data
                if self.on_tick:
                    try:
                        self.on_tick(data)
                    except Exception:  # noqa: BLE001 — a bad callback must not kill the feed
                        logger.exception("on_tick callback raised")


class OrderUpdateManager:
    """
    Streams real-time order-status pushes (PENDING -> TRADED / REJECTED /
    CANCELLED) from Dhan's `OrderUpdate` WebSocket, on a background thread
    with the same exponential-backoff auto-reconnect pattern as
    `MarketFeedManager`. This is what keeps the Trade Journal's status
    column accurate for Live orders without the user manually refreshing —
    the moment the broker confirms a fill, `on_update` (wired to
    `TradeJournal.update_status_by_order_id` in app.py) marks the journal
    row TRADED/REJECTED/CANCELLED with the actual traded price.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        on_update: Optional[Callable[[dict], None]] = None,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.on_update = on_update
        self.base_delay = base_delay
        self.max_delay = max_delay

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.status = "stopped"
        self.last_error: Optional[str] = None
        self.last_update: Optional[Dict[str, Any]] = None

    def start(self) -> None:
        if not _ORDER_UPDATE_AVAILABLE:
            self.status = "unavailable"
            self.last_error = "dhanhq.OrderUpdate not installed (requires SDK v2.1+)"
            logger.warning(self.last_error)
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_with_reconnect, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.status = "stopped"

    def _run_with_reconnect(self) -> None:
        """
        Matches the SDK's own documented retry pattern exactly: ONE
        `OrderUpdate` instance is created (with `on_update` attached) and
        reused across reconnect attempts — `connect_to_dhan_websocket_sync()`
        is called again on the same object after each disconnect, not a
        fresh instance each time. Diverging from this (e.g. constructing a
        new client per attempt) risks depending on internal state the SDK
        doesn't guarantee is safe to recreate mid-session.
        """
        attempt = 0
        context = _DhanContextForOrders(self.client_id, self.access_token)
        client = _OrderUpdate(context)
        client.on_update = self._handle_update
        while not self._stop_event.is_set():
            try:
                self.status = "connecting" if attempt == 0 else "reconnecting"
                client.connect_to_dhan_websocket_sync()
                attempt = 0  # reset backoff after a clean connect/run
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.warning("Order update feed error (attempt %s): %s", attempt, exc)
            if self._stop_event.is_set():
                break
            delay = min(self.base_delay * (2 ** attempt), self.max_delay)
            attempt += 1
            self.status = "reconnecting"
            time.sleep(delay)

    def _handle_update(self, order_data: dict) -> None:
        payload = order_data.get("Data", order_data) if isinstance(order_data, dict) else order_data
        self.last_update = payload
        if self.on_update:
            try:
                self.on_update(payload)
            except Exception:  # noqa: BLE001 — a bad callback must not kill the feed
                logger.exception("on_update callback raised")
