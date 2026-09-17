from analytics import super_intelligence as si


def test_all_bullish_signals_produce_high_confidence_buy():
    technical = {"verdict": "BULLISH", "rsi14": 60, "macd_cross": "bullish"}
    ml_result = {"predicted_direction": "UP", "probability_next_bar_up": 0.75, "holdout_accuracy": 0.6, "baseline_accuracy": 0.5}
    sentiment = {"label": "BULLISH", "positive_count": 5, "negative_count": 1}

    rec = si.generate_recommendation(technical, ml_result, sentiment)
    assert rec.action == "BUY"
    assert rec.confidence == "HIGH"
    assert rec.score > 0


def test_all_bearish_signals_produce_high_confidence_sell():
    technical = {"verdict": "BEARISH", "rsi14": 25, "macd_cross": "bearish"}
    ml_result = {"predicted_direction": "DOWN", "probability_next_bar_up": 0.2, "holdout_accuracy": 0.65, "baseline_accuracy": 0.5}
    sentiment = {"label": "BEARISH", "positive_count": 0, "negative_count": 4}

    rec = si.generate_recommendation(technical, ml_result, sentiment)
    assert rec.action == "SELL"
    assert rec.confidence == "HIGH"
    assert rec.score < 0


def test_conflicting_signals_produce_low_confidence_hold():
    technical = {"verdict": "BULLISH", "rsi14": 60, "macd_cross": "bullish"}
    sentiment = {"label": "BEARISH", "positive_count": 1, "negative_count": 5}

    rec = si.generate_recommendation(technical, None, sentiment)
    assert rec.action == "HOLD"
    assert rec.confidence == "LOW"


def test_unskilled_ml_model_is_excluded_from_the_vote():
    # holdout_accuracy below baseline_accuracy -> should be zeroed, not trusted
    ml_result = {"predicted_direction": "UP", "probability_next_bar_up": 0.95, "holdout_accuracy": 0.4, "baseline_accuracy": 0.55}
    rec = si.generate_recommendation(None, ml_result, None)
    assert rec.action == "HOLD"
    assert "ignored" in rec.rationale[0].lower()


def test_no_signals_returns_hold_low_confidence():
    rec = si.generate_recommendation()
    assert rec.action == "HOLD"
    assert rec.confidence == "LOW"
    assert rec.score == 0.0


def test_sentiment_no_data_is_excluded_from_vote():
    technical = {"verdict": "BULLISH", "rsi14": 60, "macd_cross": "bullish"}
    sentiment_no_data = {"label": "NO DATA", "normalized_score": 0.0, "headlines": []}
    rec = si.generate_recommendation(technical, None, sentiment_no_data)
    # Only the technical signal should count -> single-vote BUY territory, not HIGH (needs 2+ agreeing)
    assert rec.action == "BUY"
    assert rec.confidence != "HIGH"


def test_rationale_always_includes_composite_summary_line():
    technical = {"verdict": "NEUTRAL", "rsi14": 50, "macd_cross": "bullish"}
    rec = si.generate_recommendation(technical, None, None)
    assert any("composite score" in line.lower() for line in rec.rationale)
