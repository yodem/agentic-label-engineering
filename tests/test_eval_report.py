import json

from ale.cli import main
from ale.evalharness import report


def stats():
    return {
        "roster": {"promotion": {"min_cases": 2, "tolerance": 0.03, "min_coverage": 0.5}},
        "fields": ["role"],
        "kinds": ["short", "full"],
        "corpus": [
            {"id": "C1", "kind": "short"},
            {"id": "C2", "kind": "short"},
            {"id": "C3", "kind": "full"},
        ],
        "gold": {"gold": {"role": {"C1": "backend", "C2": "docs", "C3": "test"}},
                 "basis": {"role": {"C1": "human", "C2": "agree", "C3": "human"}},
                 "kappa": {"role": 0.5}},
        "judge": [
            {"id": "C1", "field": "role", "perm": 0, "choice": "backend", "confidence": 0.9,
             "latency_ms": 10, "error": None},
            {"id": "C2", "field": "role", "perm": 0, "choice": "frontend", "confidence": 0.8,
             "latency_ms": 30, "error": None},
            {"id": "C3", "field": "role", "perm": 0, "choice": None, "confidence": None,
             "latency_ms": None, "error": "timeout"},
            {"id": "C1", "field": "role", "perm": 1, "choice": "docs", "confidence": 0.6,
             "latency_ms": 20, "error": None},
        ],
        "labeler_a": [{"id": "C1", "role": "backend"}, {"id": "C3", "role": "docs"}],
        "labeler_b": [{"id": "C1", "role": "backend"}, {"id": "C3", "role": "test"}],
        "arms": {"rule": [{"id": "C1", "role": "backend"}, {"id": "C3", "role": "test"}]},
    }


def test_report_states_tolerance_and_bar_before_results():
    text = report.render(stats())

    assert text.index("Tolerance: 0.03") < text.index("## Results")
    assert text.index("Promotion bar") < text.index("## Results")


def test_report_prints_below_bar_when_n_under_min_cases():
    text = report.render(stats())

    assert "BELOW BAR role full" in text
    assert "n 1 < min_cases 2" in text


def test_report_renders_none_metrics_as_na():
    text = report.render({"roster": {"promotion": {"min_cases": 1, "tolerance": 0.03, "min_coverage": 0.5}},
                          "fields": ["role"], "kinds": ["short"], "corpus": [], "gold": {"gold": {"role": {}}},
                          "judge": [], "labeler_a": [], "labeler_b": [], "arms": {}})

    assert "n/a" in text


def test_report_never_raises_on_empty_inputs():
    text = report.render({})

    assert "Evaluation report" in text
    assert "n/a" in text


def test_report_contains_comparison_arm_accuracy_and_kappa():
    text = report.render(stats())

    assert "rule accuracy" in text
    assert "rule kappa" in text


def test_report_uses_goldset_kappa_and_agreement():
    text = report.render(stats())

    assert "role: kappa 0.500, agreement n/a" in text


def test_report_scores_full_gold_and_human_basis_subset_for_judge_and_arms():
    text = report.render(stats())

    assert "judge full gold accuracy: 0.333" in text
    assert "judge human-basis subset accuracy: 0.500" in text
    assert "rule full gold accuracy: 1.000" in text
    assert "rule human-basis subset accuracy: 1.000" in text


def test_report_scores_labelers_only_on_human_basis_subset():
    text = report.render(stats())

    assert "labeler A human-basis subset accuracy: 0.500" in text
    assert "labeler B human-basis subset accuracy: 1.000" in text
    assert "informational: the human subset is the A/B disagreement set" in text
    assert "labeler A full gold" not in text


def test_report_incumbent_bar_compares_full_gold_accuracy_minus_tolerance():
    s = stats()
    s["incumbent_arm"] = "rule"
    text = report.render(s)

    assert "Promotion bar" in text
    assert text.index("Promotion bar") < text.index("## Results")
    assert "BELOW BAR role full gold: judge 0.333 < rule 1.000 - tolerance 0.030" in text


def test_report_without_incumbent_prints_below_bar_reason():
    text = report.render(stats())

    assert "BELOW BAR role full gold: no incumbent arm" in text


def test_report_includes_confusion_errors_latency_sensitivity_and_interlabeler():
    text = report.render(stats())

    assert "docs -> frontend: 1" in text
    assert "timeout: 1" in text
    assert "Latency p50" in text and "Latency p95" in text
    assert "Position sensitivity" in text
    assert "Inter-labeler kappa" in text


