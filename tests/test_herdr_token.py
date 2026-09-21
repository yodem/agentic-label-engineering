from ale.herdr_token import token_text


def test_token_shape():
    text = token_text("T3", {"labels": {"role": "backend", "model_tier": "standard"}},
                      {"state": "working", "attempt": 2})
    assert text == "T3 backend\u00b7standard working 2/3"


def test_token_marks_stuck_and_is_short():
    text = token_text("T3", {"labels": {"role": "backend", "model_tier": "standard"}},
                      {"state": "working", "attempt": 2, "breaches_seen": [["stuck", 2]]})
    assert "\u26a0stuck" in text and len(text) <= 48


def test_token_truncates_role():
    text = token_text("T3", {"labels": {"role": "a" * 100, "model_tier": "standard"}},
                      {"state": "working", "attempt": 1})
    assert len(text) <= 48 and "\u00b7standard working 1/3" in text


def test_token_accepts_flat_labels():
    assert token_text("T1", {"role": "docs", "model_tier": "cheap"}, {"state": "ready"}).startswith("T1 docs")
