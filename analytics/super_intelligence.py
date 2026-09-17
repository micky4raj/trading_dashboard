"""
analytics/super_intelligence.py
----------------------------------
The "Super Intelligence" tab's engine.

Honesty note (per the build spec): this ships genuine, explainable
statistical/rule-based logic — z-score & Isolation-Forest anomaly
detection, classic technical indicators (SMA/RSI/MACD-based signals),
lexicon-based news-headline sentiment scoring, a per-session RandomForest
next-bar direction classifier, and Kelly-criterion position sizing on top
of the qualitative risk rules. None of this is a proprietary black-box
model or a paid sentiment API — it's transparent, inspectable logic you
can audit line by line. Where real data (broker feed / news) isn't wired
up, mock generators clearly labelled `generate_mock_*` stand in so the UI
is fully demoable.
"""

from __future__ import annotations

import random
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import IsolationForest, RandomForestClassifier
    _SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SKLEARN_AVAILABLE = False


# ---------------------------------------------------------------------------
# 1. Trade insights & anomaly detection
# ---------------------------------------------------------------------------

@dataclass
class Anomaly:
    trade_id: int
    instrument: str
    reason: str
    severity: str          # "medium" | "high"
    pnl: float


def detect_trade_anomalies(trades_df: pd.DataFrame, contamination: float = 0.1) -> List[Anomaly]:
    """
    Flags unusual trades by PnL and position size relative to the trader's
    own history. Uses IsolationForest when scikit-learn is available,
    falling back to a transparent z-score rule otherwise (>|2.5| std devs).
    """
    closed = trades_df[(trades_df.get("status") == "CLOSED") & trades_df.get("pnl").notna()].copy()
    if closed.empty or len(closed) < 5:
        return []

    features = closed[["pnl", "quantity"]].fillna(0.0).to_numpy()

    if _SKLEARN_AVAILABLE:
        model = IsolationForest(contamination=contamination, random_state=42)
        preds = model.fit_predict(features)
        flagged_idx = np.where(preds == -1)[0]
    else:
        z_pnl = (features[:, 0] - features[:, 0].mean()) / (features[:, 0].std() or 1.0)
        flagged_idx = np.where(np.abs(z_pnl) > 2.5)[0]

    anomalies = []
    for i in flagged_idx:
        row = closed.iloc[i]
        reason = "Unusually large loss" if row["pnl"] < 0 else "Unusually large gain"
        severity = "high" if abs(row["pnl"]) > closed["pnl"].abs().quantile(0.9) else "medium"
        anomalies.append(
            Anomaly(
                trade_id=int(row["id"]),
                instrument=str(row["instrument"]),
                reason=f"{reason} relative to your typical trade ({row['pnl']:.2f})",
                severity=severity,
                pnl=float(row["pnl"]),
            )
        )
    return anomalies


def trade_insights(trades_df: pd.DataFrame) -> List[str]:
    """Plain-language, rule-derived observations about trading behaviour."""
    closed = trades_df[(trades_df.get("status") == "CLOSED") & trades_df.get("pnl").notna()].copy()
    insights: List[str] = []
    if closed.empty:
        return ["Not enough closed trades yet to generate insights."]

    by_instrument = closed.groupby("instrument")["pnl"].sum().sort_values()
    if not by_instrument.empty:
        worst, worst_pnl = by_instrument.index[0], by_instrument.iloc[0]
        best, best_pnl = by_instrument.index[-1], by_instrument.iloc[-1]
        if worst_pnl < 0:
            insights.append(f"{worst} is your biggest net drag so far ({worst_pnl:,.2f} PnL).")
        if best_pnl > 0:
            insights.append(f"{best} is your strongest performer ({best_pnl:,.2f} PnL).")

    if "timestamp" in closed.columns:
        closed["hour"] = pd.to_datetime(closed["timestamp"]).dt.hour
        hourly = closed.groupby("hour")["pnl"].mean()
        if not hourly.empty:
            best_hour = hourly.idxmax()
            insights.append(f"Trades opened around {best_hour}:00 have the best average PnL historically.")

    if len(closed) >= 5:
        streak = (closed["pnl"].tail(5) > 0).sum()
        if streak >= 4:
            insights.append("You're on a hot streak — 4+ of your last 5 closed trades were winners.")
        elif streak <= 1:
            insights.append("Recent run of losses — consider reducing size until the trend stabilizes.")

    return insights or ["No strong patterns detected yet — keep logging trades."]


# ---------------------------------------------------------------------------
# 2. Technical pattern recognition (rule-based, on OHLC data)
# ---------------------------------------------------------------------------