def test_cli_report_writes_markdown_that_names_every_field(tmp_path, roster):
    corpus = tmp_path / "corpus.jsonl"
    gold = tmp_path / "gold.json"
    judge = tmp_path / "judge.jsonl"
    labeler_a = tmp_path / "a.jsonl"
    roster_path = tmp_path / "roster.json"
    out = tmp_path / "out"
    corpus.write_text(json.dumps({"id": "C1", "text": "Add a tiny status page.", "source": "fixture", "kind": "short"}) + "\n")
    gold.write_text(json.dumps({"gold": {field: {"C1": next(iter(roster["vocab"][field]))} for field in roster["vocab"] if field != "lane"},
                                "basis": {field: {"C1": "human"} for field in roster["vocab"] if field != "lane"},
                                "kappa": {field: None for field in roster["vocab"] if field != "lane"}}))
    firsts = {field: next(iter(roster["vocab"][field])) for field in roster["vocab"] if field != "lane"}
    judge.write_text("".join(json.dumps({"id": "C1", "field": field, "perm": 0, "choice": value,
                                         "confidence": 0.9, "probabilities": {value: 0.9},
                                         "latency_ms": 5, "error": None}) + "\n"
                               for field, value in firsts.items()))
    labeler_a.write_text(json.dumps(dict({"id": "C1"}, **firsts)) + "\n")
    roster_path.write_text(json.dumps(roster))

    assert main(["eval", "report", "--corpus", str(corpus), "--gold", str(gold), "--judge", str(judge),
                 "--a", str(labeler_a), "--roster", str(roster_path), "--out", str(out)]) == 0
    text = (out / "report.md").read_text()
    for field in firsts:
        assert field in text


def test_cli_report_accepts_b_and_incumbent_arm(tmp_path, roster):
    corpus = tmp_path / "corpus.jsonl"
    gold = tmp_path / "gold.json"
    judge = tmp_path / "judge.jsonl"
    labeler_a = tmp_path / "a.jsonl"
    labeler_b = tmp_path / "b.jsonl"
    arm = tmp_path / "arm.jsonl"
    roster_path = tmp_path / "roster.json"
    out = tmp_path / "out"
    corpus.write_text(json.dumps({"id": "C1", "text": "Add a tiny status page.", "source": "fixture", "kind": "short"}) + "\n")
    gold.write_text(json.dumps({"gold": {"role": {"C1": "backend"}}, "basis": {"role": {"C1": "human"}},
                                "kappa": {"role": 0.25}, "agreement": {"role": 0.75}}))
    judge.write_text(json.dumps({"id": "C1", "field": "role", "perm": 0, "choice": "backend", "confidence": 0.9}) + "\n")
    labeler_a.write_text(json.dumps({"id": "C1", "role": "frontend"}) + "\n")
    labeler_b.write_text(json.dumps({"id": "C1", "role": "backend"}) + "\n")
    arm.write_text(json.dumps({"id": "C1", "role": "backend"}) + "\n")
    roster_path.write_text(json.dumps(roster))

    assert main(["eval", "report", "--corpus", str(corpus), "--gold", str(gold), "--judge", str(judge),
                 "--a", str(labeler_a), "--b", str(labeler_b), "--arm", "rule=%s" % arm,
                 "--incumbent-arm", "rule", "--roster", str(roster_path), "--out", str(out)]) == 0
    text = (out / "report.md").read_text()
    assert "labeler B human-basis subset accuracy: 1.000" in text
    assert "BELOW BAR role full gold" in text
    assert "n 1 < min_cases" in text
    assert "instability not measured" in text


def _promotion_stats(judge_rows, arm_rows=None, min_cases=2, min_coverage=1.0,
                     tolerance=0.0, threshold=0.7, max_instability=0.10,
                     incumbent_arm="rule"):
    return {
        "roster": {"judge": {"threshold": threshold},
                   "promotion": {"min_cases": min_cases, "tolerance": tolerance, "min_coverage": min_coverage}},
        "fields": ["role"],
        "kinds": ["short"],
        "corpus": [{"id": "C1", "kind": "short"}, {"id": "C2", "kind": "short"}],
        "gold": {"gold": {"role": {"C1": "backend", "C2": "docs"}},
                 "basis": {"role": {"C1": "human", "C2": "human"}}},
        "judge": judge_rows,
        "labeler_a": [{"id": "C1", "role": "backend"}, {"id": "C2", "role": "docs"}],
        "arms": {"rule": arm_rows if arm_rows is not None else [{"id": "C1", "role": "backend"}, {"id": "C2", "role": "docs"}]},
        "incumbent_arm": incumbent_arm,
        "max_instability": max_instability,
    }


