"""
app.py
------
Entry point: `streamlit run app.py`

Tabs:
  1. Execution & Mode      — global Paper/Live toggle + manual order ticket
  2. Market Overview       — MTM, positions, margin, order book
  3. Trade Journal         — full trade history (DB-backed, CSV export)
  4. Performance Analysis  — Sharpe, Max DD, Win Rate, Profit Factor
  5. Super Intelligence    — anomaly detection, pattern recognition, risk recs
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))  # allow `core.` / `analytics.` imports when run directly

from config import TradingMode, ProductType, SegmentType, SETTINGS, DhanCredentials
from database import TradeJournal, RecommendationRecord
from core.auth import is_auth_required, verify_password
from core.dhan_client import DhanClient, OrderUpdateManager
from core.live_fifo_tracker import LiveFIFOTracker
from core.paper_trading import PaperTradingEngine
from core.order_manager import OrderManager, OrderRequest
from analytics import performance as perf
from analytics import super_intelligence as si

st.set_page_config(page_title="Dhan Algo Trading Dashboard", layout="wide", page_icon="📈")


# ---------------------------------------------------------------------------
# App-level login gate (separate from Dhan credentials — see core/auth.py)
# ---------------------------------------------------------------------------
def render_login_gate() -> None:
    """Blocks the rest of the app from rendering until APP_PASSWORD is verified."""
    if not is_auth_required(SETTINGS.app_password):
        return  # no password configured — e.g. pure localhost use
    if st.session_state.get("authenticated"):
        return

    if "login_attempts" not in st.session_state:
        st.session_state.login_attempts = 0

    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.title("🔒 Dhan Trading Dashboard")
        st.caption("Enter the dashboard password to continue.")
        with st.form("login_form"):
            entered_password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", use_container_width=True)

        if submitted:
            if verify_password(entered_password, SETTINGS.app_password):
                st.session_state.authenticated = True
                st.session_state.login_attempts = 0
                st.rerun()
            else:
                st.session_state.login_attempts += 1
                # A short escalating delay slows down naive brute-forcing without
                # a full lockout mechanism (which would need persistent storage
                # per-IP to survive a session reset — out of scope for a
                # single-user dashboard, but worth adding if multi-tenant later).
                time.sleep(min(1.5 * st.session_state.login_attempts, 8.0))
                st.error("Incorrect password.")

    st.stop()  # nothing below this point renders until authenticated


render_login_gate()


# ---------------------------------------------------------------------------
# Session-state bootstrapping (runs once per browser session)
# ---------------------------------------------------------------------------
def init_state():
    if "journal" not in st.session_state:
        st.session_state.journal = TradeJournal(SETTINGS.db_path)
    if "paper_engine" not in st.session_state:
        st.session_state.paper_engine = PaperTradingEngine(
            starting_capital=SETTINGS.paper_starting_capital,
            slippage_pct=SETTINGS.default_slippage_pct,
        )
    if "dhan_client" not in st.session_state:
        st.session_state.dhan_client = None
    if "order_update_manager" not in st.session_state:
        st.session_state.order_update_manager = None
    if "live_fifo_tracker" not in st.session_state:
        st.session_state.live_fifo_tracker = LiveFIFOTracker(st.session_state.journal)
        st.session_state.live_fifo_tracker.seed_from_open_positions()
    if "order_manager" not in st.session_state:
        st.session_state.order_manager = OrderManager(
            journal=st.session_state.journal,
            paper_engine=st.session_state.paper_engine,
            dhan_client=st.session_state.dhan_client,
        )
    if "trading_mode" not in st.session_state:
        st.session_state.trading_mode = TradingMode.PAPER
    if "ltp_cache" not in st.session_state:
        # security_id -> last known price, used to mark paper positions to market
        st.session_state.ltp_cache = {}
    if "watchlist" not in st.session_state:
        # list of {"name", "security_id", "exchange_segment"} — drives live LTP polling
        st.session_state.watchlist = [
            {"name": "RELIANCE", "security_id": "1333", "exchange_segment": "NSE_EQ"},
            {"name": "NIFTY 50 (Index)", "security_id": "13", "exchange_segment": "IDX_I"},
        ]


init_state()


# ---------------------------------------------------------------------------
# Sidebar: credentials + global mode toggle (shared across every tab)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("⚙️ Session Setup")

    if is_auth_required(SETTINGS.app_password):
        if st.button("🚪 Log out", use_container_width=True):
            st.session_state.authenticated = False
            st.rerun()
        st.divider()

    st.caption("Credentials are held only in memory for this session and are never written to disk.")
    default_creds = DhanCredentials()
    client_id_input = st.text_input("Dhan Client ID", value=default_creds.client_id, type="password")
    access_token_input = st.text_input("Dhan Access Token", value=default_creds.access_token, type="password")

    if st.button("Connect to Dhan", use_container_width=True):
        if not client_id_input or not access_token_input:
            st.error("Both Client ID and Access Token are required.")
        else:
            client = DhanClient(client_id_input, access_token_input)
            ok = client.connect()
            st.session_state.dhan_client = client
            st.session_state.order_manager.dhan_client = client
            if ok:
                st.success("Connected to Dhan REST API.")

                # Capture these as plain closures NOW (on the main Streamlit
                # thread, where st.session_state is valid) rather than reading
                # st.session_state.* inside the callback below. The callback
                # runs on OrderUpdateManager's background thread, which has no
                # Streamlit ScriptRunContext attached — st.session_state access
                # there is unsupported and would silently break live fill sync.
                # TradeJournal and LiveFIFOTracker are plain, already-thread-safe
                # Python objects, so holding direct references to them is safe.
                journal_ref = st.session_state.journal
                fifo_tracker_ref = st.session_state.live_fifo_tracker

                def _sync_journal_on_order_update(payload: dict) -> None:
                    """
                    Maps a Dhan order-status push onto the journal. For a
                    confirmed fill (TRADED/EXECUTED), routes through the FIFO
                    tracker so realized PnL is computed correctly if this
                    fill closed (fully or partially) an existing position,
                    rather than blindly marking every fill OPEN.
                    """
                    order_id = str(payload.get("orderId", payload.get("OrderNo", "")))
                    dhan_status = str(payload.get("orderStatus", payload.get("Status", ""))).upper()
                    traded_price = payload.get("tradedPrice") or payload.get("TradedPrice")

                    if dhan_status in ("TRADED", "EXECUTED") and order_id:
                        trade_row = journal_ref.get_trade_by_order_id(order_id)
                        if trade_row and traded_price:
                            fifo_tracker_ref.process_fill(
                                trade_id=trade_row["id"],
                                security_id=str(trade_row["security_id"]),
                                side=trade_row["side"],
                                quantity=float(trade_row["quantity"]),
                                price=float(traded_price),
                            )
                        elif order_id:
                            # No traded price yet (or row not found) — at least confirm it's live.
                            journal_ref.update_status_by_order_id(order_id, "OPEN")
                    elif dhan_status == "REJECTED" and order_id:
                        journal_ref.update_status_by_order_id(order_id, "REJECTED")
                    elif dhan_status == "CANCELLED" and order_id:
                        journal_ref.update_status_by_order_id(order_id, "CANCELLED")

                oum = OrderUpdateManager(client_id_input, access_token_input, on_update=_sync_journal_on_order_update)
                oum.start()
                st.session_state.order_update_manager = oum
            else:
                st.warning(
                    "Could not initialise the live SDK (not installed, or bad credentials). "
                    "You can still use Paper Trading mode."
                )

    st.divider()
    st.subheader("🔀 Trading Mode")
    mode_choice = st.radio(
        "Select execution mode",
        options=[TradingMode.PAPER.value, TradingMode.LIVE.value],
        index=0 if st.session_state.trading_mode == TradingMode.PAPER else 1,
        help="Paper Trading simulates fills locally. Live Trading sends real orders to Dhan.",
    )
    st.session_state.trading_mode = TradingMode(mode_choice)

    if st.session_state.trading_mode == TradingMode.LIVE:
        st.error("⚠️ LIVE mode is armed. Orders placed below will use REAL money.", icon="⚠️")
    else:
        st.success("🧪 Paper mode — all fills are simulated.", icon="🧪")
        st.session_state.paper_engine.slippage_pct = st.slider(
            "Simulated slippage (%)", 0.0, 1.0, SETTINGS.default_slippage_pct, 0.01
        )

    st.divider()
    if st.session_state.order_update_manager is not None:
        oum_status = st.session_state.order_update_manager.status
        status_icon = {"connected": "🟢", "connecting": "🟡", "reconnecting": "🟡", "unavailable": "⚪", "stopped": "⚪"}
        st.caption(f"{status_icon.get(oum_status, '⚪')} Live order-update feed: {oum_status}")

    st.divider()
    if st.button("↻ Reset Paper Account", use_container_width=True):
        st.session_state.paper_engine.reset()
        st.success("Paper account reset to starting capital.")

mode = st.session_state.trading_mode
om: OrderManager = st.session_state.order_manager
journal: TradeJournal = st.session_state.journal

st.title("📈 Dhan Algo Trading Dashboard")

tab_exec, tab_overview, tab_journal, tab_perf, tab_si = st.tabs(
    ["🎯 Execution & Mode", "📊 Market Overview", "📒 Trade Journal", "📈 Performance Analysis", "🧠 Super Intelligence"]
)


# ---------------------------------------------------------------------------
# TAB 1 — Execution & Mode
# ---------------------------------------------------------------------------
with tab_exec:
    st.subheader("Manual Order Ticket")
    st.caption(f"Current mode: **{mode.value}**")

    watchlist_names = [w["name"] for w in st.session_state.watchlist]
    picked = st.selectbox(
        "Quick-fill from Watchlist (optional)", ["— manual entry —"] + watchlist_names
    )
    picked_item = next((w for w in st.session_state.watchlist if w["name"] == picked), None)
    picked_ltp = st.session_state.ltp_cache.get(picked_item["security_id"]) if picked_item else None

    col1, col2, col3 = st.columns(3)
    with col1:
        instrument = st.text_input("Instrument / Symbol", value=picked_item["name"] if picked_item else "RELIANCE")
        security_id = st.text_input(
            "Security ID (Dhan)", value=picked_item["security_id"] if picked_item else "1333"
        )
        instrument_type = st.selectbox("Instrument Type", ["EQUITY", "FUTURE", "OPTION"])
    with col2:
        segment_options = [s.value for s in SegmentType] + ["IDX_I"]
        default_segment = picked_item["exchange_segment"] if picked_item else segment_options[0]
        segment = st.selectbox("Exchange Segment", segment_options, index=segment_options.index(default_segment))
        product_type = st.selectbox("Product Type (Intraday/Delivery/F&O)", [p.value for p in ProductType])
        side = st.selectbox("Side", ["BUY", "SELL"])
    with col3:
        quantity = st.number_input("Quantity", min_value=1, value=10, step=1)
        order_type = st.selectbox("Order Type", ["MARKET", "LIMIT", "SL", "SL-M"])
        ref_price = st.number_input(
            "Reference LTP (paper mode) / Limit Price",
            min_value=0.0,
            value=float(picked_ltp) if picked_ltp else 2500.0,
            step=0.05,
            help="Auto-filled from the watchlist's last refreshed live price when available.",
        )

    strategy_tag = st.text_input("Strategy tag", value="manual")

    if st.button("🚀 Place Order", type="primary"):
        order = OrderRequest(
            instrument=instrument,
            security_id=security_id,
            exchange_segment=segment,
            side=side,
            quantity=int(quantity),
            order_type=order_type,
            product_type=product_type,
            instrument_type=instrument_type,
            price=ref_price,
            strategy_tag=strategy_tag,
        )
        try:
            result = om.execute(mode, order, ltp_hint=ref_price)
            st.session_state.ltp_cache[security_id] = ref_price
            st.success(f"Order executed: {result.get('order_id', result)}")
            st.json(result, expanded=False)
        except Exception as exc:  # noqa: BLE001 — surface to the trader, never crash the app
            st.error(f"Order failed: {exc}")

    st.info(
        "Tip: in Paper Trading, the 'Reference LTP' you enter here becomes the simulated fill "
        "reference (slippage is applied on top). In Live Trading, it's used as the limit price "
        "for non-market orders."
    )

    if mode == TradingMode.LIVE:
        st.divider()
        st.subheader("Modify / Cancel Open Orders")
        dhan_client_exec: DhanClient = st.session_state.dhan_client
        if dhan_client_exec is None or not dhan_client_exec.connected:
            st.caption("Connect to Dhan (sidebar) to manage open orders.")
        else:
            if st.button("🔄 Refresh Order List"):
                st.session_state["_order_list_cache"] = dhan_client_exec.get_order_list()
            order_list = st.session_state.get("_order_list_cache", [])
            open_orders = [
                o for o in order_list
                if str(o.get("orderStatus", o.get("status", ""))).upper() in ("PENDING", "TRANSIT", "OPEN")
            ]
            if not open_orders:
                st.caption("No open/pending orders (click Refresh to check again).")
            else:
                for o in open_orders:
                    order_id = str(o.get("orderId", o.get("id", "")))
                    with st.expander(f"Order {order_id} — {o.get('tradingSymbol', o.get('instrument', ''))}"):
                        st.json(o, expanded=False)
                        mc1, mc2, mc3 = st.columns(3)
                        new_qty = mc1.number_input("New quantity", min_value=1, value=int(o.get("quantity", 1)), key=f"qty_{order_id}")
                        new_price = mc2.number_input("New price", min_value=0.0, value=float(o.get("price", 0.0)), key=f"price_{order_id}")
                        if mc3.button("✏️ Modify", key=f"mod_{order_id}"):
                            try:
                                resp = dhan_client_exec.modify_order(
                                    order_id,
                                    order_type=o.get("orderType", "LIMIT"),
                                    leg_name=o.get("legName", ""),
                                    quantity=int(new_qty),
                                    price=float(new_price),
                                    trigger_price=float(o.get("triggerPrice", 0.0)),
                                    disclosed_quantity=int(o.get("disclosedQuantity", 0)),
                                    validity=o.get("validity", "DAY"),
                                )
                                st.success(f"Modify request sent: {resp}")
                            except Exception as exc:  # noqa: BLE001
                                st.error(f"Modify failed: {exc}")
                        if st.button("🗑️ Cancel", key=f"cancel_{order_id}"):
                            try:
                                resp = dhan_client_exec.cancel_order(order_id)
                                journal.update_status_by_order_id(order_id, "CANCELLED")
                                st.success(f"Cancel request sent: {resp}")
                            except Exception as exc:  # noqa: BLE001
                                st.error(f"Cancel failed: {exc}")


# ---------------------------------------------------------------------------
# TAB 2 — Market Overview
# ---------------------------------------------------------------------------
with tab_overview:
    st.subheader("Watchlist & Live Prices")
    wl_col1, wl_col2 = st.columns([3, 1])
    with wl_col1:
        with st.form("add_watchlist_item", clear_on_submit=True):
            f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
            wl_name = f1.text_input("Name", placeholder="INFY")
            wl_sec_id = f2.text_input("Security ID", placeholder="1594")
            wl_segment = f3.selectbox("Segment", [s.value for s in SegmentType] + ["IDX_I"], key="wl_segment")
            add_clicked = f4.form_submit_button("➕ Add")
            if add_clicked and wl_name and wl_sec_id:
                st.session_state.watchlist.append(
                    {"name": wl_name, "security_id": wl_sec_id, "exchange_segment": wl_segment}
                )
    with wl_col2:
        st.write("")
        st.write("")
        refresh_clicked = st.button("🔄 Refresh Live Prices", use_container_width=True)

    dhan_client: DhanClient = st.session_state.dhan_client
    if refresh_clicked:
        if dhan_client is not None and dhan_client.connected:
            grouped: dict[str, list] = {}
            for item in st.session_state.watchlist:
                grouped.setdefault(item["exchange_segment"], []).append(item["security_id"])
            fetched = dhan_client.get_ltp_batch(grouped)
            if fetched:
                st.session_state.ltp_cache.update(fetched)
                st.success(f"Updated {len(fetched)} live price(s).")
            else:
                st.warning("No prices returned — check security IDs / segments, or SDK availability.")
        else:
            st.info("Connect to Dhan (sidebar) to pull real live prices. Showing last cached/manual values.")

    if st.session_state.watchlist:
        wl_rows = [
            {
                **item,
                "last_price": st.session_state.ltp_cache.get(item["security_id"], "—"),
            }
            for item in st.session_state.watchlist
        ]
        st.dataframe(pd.DataFrame(wl_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Watchlist is empty — add an instrument above to start tracking live prices.")

    st.divider()
    st.subheader("Account Snapshot")
    margin = om.get_margin(mode)
    positions = om.get_positions(mode)
    order_book = om.get_order_book(mode)

    mtm = 0.0
    if mode == TradingMode.PAPER:
        mtm = st.session_state.paper_engine.mark_to_market(st.session_state.ltp_cache)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Mode", mode.value)
    m2.metric("Unrealized MTM", f"₹{mtm:,.2f}" if mode == TradingMode.PAPER else "—",
              help="Live MTM requires the market feed / positions API; paper MTM uses cached reference prices.")
    m3.metric("Available Margin", f"₹{margin.get('availableBalance', 0):,.2f}" if margin else "—")
    m4.metric("Open Positions", len(positions))

    st.divider()
    left, right = st.columns(2)
    with left:
        st.markdown("**Active Positions** (all types: Intraday / Delivery / Futures / Options)")
        if positions:
            st.dataframe(pd.DataFrame(positions), use_container_width=True)
        else:
            st.caption("No open positions.")
    with right:
        st.markdown("**Order Book**")
        if order_book:
            st.dataframe(pd.DataFrame(order_book), use_container_width=True)
        else:
            st.caption("No orders yet.")

    st.caption(
        "💡 For Live mode, wire `core/dhan_client.MarketFeedManager` to your instrument watchlist "
        "on app startup to get true real-time MTM instead of the last traded reference price."
    )


# ---------------------------------------------------------------------------
# TAB 3 — Trade Journal
# ---------------------------------------------------------------------------
with tab_journal:
    st.subheader("Trade Journal")
    df = journal.fetch_all()

    filt1, filt2 = st.columns(2)
    with filt1:
        mode_filter = st.multiselect(
            "Filter by mode", [TradingMode.PAPER.value, TradingMode.LIVE.value],
            default=[TradingMode.PAPER.value, TradingMode.LIVE.value],
        )
    with filt2:
        status_filter = st.multiselect(
            "Filter by status", ["OPEN", "CLOSED", "REJECTED"], default=["OPEN", "CLOSED", "REJECTED"]
        )

    if not df.empty:
        view = df[df["mode"].isin(mode_filter) & df["status"].isin(status_filter)]
        st.dataframe(
            view[
                [
                    "timestamp", "instrument", "mode", "side", "quantity",
                    "entry_price", "exit_price", "product_type", "status", "pnl", "strategy_tag",
                ]
            ],
            use_container_width=True,
        )
        csv_path = os.path.join(os.path.dirname(SETTINGS.db_path) or ".", "trade_journal_export.csv")
        journal.export_csv(csv_path)
        with open(csv_path, "rb") as f:
            st.download_button("⬇️ Export full journal as CSV", f, file_name="trade_journal.csv")
    else:
        st.info("No trades logged yet — place an order from the Execution tab to get started.")


# ---------------------------------------------------------------------------
# TAB 4 — Performance Analysis
# ---------------------------------------------------------------------------
with tab_perf:
    st.subheader("Performance Analysis")
    df = journal.fetch_all()

    use_mock = df[df["status"] == "CLOSED"].shape[0] < 3 if not df.empty else True
    if use_mock:
        st.warning("Fewer than 3 closed trades logged — showing illustrative mock data below the divider so "
                    "you can preview the analytics. Real numbers will replace this once you have trade history.")
    source_df = si.generate_mock_trades() if use_mock else df

    summary = perf.summarize(source_df, SETTINGS.paper_starting_capital, SETTINGS.risk_free_rate_annual)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sharpe Ratio", f"{summary.sharpe_ratio:.2f}")
    c2.metric("Max Drawdown", f"{summary.max_drawdown_pct:.2f}%", f"₹{summary.max_drawdown_value:,.0f}")
    c3.metric("Win Rate", f"{summary.win_rate:.1f}%")
    c4.metric("Profit Factor", f"{summary.profit_factor:.2f}")

    c5, c6, c7 = st.columns(3)
    c5.metric("Total Realized PnL", f"₹{summary.total_pnl:,.2f}")
    c6.metric("Avg Win", f"₹{summary.avg_win:,.2f}")
    c7.metric("Avg Loss", f"₹{summary.avg_loss:,.2f}")

    equity_curve = perf.build_equity_curve(source_df, SETTINGS.paper_starting_capital)
    if not equity_curve.empty:
        fig = px.line(equity_curve, x="timestamp", y="equity", title="Equity Curve")
        st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# TAB 5 — Super Intelligence
# ---------------------------------------------------------------------------
with tab_si:
    st.subheader("🧠 Super Intelligence")
    st.caption(
        "Statistical anomaly detection + rule-based technical & risk analytics. "
        "Clearly-marked mock data fills in wherever live history/feeds aren't available yet."
    )

    df = journal.fetch_all()
    use_mock_trades = df[df["status"] == "CLOSED"].shape[0] < 5 if not df.empty else True
    trades_source = si.generate_mock_trades() if use_mock_trades else df
    if use_mock_trades:
        st.caption("📎 Trade insights below use mock trade history (fewer than 5 real closed trades logged).")

    st.markdown("### 1️⃣ Trade Insights & Anomaly Detection")
    insights = si.trade_insights(trades_source)
    for line in insights:
        st.write(f"- {line}")

    anomalies = si.detect_trade_anomalies(trades_source)
    if anomalies:
        st.markdown("**Flagged trades:**")
        st.dataframe(pd.DataFrame([a.__dict__ for a in anomalies]), use_container_width=True)
    else:
        st.caption("No anomalies detected in the current trade sample.")

    st.divider()
    st.markdown("### 2️⃣ Technical Pattern Recognition & ML Direction Signal")
    ohlc = None
    picked_wl = None
    if dhan_client is not None and dhan_client.connected and st.session_state.watchlist:
        pattern_pick = st.selectbox(
            "Instrument for pattern analysis",
            [w["name"] for w in st.session_state.watchlist],
            key="pattern_pick",
        )
        picked_wl = next((w for w in st.session_state.watchlist if w["name"] == pattern_pick), None)
        if picked_wl is not None and picked_wl.get("exchange_segment") == "IDX_I":
            st.caption(
                "⚠️ Index instruments (IDX_I) have a known DhanHQ SDK issue where intraday historical "
                "data calls can return a failure response — live LTP still works fine. "
                "See github.com/dhan-oss/DhanHQ-py/issues/113."
            )
        if st.button("📥 Fetch live historical candles"):
            # intraday_minute_data covers the last 5 trading days per Dhan's docs;
            # a 5-day lookback window is a safe default that stays within that limit.
            today = datetime.now().date()
            ohlc = dhan_client.get_historical_ohlc(
                security_id=picked_wl["security_id"],
                exchange_segment=picked_wl["exchange_segment"],
                interval_minutes=5,
                from_date=(today - timedelta(days=5)).isoformat(),
                to_date=today.isoformat(),
            )
            if ohlc is None:
                st.warning("Live historical data unavailable for this instrument/SDK version — using mock data below.")
    if ohlc is None:
        st.caption("📎 Using mock OHLC data — connect Dhan and pick a watchlist instrument above for a real feed.")
        ohlc = si.generate_mock_ohlc()
    pattern = si.analyze_technical_patterns(ohlc)

    p1, p2, p3 = st.columns(3)
    p1.metric("Verdict", pattern["verdict"])
    p2.metric("RSI (14)", pattern["rsi14"], pattern["rsi_note"])
    p3.metric("MACD Cross", pattern["macd_cross"])

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=pattern["series"]["timestamp"], y=pattern["series"]["close"], name="Close"))
    fig2.add_trace(go.Scatter(x=pattern["series"]["timestamp"], y=pattern["series"]["sma20"], name="SMA20"))
    fig2.add_trace(go.Scatter(x=pattern["series"]["timestamp"], y=pattern["series"]["sma50"], name="SMA50"))
    fig2.update_layout(title="Price Action with SMA Overlay", height=350)
    st.plotly_chart(fig2, use_container_width=True)

    st.markdown("**Trained ML next-bar direction classifier** (RandomForest, fit in-session on the series above)")
    ml_result = si.train_next_bar_classifier(ohlc)
    if ml_result is None:
        st.caption("Not enough history yet for a meaningful fit (need ~50+ bars), or scikit-learn isn't installed.")
    else:
        ml1, ml2, ml3 = st.columns(3)
        ml1.metric("Next-bar prediction", ml_result["predicted_direction"], f"{ml_result['probability_next_bar_up']:.0%} prob. up")
        ml2.metric("Holdout accuracy", f"{ml_result['holdout_accuracy']:.0%}", f"n={ml_result['holdout_size']}")
        ml3.metric("Naive baseline", f"{ml_result['baseline_accuracy']:.0%}", help="Accuracy from always predicting the majority class")
        if ml_result["holdout_accuracy"] <= ml_result["baseline_accuracy"]:
            st.caption(
                "⚠️ Holdout accuracy isn't beating the naive baseline on this sample — treat the prediction as "
                "noise, not a signal, until it does. This is intentionally shown rather than hidden."
            )
        with st.expander("Feature importances"):
            st.bar_chart(pd.Series(ml_result["feature_importances"]))

    st.divider()
    st.markdown("### 2️⃣b Real-Time Market Sentiment (news headlines)")
    default_query = picked_wl["name"] if picked_wl is not None else "NIFTY 50"
    sentiment_query = st.text_input("Sentiment search query", value=default_query)
    if st.button("📰 Fetch & score headlines"):
        with st.spinner("Fetching headlines..."):
            sentiment = si.market_sentiment(sentiment_query)
        st.session_state["_last_sentiment"] = sentiment

    sentiment = st.session_state.get("_last_sentiment")
    if sentiment is None:
        st.caption("Click the button above to pull live headlines (requires internet access) and score them.")
    elif sentiment["label"] == "NO DATA":
        st.warning("No headlines retrieved — check internet connectivity, or try a different query.")
    else:
        s1, s2, s3 = st.columns(3)
        s1.metric("Sentiment", sentiment["label"], f"{sentiment['normalized_score']:+.2f}")
        s2.metric("Positive headlines", sentiment["positive_count"])
        s3.metric("Negative headlines", sentiment["negative_count"])
        with st.expander(f"Scored headlines ({sentiment['total_headlines']})"):
            for h in sentiment["headlines"]:
                tag = "🟢" if h.score > 0 else "🔴" if h.score < 0 else "⚪"
                st.write(f"{tag} {h.title}")
                if h.matched_positive or h.matched_negative:
                    st.caption(f"matched: +{h.matched_positive} / -{h.matched_negative}")
        st.caption(
            "📎 Lexicon-based scoring against headline titles (Google News RSS, no API key) — "
            "transparent and auditable, not a trained sentiment model."
        )

    st.divider()
    st.markdown("### 3️⃣ Dynamic Risk-Adjustment Recommendations")
    positions_count = len(om.get_positions(mode))
    summary_for_risk = perf.summarize(trades_source, SETTINGS.paper_starting_capital, SETTINGS.risk_free_rate_annual)
    for rec in si.risk_recommendations(summary_for_risk, positions_count, SETTINGS.paper_starting_capital):
        st.warning(rec) if "reduce" in rec.lower() or "drawdown" in rec.lower() else st.info(rec)

    st.markdown("**Kelly-criterion position sizing**")
    kelly = si.kelly_position_size(summary_for_risk, SETTINGS.paper_starting_capital)
    if kelly["status"] == "ok":
        k1, k2, k3 = st.columns(3)
        k1.metric("Full Kelly", f"{kelly['kelly_fraction']:.1%}")
        k2.metric("Half-Kelly (recommended)", f"{kelly['half_kelly_fraction']:.1%}")
        k3.metric("Suggested allocation", f"₹{kelly['recommended_capital_allocation']:,.0f}")
        st.caption(kelly["message"])
    else:
        st.info(kelly["message"])

    # -----------------------------------------------------------------
    # 4️⃣ Human-in-the-loop composite recommendation
    # -----------------------------------------------------------------
    st.divider()
    st.markdown("### 4️⃣ Human + Super Intelligence — Composite Recommendation")
    st.caption(
        "Combines the technical, ML, and sentiment signals above into one advisory call. "
        "**Nothing here ever executes automatically** — an order is only placed if you explicitly "
        "click Approve below, and every recommendation (approved, dismissed, or ignored) is logged "
        "to the audit trail either way."
    )

    recommendation = si.generate_recommendation(pattern, ml_result, sentiment)
    badge = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(recommendation.action, "⚪")
    st.subheader(f"{badge} {recommendation.action} · {recommendation.confidence} confidence · score {recommendation.score:+.2f}")
    for line in recommendation.rationale:
        st.write(f"- {line}")

    # Log the instant a *new* recommendation appears (not on every rerun / widget interaction).
    rec_signature = (recommendation.action, recommendation.confidence, recommendation.score, id(picked_wl))
    if st.session_state.get("_last_rec_signature") != rec_signature:
        rec_instrument = picked_wl["name"] if picked_wl is not None else sentiment_query
        rec_id = journal.log_recommendation(
            RecommendationRecord(
                instrument=rec_instrument,
                action=recommendation.action,
                confidence=recommendation.confidence,
                score=recommendation.score,
                rationale="\n".join(recommendation.rationale),
            )
        )
        st.session_state["_last_rec_id"] = rec_id
        st.session_state["_last_rec_signature"] = rec_signature

    if recommendation.action == "HOLD":
        st.caption("Recommendation is HOLD — nothing to approve or dismiss.")
    elif picked_wl is None:
        st.info("Select a connected watchlist instrument above (Section 2️⃣) to enable order placement from a recommendation.")
        if st.button("❌ Dismiss recommendation", key="dismiss_no_wl"):
            journal.decide_recommendation(st.session_state["_last_rec_id"], "DISMISSED")
            st.info("Dismissed and logged.")
    else:
        rc1, rc2, rc3 = st.columns([1, 1, 1])
        rec_qty = rc1.number_input("Quantity", min_value=1, value=10, step=1, key="rec_qty")
        if rc2.button(f"✅ Approve — place {recommendation.action} order ({mode.value})", type="primary"):
            rec_ltp = st.session_state.ltp_cache.get(picked_wl["security_id"]) or float(ohlc["close"].iloc[-1])
            approve_order = OrderRequest(
                instrument=picked_wl["name"],
                security_id=picked_wl["security_id"],
                exchange_segment=picked_wl["exchange_segment"],
                side=recommendation.action,
                quantity=int(rec_qty),
                strategy_tag="super_intelligence_approved",
            )
            try:
                exec_result = om.execute(mode, approve_order, ltp_hint=rec_ltp)
                journal.decide_recommendation(
                    st.session_state["_last_rec_id"], "APPROVED", resulting_trade_id=exec_result.get("trade_id")
                )
                st.success(f"Order placed per your approval: {exec_result.get('order_id', exec_result)}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Order failed: {exc}")
        if rc3.button("❌ Dismiss", key="dismiss_with_wl"):
            journal.decide_recommendation(st.session_state["_last_rec_id"], "DISMISSED")
            st.info("Dismissed and logged.")

    with st.expander("📋 Recommendation audit trail (all Super Intelligence suggestions + human decisions)"):
        rec_history = journal.fetch_recommendations()
        if rec_history.empty:
            st.caption("No recommendations logged yet.")
        else:
            st.dataframe(
                rec_history[["timestamp", "instrument", "action", "confidence", "score", "human_decision", "decided_at"]],
                use_container_width=True,
            )