def analyze_technical_patterns(ohlc: pd.DataFrame) -> dict:
    """
    Expects columns: timestamp, open, high, low, close, volume.
    Computes SMA20/50 crossover, RSI(14), and a simple MACD signal —
    all vectorized — and returns a human-readable verdict.
    """
    df = ohlc.copy()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    latest = df.iloc[-1]
    verdict = "NEUTRAL"
    if latest["sma20"] > latest["sma50"] and latest["macd"] > latest["macd_signal"]:
        verdict = "BULLISH"
    elif latest["sma20"] < latest["sma50"] and latest["macd"] < latest["macd_signal"]:
        verdict = "BEARISH"

    rsi_note = "overbought" if latest["rsi14"] > 70 else "oversold" if latest["rsi14"] < 30 else "neutral"

    return {
        "verdict": verdict,
        "rsi14": round(float(latest["rsi14"]), 2) if pd.notna(latest["rsi14"]) else None,
        "rsi_note": rsi_note,
        "sma20": round(float(latest["sma20"]), 2) if pd.notna(latest["sma20"]) else None,
        "sma50": round(float(latest["sma50"]), 2) if pd.notna(latest["sma50"]) else None,
        "macd_cross": "bullish" if latest["macd"] > latest["macd_signal"] else "bearish",
        "series": df,
    }


# ---------------------------------------------------------------------------
# 2b. Real-time market sentiment (news headlines + finance lexicon)
# ---------------------------------------------------------------------------

# A deliberately small, transparent finance-specific lexicon rather than a
# trained sentiment model — every headline's score is fully auditable by
# eye. Extend these lists for your own coverage universe as needed.
# A deliberately small, transparent finance-specific lexicon rather than a
# trained sentiment model — every headline's score is fully auditable by
# eye. Each entry is a base form only (substring matching already catches
# "surge"/"surges"/"surged" etc. via one entry — listing all three would
# double-count a single headline). Extend for your own coverage as needed.
_POSITIVE_TERMS = [
    "surg", "rall", "jump", "soar", "beat estimate", "beats estimate", "record high", "record profit",
    "upgrad", "outperform", "bullish", "buyback", "strong growth", "robust growth",
    "raises guidance", "raised guidance", "expansion", "wins order", "wins contract", "profit jump",
    "all-time high", "gain", "rebound", "positive outlook", "strong demand",
]
_NEGATIVE_TERMS = [
    "plung", "crash", "slump", "miss estimate", "downgrad", "underperform", "bearish",
    "probe", "fraud", "investigation", "lawsuit", "layoff", "job cut", "weak demand", "guidance cut",
    "cuts guidance", "profit fall", "loss widen", "default", "bankrupt", "resign",
    "scandal", "recall", "sell-off", "selloff", "declin", "tumbl",
]


@dataclass
class Headline:
    title: str
    link: str
    published: str
    matched_positive: List[str]
    matched_negative: List[str]
    score: int


def fetch_headlines(query: str, max_items: int = 15, timeout_seconds: float = 5.0) -> List[dict]:
    """
    Pulls recent headlines for `query` from Google News' public RSS feed —
    no API key required. Returns [] (never raises) on any network issue so
    the UI can degrade gracefully when the host has no internet access.
    Runs at app runtime on the user's own machine/network, not inside this
    build sandbox.
    """
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (trading-dashboard)"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if title:
                items.append({"title": title, "link": link, "published": pub_date})
        return items
    except (urllib.error.URLError, socket.timeout, ET.ParseError, ValueError):
        return []


def score_sentiment(headlines: List[dict]) -> dict:
    """
    Lexicon match against each headline title, then aggregates into an
    overall score in [-1, +1] and a Bullish/Bearish/Neutral label. Fully
    transparent — every matched keyword is returned alongside its headline
    so a trader can see exactly why a score was assigned, not just trust it.
    """
    scored: List[Headline] = []
    total_score = 0

    for h in headlines:
        title_lower = h["title"].lower()
        pos_hits = [term for term in _POSITIVE_TERMS if term in title_lower]
        neg_hits = [term for term in _NEGATIVE_TERMS if term in title_lower]
        headline_score = len(pos_hits) - len(neg_hits)
        total_score += headline_score
        scored.append(
            Headline(
                title=h["title"], link=h.get("link", ""), published=h.get("published", ""),
                matched_positive=pos_hits, matched_negative=neg_hits, score=headline_score,
            )
        )

    if not scored:
        return {"label": "NO DATA", "normalized_score": 0.0, "headlines": [], "positive_count": 0, "negative_count": 0}

    normalized = max(-1.0, min(1.0, total_score / max(len(scored), 1) / 2.0))
    label = "BULLISH" if normalized > 0.15 else "BEARISH" if normalized < -0.15 else "NEUTRAL"
    positive_count = sum(1 for h in scored if h.score > 0)
    negative_count = sum(1 for h in scored if h.score < 0)

    return {
        "label": label,
        "normalized_score": round(normalized, 3),
        "headlines": scored,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "total_headlines": len(scored),
    }