def _stable_judge_rows(c1="backend", c2="docs", c1_conf=0.9, c2_conf=0.9):
    return [
        {"id": "C1", "field": "role", "perm": 0, "choice": c1, "confidence": c1_conf},
        {"id": "C2", "field": "role", "perm": 0, "choice": c2, "confidence": c2_conf},
        {"id": "C1", "field": "role", "perm": 1, "choice": c1, "confidence": c1_conf},
        {"id": "C2", "field": "role", "perm": 1, "choice": c2, "confidence": c2_conf},
    ]


def _promotion_line_from(text):
    for line in text.splitlines():
        if line.startswith(("MEETS BAR role full gold", "BELOW BAR role full gold")):
            return line
    raise AssertionError("promotion line not found")


def _assert_single_failed_promotion(stats_obj, expected):
    line = _promotion_line_from(report.render(stats_obj))
    assert line.startswith("BELOW BAR role full gold: ")
    assert expected in line
    assert "; " not in line


def test_promotion_below_bar_names_only_n_below_min_cases():
    s = _promotion_stats(_stable_judge_rows(), min_cases=3)

    _assert_single_failed_promotion(s, "n 2 < min_cases 3")


def test_promotion_below_bar_names_only_no_incumbent_arm():
    s = _promotion_stats(_stable_judge_rows(), incumbent_arm=None)

    _assert_single_failed_promotion(s, "no incumbent arm")


def test_promotion_below_bar_names_only_full_gold_accuracy_below_incumbent():
    s = _promotion_stats(_stable_judge_rows(c2="frontend", c2_conf=0.4), min_coverage=0.5, tolerance=0.1)

    _assert_single_failed_promotion(s, "judge 0.500 < rule 1.000 - tolerance 0.100")


def test_promotion_below_bar_names_only_coverage_below_min_coverage():
    s = _promotion_stats(_stable_judge_rows(c2_conf=0.4), min_coverage=1.0)

    _assert_single_failed_promotion(s, "coverage at threshold 0.700 0.500 < min_coverage 1.000")


def test_promotion_below_bar_names_only_covered_accuracy_too_low():
    arm = [{"id": "C1", "role": "backend"}, {"id": "C2", "role": "frontend"}]
    s = _promotion_stats(_stable_judge_rows(c1="frontend", c1_conf=0.9, c2="docs", c2_conf=0.4),
                         arm_rows=arm, min_coverage=0.5)

    _assert_single_failed_promotion(s, "covered accuracy at threshold 0.700 0.000 < rule 0.500 - tolerance 0.000")


def test_promotion_below_bar_names_only_instability_above_maximum():
    rows = [
        {"id": "C1", "field": "role", "perm": 0, "choice": "backend", "confidence": 0.9},
        {"id": "C2", "field": "role", "perm": 0, "choice": "docs", "confidence": 0.9},
        {"id": "C1", "field": "role", "perm": 1, "choice": "frontend", "confidence": 0.9},
        {"id": "C2", "field": "role", "perm": 1, "choice": "docs", "confidence": 0.9},
    ]
    s = _promotion_stats(rows, max_instability=0.10)

    _assert_single_failed_promotion(s, "instability 0.500 > max_instability 0.100")


def test_promotion_below_bar_names_only_instability_not_measured():
    rows = [
        {"id": "C1", "field": "role", "perm": 0, "choice": "backend", "confidence": 0.9},
        {"id": "C2", "field": "role", "perm": 0, "choice": "docs", "confidence": 0.9},
    ]
    s = _promotion_stats(rows)

    _assert_single_failed_promotion(s, "instability not measured")


def test_promotion_meets_bar_when_all_criteria_hold():
    s = _promotion_stats(_stable_judge_rows(), min_cases=2, min_coverage=1.0,
                         tolerance=0.0, threshold=0.7, max_instability=0.10)

    line = _promotion_line_from(report.render(s))
    assert line.startswith("MEETS BAR role full gold")
    assert "instability 0.000 <= max_instability 0.100" in line


def test_cli_report_bad_corpus_jsonl_is_clean_error(tmp_path, roster, capsys):
    corpus = tmp_path / "corpus.jsonl"
    gold = tmp_path / "gold.json"
    judge = tmp_path / "judge.jsonl"
    labeler_a = tmp_path / "a.jsonl"
    roster_path = tmp_path / "roster.json"
    out = tmp_path / "out"
    corpus.write_text("{bad json}\n")
    gold.write_text(json.dumps({"gold": {}, "basis": {}}))
    judge.write_text("")
    labeler_a.write_text("")
    roster_path.write_text(json.dumps(roster))

    assert main(["eval", "report", "--corpus", str(corpus), "--gold", str(gold), "--judge", str(judge),
                 "--a", str(labeler_a), "--roster", str(roster_path), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert str(corpus) in err
    assert ":1:" in err
    assert "Traceback" not in err
