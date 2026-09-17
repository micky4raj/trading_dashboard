from analytics import super_intelligence as si


# ---------------------------------------------------------------------------
# Sentiment scoring (pure logic — no network needed)
# ---------------------------------------------------------------------------

def test_score_sentiment_bullish_headline():
    headlines = [{"title": "Reliance shares surge after profit beats estimates", "link": "", "published": ""}]
    result = si.score_sentiment(headlines)
    assert result["label"] == "BULLISH"
    assert result["positive_count"] == 1
    assert result["negative_count"] == 0


def test_score_sentiment_bearish_headline():
    headlines = [{"title": "TCS stock plunges after weak demand and guidance cut", "link": "", "published": ""}]
    result = si.score_sentiment(headlines)
    assert result["label"] == "BEARISH"
    assert result["negative_count"] == 1


def test_score_sentiment_neutral_headline():
    headlines = [{"title": "Market ends flat ahead of Fed decision", "link": "", "published": ""}]
    result = si.score_sentiment(headlines)
    assert result["label"] == "NEUTRAL"


def test_score_sentiment_no_headlines_returns_no_data():
    result = si.score_sentiment([])
    assert result["label"] == "NO DATA"
    assert result["headlines"] == []


def test_score_sentiment_does_not_double_count_stem_and_conjugation():
    # "plunges" should only match the "plung" stem once, not twice.
    headlines = [{"title": "Stock plunges sharply", "link": "", "published": ""}]
    result = si.score_sentiment(headlines)
    assert result["headlines"][0].score == -1


def test_fetch_headlines_returns_empty_list_never_raises():
    # No real network access in this sandbox — must degrade gracefully, not raise.
    result = si.fetch_headlines("some query", max_items=3, timeout_seconds=1.0)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ML next-bar direction classifier
# ---------------------------------------------------------------------------

def test_train_next_bar_classifier_returns_expected_keys():
    ohlc = si.generate_mock_ohlc(periods=200)
    result = si.train_next_bar_classifier(ohlc)
    assert result is not None
    for key in ("holdout_accuracy", "probability_next_bar_up", "predicted_direction", "feature_importances", "baseline_accuracy"):
        assert key in result
    assert result["predicted_direction"] in {"UP", "DOWN"}
    assert 0.0 <= result["probability_next_bar_up"] <= 1.0


def test_train_next_bar_classifier_insufficient_data_returns_none():
    ohlc = si.generate_mock_ohlc(periods=15)
    assert si.train_next_bar_classifier(ohlc) is None


# ---------------------------------------------------------------------------
# Kelly criterion sizing
# ---------------------------------------------------------------------------

class _FakeSummaryOk:
    closed_trades = 20
    win_rate = 60.0
    avg_win = 200.0
    avg_loss = -100.0


class _FakeSummaryInsufficient:
    closed_trades = 3
    win_rate = 60.0
    avg_win = 200.0
    avg_loss = -100.0


class _FakeSummaryNegativeEdge:
    closed_trades = 20
    win_rate = 20.0
    avg_win = 100.0
    avg_loss = -500.0


def test_kelly_position_size_healthy_edge():
    result = si.kelly_position_size(_FakeSummaryOk(), capital=1_000_000)
    assert result["status"] == "ok"
    assert result["kelly_fraction"] > 0
    assert result["half_kelly_fraction"] == round(result["kelly_fraction"] / 2, 4)
    assert result["half_kelly_fraction"] <= 0.25


def test_kelly_position_size_insufficient_data():
    result = si.kelly_position_size(_FakeSummaryInsufficient(), capital=1_000_000)
    assert result["status"] == "insufficient_data"


def test_kelly_position_size_negative_edge():
    result = si.kelly_position_size(_FakeSummaryNegativeEdge(), capital=1_000_000)
    assert result["status"] == "negative_edge"
    assert result["kelly_fraction"] < 0
