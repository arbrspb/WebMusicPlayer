from app.recommendation_history import RecommendationHistory


def _items(*names):
    return [{"path": name, "similarity": 1.0 - index / 100} for index, name in enumerate(names)]


def test_repeated_query_prefers_unseen_candidates():
    history = RecommendationHistory()
    pool = _items("A.mp3", "B.mp3", "C.mp3", "D.mp3")
    assert [row["path"] for row in history.rerank(pool, ["ref.mp3"], 2, now=10)] == ["A.mp3", "B.mp3"]
    assert [row["path"] for row in history.rerank(pool, ["ref.mp3"], 2, now=11)] == ["C.mp3", "D.mp3"]


def test_cooldown_expiry_allows_candidate_again():
    history = RecommendationHistory()
    pool = _items("A.mp3", "B.mp3")
    history.rerank(pool, ["ref.mp3"], 1, now=10)
    result = history.rerank(pool, ["ref.mp3"], 1, now=10 + 3 * 60 * 60)
    assert result[0]["path"] == "A.mp3"


def test_new_reference_softens_pair_history():
    history = RecommendationHistory()
    pool = _items("A.mp3", "B.mp3")
    history.rerank(pool, ["ref-a.mp3"], 1, now=10)
    # A remains a fallback candidate for a new reference and is never blacklisted.
    assert history.rerank([pool[0]], ["ref-b.mp3"], 1, now=11)[0]["path"] == "A.mp3"


def test_small_pool_never_becomes_empty():
    history = RecommendationHistory()
    pool = _items("A.mp3")
    history.rerank(pool, ["ref.mp3"], 1, now=10)
    assert history.rerank(pool, ["ref.mp3"], 5, now=11) == pool


def test_clear_restores_original_ranking():
    history = RecommendationHistory()
    pool = _items("A.mp3", "B.mp3", "C.mp3")
    history.rerank(pool, ["ref.mp3"], 2, now=10)
    history.clear()
    assert [row["path"] for row in history.rerank(pool, ["ref.mp3"], 2, now=11)] == ["A.mp3", "B.mp3"]
