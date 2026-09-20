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
