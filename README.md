# Dhan Algo Trading Dashboard

A modular Streamlit dashboard for Indian-market algo trading via the
[DhanHQ](https://dhanhq.co) API — Intraday, Delivery, Futures, and Options —
with a Paper/Live trading toggle, trade journal, performance analytics, and
a rule-based "Super Intelligence" insights panel.

## Architecture

```
trading_dashboard/
├── app.py                     # Streamlit UI — 5 tabs
├── config.py                  # env-based settings & credential loading
├── database.py                # SQLite trade journal (TradeJournal, TradeRecord)
├── core/
│   ├── auth.py                 # app-level login gate (separate from Dhan creds)
│   ├── dhan_client.py          # DhanClient (REST) + MarketFeedManager (WS, auto-reconnect)
│   ├── order_update_manager    # (class inside dhan_client.py) live fill/reject/cancel sync
│   ├── live_fifo_tracker.py    # FIFO position netting -> realized PnL for live trades
│   ├── paper_trading.py        # PaperTradingEngine (simulated fills + slippage)
│   └── order_manager.py        # Routes orders to paper/live, logs to journal
├── analytics/
│   ├── performance.py         # Sharpe, Max DD, Win Rate, Profit Factor (vectorized)
│   └── super_intelligence.py  # Anomaly detection, pattern recognition, risk recs
├── tests/                     # pytest suite (32 tests, no live credentials needed)
├── Dockerfile / docker-compose.yml
├── requirements.txt / requirements-dev.txt
├── .env.example
└── data/                      # SQLite DB + CSV exports (git-ignored)
```

## Human + Super Intelligence (human-in-the-loop trading)

Section 4️⃣ of the Super Intelligence tab combines the technical, ML, and
sentiment signals above into one advisory recommendation — but it never
places an order by itself:

- `analytics/super_intelligence.py::generate_recommendation` runs a
  transparent weighted vote (each available signal contributes -1..+1;
  an unskilled ML model that doesn't beat its own baseline is excluded
  from the vote entirely) into a BUY/SELL/HOLD call with a confidence
  level based on cross-signal *agreement*, not magnitude — HIGH
  confidence requires 2+ signals and full agreement, never one signal
  alone.
- Every recommendation is logged to a `recommendations` table the moment
  it's generated — instrument, action, confidence, score, full rationale
  — via `TradeJournal.log_recommendation`.
- The UI requires an explicit **Approve** or **Dismiss** click before
  anything happens. Approving routes straight through the same
  `OrderManager.execute` used everywhere else (respecting Paper/Live mode)
  and links the resulting trade back to the recommendation row via
  `decide_recommendation`; dismissing just records the decision.
- The "📋 Recommendation audit trail" expander shows the full history —
  proof, after the fact, that a human made every trading call, not the
  model.

This is intentionally the opposite of full automation: Super Intelligence
proposes, a human disposes.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

76 unit tests cover the trade journal (SQLite, including order-status sync
and realized-PnL accumulation), the paper-trading fill/PnL engine, the
live FIFO position-netting tracker (partial closes, position flips, FIFO
ordering, session-restart reseeding), the order manager's paper/live
routing, Dhan REST response parsing (LTP batch, historical OHLC),
vectorized performance metrics, the full Super Intelligence suite
(anomaly detection, lexicon sentiment scoring, the ML next-bar classifier,
Kelly-criterion sizing, the composite human-in-the-loop recommendation
engine and its audit-trail logging), and the app-level login gate — no
live Dhan credentials or internet access required, since paper mode and
mocked SDK/network responses are used throughout.

A GitHub Actions workflow (`.github/workflows/tests.yml`) runs the full
suite plus a whole-repo syntax check on every push/PR, across Python
3.10–3.12.

## Docker deployment

```bash
cp .env.example .env        # fill in your real credentials AND an APP_PASSWORD
docker compose up --build
```

This builds the image, mounts `./data` so the SQLite trade journal survives
container restarts, and serves the dashboard on `http://localhost:8501`.
Credentials are passed via `.env` (loaded by `env_file:` in
`docker-compose.yml`) — never baked into the image itself. **Set
`APP_PASSWORD`** in that `.env` before exposing the container beyond your
own machine — see "Security notes" above.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your real DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN
streamlit run app.py
```

You can also skip `.env` entirely and paste credentials into the sidebar at
runtime — they're held only in `st.session_state` for that browser session
and are never written to disk.

## Security notes

- **App-level login** — set `APP_PASSWORD` in `.env` before deploying
  anywhere network-reachable (Docker host, cloud VM, etc.). Without it,
  anyone who reaches the URL can use your already-connected Dhan session
  to place live orders; Dhan's own client ID/token only authenticate the
  *app to Dhan*, not a *person to the app*. `core/auth.py` does a
  timing-safe password comparison (`hmac.compare_digest`) with a small
  escalating delay on repeated failures. Leave it blank for pure
  `localhost`-only use.
- **Never** hardcode `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` in source. Use
  `.env` (git-ignored) or the sidebar's password-masked inputs.
- `.env`, the SQLite journal, and `__pycache__` are excluded via `.gitignore`.
- The sidebar credential fields use `type="password"` so tokens aren't shown
  on screen or in shoulder-surfing scenarios.
- The app defaults to **Paper Trading** on every fresh session — you must
  explicitly flip the toggle and connect valid credentials to arm Live mode.
- Consider rotating your Dhan access token regularly and scoping it to the
  minimum required permissions if/when Dhan supports scoped tokens.

## Trading Mode

- **Paper Trading**: fills are simulated instantly at your entered reference
  LTP, adjusted by a configurable slippage % (worse for you in the trade's
  direction, plus small random jitter) — see `core/paper_trading.py`.
- **Live Trading**: orders are sent to Dhan via `dhanhq`'s `place_order`.
  Every attempt (success or rejection) is logged to the journal.

## Live order lifecycle (Live Trading mode)

- **Real-time fill sync** — on connecting, `core/dhan_client.py::OrderUpdateManager`
  starts Dhan's `OrderUpdate` WebSocket on a background thread (same
  exponential-backoff auto-reconnect pattern as the market feed). Every
  push (TRADED / REJECTED / CANCELLED) updates the journal so the Trade
  Journal tab stays accurate without manual refreshing. A status indicator
  ("🟢 connected" / "🟡 reconnecting") shows in the sidebar once armed.
- **FIFO position netting & realized PnL** — `core/live_fifo_tracker.py`'s
  `LiveFIFOTracker` matches each confirmed fill against existing
  opposite-side lots for that instrument, oldest first, exactly like a
  real position book. A fully-offsetting fill closes the original lot and
  records its realized PnL; a partial offset splits the realized PnL onto
  the old lot while leaving it OPEN for the remainder; an oversized
  opposite fill closes the old lot AND opens a new one in the flipped
  direction. This is wired into the `OrderUpdate` callback in `app.py`, so
  realized PnL on live trades populates the Performance Analysis tab the
  same way paper trades do. If the app restarts mid-session,
  `seed_from_open_positions()` rebuilds in-memory lots from the journal's
  still-OPEN rows.
- **Modify / cancel** — the Execution & Mode tab, in Live mode, lists open
  orders (refresh on demand) with inline quantity/price modification and
  cancellation, calling `DhanClient.modify_order` / `cancel_order`; a
  cancel also updates the journal row immediately.

## Real-time data

Two options are built in:

- **REST polling (wired up by default)** — the Market Overview tab has a
  Watchlist section: add instruments (name, security ID, exchange segment),
  click "🔄 Refresh Live Prices", and `DhanClient.get_ltp_batch` calls
  Dhan's `ticker_data` market-quote method (falling back to `ohlc_data`/
  `quote_data` on older SDK builds) in one batched call, populating
  `st.session_state.ltp_cache`. The Execution tab's order ticket can then
  quick-fill an instrument's security ID, segment, and last live price
  straight from the watchlist. Requires a connected client (sidebar);
  without one, cached/manually-entered prices are used instead.
- **Streaming WebSocket (available, not auto-started)** —
  `core/dhan_client.py::MarketFeedManager` wraps the SDK's `MarketFeed`
  class (`MarketFeed(dhan_context, instruments, version)`, matching its
  documented `run_forever()` once + poll-`get_data()` usage) on a
  background thread with exponential-backoff auto-reconnect (2s → 60s
  cap), for true tick-by-tick data. `segment_to_marketfeed_code()` handles
  translating this app's REST-style segment strings ("NSE_EQ") into the
  SDK's differently-encoded subscription constants for the confirmed
  segments (NSE, BSE, NSE_FNO) — see that function's docstring for which
  mappings are confirmed vs. best-guess. Instantiate it with your
  watchlist at app startup and feed `latest_ticks` into `ltp_cache` if you
  need sub-second updates instead of polling.

## Accuracy audit against the live DhanHQ SDK

An earlier draft of this project guessed at several DhanHQ-py method names
from general REST-API conventions rather than the SDK's actual current
surface (`dhan-oss/DhanHQ-py`, v2.3.0). A follow-up pass checked every
method call against the SDK's own published README and fixed what didn't
match:

- `get_historical_ohlc` was calling nonexistent methods
  (`fetch_historical_charts`/`fetch_intraday_charts`) — corrected to the
  real `intraday_minute_data(...)` / `historical_daily_data(...)`, which
  also require `from_date`/`to_date` (the Execution tab call site was
  fixed to actually pass a 5-day lookback window, since it wasn't before).
- `get_ltp_batch` was calling a nonexistent `ltp_data` — corrected to the
  real minimal-LTP method, `ticker_data`.
- `MarketFeedManager` was built around a nonexistent `dhanhq.marketfeed.DhanFeed`
  class taking `(client_id, access_token, instruments)` — corrected to the
  real `MarketFeed(dhan_context, instruments, version)`, and its
  `run_forever()`-once-then-poll usage pattern (not per-tick blocking).
- `OrderUpdateManager` now reuses one `OrderUpdate` instance across
  reconnect attempts, matching the SDK's documented retry loop, instead of
  constructing a new one per attempt.
- Historical-data response parsing confirmed against the documented
  shape: parallel `open`/`high`/`low`/`close`/`volume`/`start_Time` arrays,
  `start_Time` in Unix epoch seconds (a v2.0 breaking change from the
  original Julian-style epoch).
- Two bugs unrelated to the SDK surface were also caught by this pass:
  a `conn.execute()` that needed to be `conn.executescript()` once the
  schema grew a second `CREATE TABLE` statement, and a recommendation-log
  ordering query that needed an `id DESC` tiebreaker for same-second
  timestamps.

`tests/test_dhan_client.py` now mocks the SDK's real, confirmed method
signatures rather than the earlier guessed ones. Full test suite: 83/83
passing.

## Performance & Super Intelligence

- `analytics/performance.py` is fully vectorized (pandas/numpy) — no
  per-row Python loops — for Sharpe Ratio, Max Drawdown, Win Rate, and
  Profit Factor.
- `analytics/super_intelligence.py` — all four sub-features are genuine,
  auditable logic, not a black box:
  1. **Anomaly detection** — Isolation Forest (or z-score fallback) on
     trade PnL/size, plus rule-derived plain-language insights.
  2. **Technical pattern recognition** — vectorized SMA/RSI/MACD, live on
     any watchlist instrument once connected.
  2b. **ML next-bar direction classifier** — a RandomForest trained
     *in-session* on lagged return/RSI/MACD/volume features, evaluated on
     a chronological holdout split, with feature importances shown. Its
     accuracy is displayed next to a naive majority-class baseline — when
     it doesn't beat the baseline (common on short, noisy series), the UI
     says so explicitly rather than presenting the prediction as a signal.
  2c. **Real-time market sentiment** — fetches live headlines from Google
     News' public RSS feed (no API key) for a chosen query/instrument, and
     scores them with a small transparent finance lexicon (matched
     keywords are shown per headline, so every score is auditable).
  3. **Risk recommendations** — rule-based qualitative checks (drawdown,
     win rate, profit factor, Sharpe, concentration) plus a quantitative
     **Kelly Criterion** position-sizing calculation (reported at
     half-Kelly, capped at 25% of capital, with an explicit
     "insufficient data" / "negative edge" message when the historical
     sample doesn't support a sizing recommendation).

  Mock data generators (`generate_mock_trades`, `generate_mock_ohlc`)
  populate the UI until you have enough live history — the in-app
  "using mock data" captions disappear once you've logged real trades or
  connected a watchlist instrument.

## Extending to a full ML "Super Intelligence"

Swap in your own model at these seams:
1. `si.detect_trade_anomalies` — replace Isolation Forest with a trained
   model of your own.
2. `si.analyze_technical_patterns` / `si.train_next_bar_classifier` —
   already wired to live data via `DhanClient.get_historical_ohlc`; add
   more feature engineering or swap RandomForest for a model of your
   choice (the interface — features in, direction/probability out — stays
   the same).
3. `si.market_sentiment` — currently lexicon-based on free RSS headlines;
   swap in a paid news API or a transformer sentiment model by replacing
   `fetch_headlines`'s source and/or `score_sentiment`'s scoring function.
4. `si.kelly_position_size` / `si.risk_recommendations` — replace the
   rule thresholds and Kelly inputs with a learned position-sizing policy
   fit on your own trade history.

## Before your first live order

See `SMOKE_TEST.md` for a live smoke-test checklist. Everything in this
project has been verified against DhanHQ-py's published docs and against
mocks matching its confirmed signatures, but has not been run against a
real account or live network from this build environment — that first
real-money-adjacent check is on you, once, before trusting it further.

## Disclaimer

This is a technical starting point, not financial advice. Algorithmic
trading carries substantial risk of loss. Test thoroughly in Paper Trading
mode before ever enabling Live Trading, and comply with your broker's and
exchange's applicable rules and regulations.