def market_sentiment(query: str, max_items: int = 15) -> dict:
    """Convenience wrapper: fetch + score in one call."""
    headlines = fetch_headlines(query, max_items=max_items)
    return score_sentiment(headlines)


# ---------------------------------------------------------------------------
# 2c. Trained ML next-bar direction classifier (per-session RandomForest)
# ---------------------------------------------------------------------------

def _build_ml_features(ohlc: pd.DataFrame) -> pd.DataFrame:
    df = ohlc.copy()
    df["return_1"] = df["close"].pct_change(1)
    df["return_2"] = df["close"].pct_change(2)
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma_cross"] = (df["sma20"] - df["sma50"]) / df["close"]

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_diff"] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    df["volume_change"] = df["volume"].pct_change(1)

    df["target_up"] = (df["close"].shift(-1) > df["close"]).astype(int)
    return df


FEATURE_COLUMNS = ["return_1", "return_2", "sma_cross", "rsi14", "macd_diff", "volume_change"]


def train_next_bar_classifier(ohlc: pd.DataFrame, test_fraction: float = 0.2) -> Optional[dict]:
    """
    Trains a small RandomForestClassifier, IN-SESSION, to predict whether
    the next bar's close will be higher than the current one, from lagged
    technical features. This is a genuine trained model — not a canned
    prediction — but it is a small demo classifier on noisy short-horizon
    data, not a validated trading signal. Report the holdout accuracy
    alongside the prediction so its (likely modest) skill is visible, and
    treat it as one more input among many, never as a standalone signal.

    Uses a chronological (not random) train/test split, since shuffling
    time-series bars would leak future information into training.
    Returns None if scikit-learn isn't installed or there isn't enough
    history to fit + evaluate.
    """
    if not _SKLEARN_AVAILABLE:
        return None

    df = _build_ml_features(ohlc).dropna(subset=FEATURE_COLUMNS)
    labeled = df.iloc[:-1]  # last row has no known next-bar outcome yet
    if len(labeled) < 40:
        return None

    split_idx = int(len(labeled) * (1 - test_fraction))
    train_df, test_df = labeled.iloc[:split_idx], labeled.iloc[split_idx:]
    if len(test_df) < 5 or train_df["target_up"].nunique() < 2:
        return None

    model = RandomForestClassifier(n_estimators=200, max_depth=4, random_state=42, min_samples_leaf=5)
    model.fit(train_df[FEATURE_COLUMNS], train_df["target_up"])

    test_accuracy = float(model.score(test_df[FEATURE_COLUMNS], test_df["target_up"]))

    latest_features = df.iloc[[-1]][FEATURE_COLUMNS]
    prob_up = float(model.predict_proba(latest_features)[0][1])

    importances = dict(zip(FEATURE_COLUMNS, model.feature_importances_.round(3).tolist()))

    return {
        "holdout_accuracy": round(test_accuracy, 3),
        "holdout_size": len(test_df),
        "probability_next_bar_up": round(prob_up, 3),
        "predicted_direction": "UP" if prob_up > 0.5 else "DOWN",
        "feature_importances": importances,
        "baseline_accuracy": round(float(max(train_df["target_up"].mean(), 1 - train_df["target_up"].mean())), 3),
    }


# ---------------------------------------------------------------------------
# 3. Dynamic risk-adjustment recommendations (rule-based + Kelly sizing)
# ---------------------------------------------------------------------------

def risk_recommendations(perf_summary, open_positions_count: int, capital: float) -> List[str]:
    """
    Simple, explainable heuristics — NOT a black box. Tune thresholds to
    your own risk appetite.
    """
    recs: List[str] = []

    if perf_summary.max_drawdown_pct <= -15:
        recs.append(
            f"Drawdown is at {perf_summary.max_drawdown_pct:.1f}% — consider halving position "
            "size until equity recovers above the prior high."
        )
    elif perf_summary.max_drawdown_pct <= -8:
        recs.append(f"Drawdown at {perf_summary.max_drawdown_pct:.1f}% — trade cautiously, tighten stops.")

    if perf_summary.win_rate and perf_summary.win_rate < 35 and perf_summary.closed_trades >= 10:
        recs.append("Win rate is below 35% over a meaningful sample — review entry criteria before scaling up.")

    if perf_summary.profit_factor and 0 < perf_summary.profit_factor < 1.0:
        recs.append("Profit factor is under 1.0 (losses outweigh gains) — reduce size and reassess strategy.")

    if open_positions_count >= 8:
        recs.append(f"{open_positions_count} concurrent open positions — concentration/margin risk is elevated.")

    if perf_summary.sharpe_ratio and perf_summary.sharpe_ratio < 0:
        recs.append("Negative Sharpe ratio — returns aren't compensating for the risk taken; consider a pause.")

    if not recs:
        recs.append("No elevated risk signals detected — current sizing looks consistent with recent performance.")

    return recs


