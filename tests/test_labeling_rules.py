import pytest

from ale.labeling.rules import RuleError, rule_votes


def test_path_prefix_rule_fires_on_allowed_paths(roster, label_t02):
    votes = rule_votes(label_t02, "", roster)
    fired = [v for v in votes if v["value"] is not None]
    assert fired == [{"field": "role", "value": "docs", "by": "rule:0", "confidence": 1.0, "detail": {"path_prefix": "docs/"}}]


def test_keyword_rule_fires_on_title_or_text(roster, label_t01):
    assert all(v["value"] is None for v in rule_votes(label_t01, "", roster) if v["field"] == "role")
    votes = rule_votes(label_t01, "Add pytest coverage for the refresh path", roster)
    assert [v["value"] for v in votes if v["by"] == "rule:1"] == ["test"]


def test_every_rule_returns_a_vote_even_when_abstaining(roster, label_t01):
    assert len(rule_votes(label_t01, "", roster)) == len(roster["rules"])


def test_both_conditions_must_hold(roster, label_t01):
    roster["rules"] = [{"field": "role", "when": {"path_prefix": "src/auth", "keyword": "zzz"}, "value": "backend"}]
    assert rule_votes(label_t01, "", roster)[0]["value"] is None
    roster["rules"][0]["when"]["keyword"] = "(?i)refresh"
    assert rule_votes(label_t01, "", roster)[0]["value"] == "backend"


def test_rule_value_outside_vocab_raises(roster, label_t01):
    roster["rules"] = [{"field": "role", "when": {"keyword": "x"}, "value": "wizard"}]
    with pytest.raises(RuleError):
        rule_votes(label_t01, "", roster)


def test_bad_regex_raises(roster, label_t01):
    roster["rules"] = [{"field": "role", "when": {"keyword": "("}, "value": "docs"}]
    with pytest.raises(RuleError):
        rule_votes(label_t01, "", roster)


def test_no_rules_key_means_no_votes(roster, label_t01):
    del roster["rules"]
    assert rule_votes(label_t01, "", roster) == []
