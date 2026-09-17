from database import RecommendationRecord


def test_log_recommendation_defaults_to_pending(tmp_journal):
    rid = tmp_journal.log_recommendation(
        RecommendationRecord(instrument="RELIANCE", action="BUY", confidence="MEDIUM", score=0.42, rationale="test")
    )
    df = tmp_journal.fetch_recommendations()
    row = df[df["id"] == rid].iloc[0]
    assert row["human_decision"] == "PENDING"
    assert row["action"] == "BUY"
    assert row["decided_at"] is None or str(row["decided_at"]) == "nan" or row["decided_at"] == ""


def test_decide_recommendation_approved_links_trade(tmp_journal):
    rid = tmp_journal.log_recommendation(
        RecommendationRecord(instrument="TCS", action="SELL", confidence="HIGH", score=-0.6, rationale="test")
    )
    tmp_journal.decide_recommendation(rid, "APPROVED", resulting_trade_id=7)

    df = tmp_journal.fetch_recommendations()
    row = df[df["id"] == rid].iloc[0]
    assert row["human_decision"] == "APPROVED"
    assert row["resulting_trade_id"] == 7
    assert row["decided_at"] is not None


def test_decide_recommendation_dismissed_has_no_trade(tmp_journal):
    rid = tmp_journal.log_recommendation(
        RecommendationRecord(instrument="INFY", action="BUY", confidence="LOW", score=0.1, rationale="test")
    )
    tmp_journal.decide_recommendation(rid, "DISMISSED")

    df = tmp_journal.fetch_recommendations()
    row = df[df["id"] == rid].iloc[0]
    assert row["human_decision"] == "DISMISSED"
    assert row["resulting_trade_id"] is None or str(row["resulting_trade_id"]) == "nan"


def test_fetch_recommendations_orders_newest_first(tmp_journal):
    first_id = tmp_journal.log_recommendation(
        RecommendationRecord(instrument="A", action="HOLD", confidence="LOW", score=0.0, rationale="first")
    )
    second_id = tmp_journal.log_recommendation(
        RecommendationRecord(instrument="B", action="BUY", confidence="MEDIUM", score=0.3, rationale="second")
    )
    df = tmp_journal.fetch_recommendations()
    assert len(df) == 2
    # newest first
    assert df.iloc[0]["id"] == second_id
    assert df.iloc[1]["id"] == first_id


def test_trades_table_unaffected_by_recommendations_table(tmp_journal):
    from database import TradeRecord
    tid = tmp_journal.log_trade(TradeRecord(instrument="X", side="BUY", quantity=1, mode="Paper Trading"))
    rid = tmp_journal.log_recommendation(
        RecommendationRecord(instrument="X", action="BUY", confidence="LOW", score=0.1, rationale="test")
    )
    assert tid == 1
    assert rid == 1  # independent auto-increment sequences, both tables start fresh
    assert len(tmp_journal.fetch_all()) == 1
    assert len(tmp_journal.fetch_recommendations()) == 1