def kelly_position_size(perf_summary, capital: float) -> dict:
    """
    Classic Kelly Criterion: f* = W - (1-W)/R
      W = historical win rate (as a fraction)
      R = payoff ratio = average win / |average loss|

    Full Kelly is aggressive and assumes the historical win rate/payoff
    ratio hold going forward, which is a strong assumption on a small
    sample — so this reports HALF-Kelly as the practical recommendation
    (a standard haircut used to reduce variance from estimation error),
    and clamps the result to [0%, 25%] of capital as a sanity ceiling.
    Returns a "not enough data" / "edge is negative" message instead of a
    number when the inputs don't support a meaningful estimate.
    """
    if perf_summary.closed_trades < 10:
        return {"status": "insufficient_data", "message": "Need at least 10 closed trades for a meaningful Kelly estimate."}

    win_rate = (perf_summary.win_rate or 0.0) / 100.0
    avg_win = perf_summary.avg_win or 0.0
    avg_loss = abs(perf_summary.avg_loss or 0.0)

    if avg_loss == 0 or avg_win == 0:
        return {"status": "insufficient_data", "message": "Need both winning and losing trades to compute a payoff ratio."}

    payoff_ratio = avg_win / avg_loss
    kelly_fraction = win_rate - (1 - win_rate) / payoff_ratio

    if kelly_fraction <= 0:
        return {
            "status": "negative_edge",
            "message": (
                f"Kelly fraction is negative ({kelly_fraction:.1%}) — historical win rate/payoff ratio "
                "don't support a positive-expectancy position size. Consider paper trading until the edge turns positive."
            ),
            "kelly_fraction": round(kelly_fraction, 4),
            "payoff_ratio": round(payoff_ratio, 2),
        }

    half_kelly = max(0.0, min(kelly_fraction / 2.0, 0.25))  # half-Kelly, capped at 25% of capital
    return {
        "status": "ok",
        "kelly_fraction": round(kelly_fraction, 4),
        "half_kelly_fraction": round(half_kelly, 4),
        "payoff_ratio": round(payoff_ratio, 2),
        "recommended_capital_allocation": round(half_kelly * capital, 2),
        "message": (
            f"Full Kelly suggests {kelly_fraction:.1%} of capital per position; using half-Kelly "
            f"({half_kelly:.1%} ≈ ₹{half_kelly * capital:,.0f}) as a more conservative, estimation-error-tolerant sizing."
        ),
    }


# ---------------------------------------------------------------------------
# 4. Human-in-the-loop composite recommendation
# ---------------------------------------------------------------------------
#
# This is the "Human/Super Intelligence" collaboration layer: it combines
# the three independent signals above (technical pattern, ML classifier,
# news sentiment) into ONE advisory BUY/SELL/HOLD call with a confidence
# level and a plain-language rationale for each contributing signal. It
# NEVER places an order itself — `generate_recommendation` returns data, not
# an action. The UI (app.py) always requires an explicit human click
# ("Approve" or "Dismiss") before anything reaches the order manager, and
# logs that decision to `TradeJournal.recommendations` either way, so there
# is always an audit trail proving a human made the actual trading call.

@dataclass
class Recommendation:
    action: str          # BUY / SELL / HOLD
    confidence: str       # LOW / MEDIUM / HIGH
    score: float           # -1 (max bearish) .. +1 (max bullish)
    rationale: List[str]


