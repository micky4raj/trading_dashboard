# Live Smoke Test Checklist

Everything in this project has been verified against DhanHQ-py's published
documentation and against mocks matching its confirmed method signatures —
but it has **not** been run against a real Dhan account or a live network
connection (this build environment has neither). Before trusting the app
with real money, work through this checklist somewhere that has both.

## 0. Setup
- [ ] `pip install -r requirements.txt` succeeds and installs `dhanhq>=2.0.2`
- [ ] `python -c "import dhanhq; print(dhanhq.__version__)"` prints a version ≥ 2.0.0
- [ ] `.env` has real `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` (a **paper/sandbox**
      account if Dhan offers one for your use case — check current Dhan docs)
- [ ] `pytest` passes locally (83 tests, no network needed for these)

## 1. Connection
- [ ] Sidebar → "Connect to Dhan" succeeds without error
- [ ] `DhanClient.get_fund_limits()` returns real numbers (Market Overview
      tab's "Available Margin" metric)
- [ ] Sidebar's order-update feed indicator turns 🟢 within a few seconds

## 2. Market data (the methods fixed in the accuracy audit — verify these first)
- [ ] Watchlist → add a known-liquid equity (e.g. RELIANCE, security_id 1333,
      NSE_EQ) → "🔄 Refresh Live Prices" returns a real, sane LTP
      (validates `get_ltp_batch` → `ticker_data`)
- [ ] Super Intelligence tab → pick that same instrument → "📥 Fetch live
      historical candles" returns real candles, not the "unavailable"
      warning (validates `get_historical_ohlc` → `intraday_minute_data`)
- [ ] Try the same for an index (NIFTY 50, security_id 13, IDX_I) — if this
      one fails with a JSON-parse-style error, that's the known SDK issue
      (dhan-oss/DhanHQ-py#113), not a bug in this app; LTP should still work
      even if historical candles don't

## 3. Paper mode (safe — no real orders, do this before Live)
- [ ] Execution tab → place a paper BUY, then a paper SELL for the same
      instrument → Trade Journal shows both, second row CLOSED with a PnL
      that matches (exit − entry) × qty, adjusted for slippage
- [ ] Performance Analysis tab reflects the paper trade(s) once you have 3+
      closed trades (until then it shows illustrative mock data — check the
      warning banner disappears at that point)

## 4. Live mode — start with the smallest possible order
- [ ] Flip to Live Trading in the sidebar (confirms the red "LIVE mode is
      armed" banner appears)
- [ ] Place ONE minimum-quantity order in an instrument/market you're
      comfortable risking
- [ ] Confirm the order appears in Dhan's own app/web terminal too — not
      just this dashboard, as a sanity cross-check
- [ ] Within a few seconds, confirm the Trade Journal row updates from
      PENDING/OPEN to reflect the fill, and the sidebar's order-update feed
      shows activity (validates `OrderUpdateManager` + `LiveFIFOTracker`)
- [ ] Place an opposite-side order to close that position → confirm the
      original journal row flips to CLOSED with a realized PnL that matches
      what Dhan itself reports (validates FIFO netting end-to-end)
- [ ] Execution tab → Modify/Cancel panel → try modifying a pending order's
      price, then cancel a different pending order → confirm both reflect
      correctly in Dhan's own terminal

## 5. Sentiment (needs real internet, separate from Dhan)
- [ ] Super Intelligence tab → "📰 Fetch & score headlines" for a real
      query → confirm headlines come back (validates the Google News RSS
      fetch actually resolves from wherever you're hosting this)

## If something in section 2 or 4 fails
That's exactly the kind of thing this checklist exists to catch before
trusting the app further — the SDK's own surface has changed at least once
before (the v2.0 breaking changes this project's accuracy audit was built
around), and could again. Re-run the relevant `web_search`/docs check
against `dhan-oss/DhanHQ-py`'s current README, fix the mismatch the same
way the accuracy-audit section of README.md documents, and add a test
against the corrected mock before trusting it live again.