def generate_recommendation(
    technical: Optional[dict] = None,
    ml_result: Optional[dict] = None,
    sentiment: Optional[dict] = None,
) -> Recommendation:
    """
    Simple, transparent weighted vote across whichever signals are
    available (any subset — this degrades gracefully if e.g. sentiment
    wasn't fetched yet). Each signal contributes a vote in [-1, +1]:
      - technical: BULLISH -> +1, BEARISH -> -1, NEUTRAL -> 0
      - ML: scaled by how far probability_next_bar_up is from 0.5, and
        zeroed out entirely if holdout accuracy doesn't beat baseline
        (an unskilled model shouldn't move the vote)
      - sentiment: BULLISH -> +1, BEARISH -> -1, NEUTRAL/NO DATA -> 0

    Confidence reflects AGREEMENT across signals, not just magnitude: all
    available signals pointing the same way is HIGH; a mix is LOW/MEDIUM.
    No single signal is trusted enough to command HIGH confidence alone.
    """
    votes: List[float] = []
    rationale: List[str] = []

    if technical is not None:
        tech_vote = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}.get(technical.get("verdict"), 0.0)
        votes.append(tech_vote)
        rationale.append(
            f"Technical pattern: {technical.get('verdict')} "
            f"(RSI {technical.get('rsi14')}, MACD cross {technical.get('macd_cross')})"
        )

    if ml_result is not None:
        beats_baseline = ml_result.get("holdout_accuracy", 0) > ml_result.get("baseline_accuracy", 1)
        prob_up = ml_result.get("probability_next_bar_up", 0.5)
        ml_vote = (prob_up - 0.5) * 2.0 if beats_baseline else 0.0
        votes.append(ml_vote)
        skill_note = "" if beats_baseline else " (ignored — didn't beat naive baseline on holdout)"
        rationale.append(
            f"ML classifier: {ml_result.get('predicted_direction')} "
            f"at {prob_up:.0%} probability{skill_note}"
        )

    if sentiment is not None and sentiment.get("label") != "NO DATA":
        sent_vote = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}.get(sentiment.get("label"), 0.0)
        votes.append(sent_vote)
        rationale.append(
            f"News sentiment: {sentiment.get('label')} "
            f"({sentiment.get('positive_count', 0)} positive / {sentiment.get('negative_count', 0)} negative headlines)"
        )

    if not votes:
        return Recommendation(action="HOLD", confidence="LOW", score=0.0, rationale=["No signals available yet."])

    composite_score = sum(votes) / len(votes)
    action = "BUY" if composite_score > 0.2 else "SELL" if composite_score < -0.2 else "HOLD"

    # Agreement: what fraction of available votes point the same direction as the composite?
    same_direction = sum(1 for v in votes if (v > 0) == (composite_score > 0) and v != 0)
    agreement_ratio = same_direction / len(votes) if votes else 0.0
    if len(votes) >= 2 and agreement_ratio == 1.0 and abs(composite_score) > 0.4:
        confidence = "HIGH"
    elif abs(composite_score) > 0.2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    rationale.append(f"Composite score: {composite_score:+.2f} across {len(votes)} signal(s) -> {action} ({confidence} confidence)")
    return Recommendation(action=action, confidence=confidence, score=round(composite_score, 3), rationale=rationale)


# ---------------------------------------------------------------------------
# Mock data generators (used only when live history / feeds are unavailable)
# ---------------------------------------------------------------------------

def generate_mock_trades(n: int = 40, seed: int = 7) -> pd.DataFrame:
    rng = random.Random(seed)
    instruments = ["RELIANCE", "NIFTY24DECFUT", "NIFTY24DEC22000CE", "TCS", "HDFCBANK", "BANKNIFTY24DECFUT"]
    rows = []
    now = datetime.now()
    for i in range(n):
        pnl = rng.gauss(500, 2500)
        rows.append(
            {
                "id": i + 1,
                "timestamp": now - timedelta(hours=rng.randint(1, 800)),
                "mode": rng.choice(["Paper Trading", "Live Trading"]),
                "instrument": rng.choice(instruments),
                "side": rng.choice(["BUY", "SELL"]),
                "quantity": rng.choice([1, 5, 10, 25, 50]),
                "entry_price": round(rng.uniform(100, 2500), 2),
                "exit_price": round(rng.uniform(100, 2500), 2),
                "status": "CLOSED",
                "pnl": round(pnl, 2),
                "strategy_tag": rng.choice(["breakout", "mean_reversion", "manual", "momentum"]),
            }
        )
    return pd.DataFrame(rows)


def generate_mock_ohlc(periods: int = 120, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 100 + np.cumsum(rng.normal(0, 1.2, periods))
    price = np.maximum(price, 10)
    high = price + rng.uniform(0.2, 1.5, periods)
    low = price - rng.uniform(0.2, 1.5, periods)
    open_ = price + rng.normal(0, 0.5, periods)
    volume = rng.integers(10_000, 500_000, periods)
    timestamps = pd.date_range(end=datetime.now(), periods=periods, freq="5min")
    return pd.DataFrame(
        {"timestamp": timestamps, "open": open_, "high": high, "low": low, "close": price, "volume": volume}
    )
