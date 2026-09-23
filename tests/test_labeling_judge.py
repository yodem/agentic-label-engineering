import json
import subprocess

import pytest

from ale.labeling.judge import CommandJudge, is_mostly_english, key_of, options_for


class Proc:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def answer(choice, conf=0.9, probs=None):
    return json.dumps({"type": "choice", "choice": choice, "confidence": conf, "probabilities": probs or {choice: conf}, "model": "m"})


def test_options_carry_guidelines_and_an_escape(roster):
    opts = options_for("effort", roster)
    assert opts[0].startswith("S: ") and opts[-1] == "other: none of these fit" and len(opts) == 4
    assert [key_of(o) for o in options_for("effort", roster, order=["L", "S", "M"])] == ["L", "S", "M", "other"]


def test_vocab_key_with_colon_or_named_other_is_refused(roster):
    roster["vocab"]["role"]["a:b"] = "x"
    with pytest.raises(ValueError):
        options_for("role", roster)


def test_happy_path_maps_option_back_to_key(roster):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return Proc(0, answer("backend: Server code, APIs, data access, scripts.", 0.8,
                              {"backend: Server code, APIs, data access, scripts.": 0.8, "other: none of these fit": 0.2}))

    vote = CommandJudge(["jev-ask"], run=run).ask("role", "Which kind?", options_for("role", roster), "Add an endpoint")
    assert (vote["field"], vote["value"], vote["by"], vote["confidence"]) == ("role", "backend", "judge:command", 0.8)
    assert vote["detail"]["probabilities"] == {"backend": 0.8, "other": 0.2} and isinstance(vote["detail"]["latency_ms"], int)
    assert seen["cmd"][:3] == ["jev-ask", "choice", "Which kind?"] and seen["kw"]["input"] == "Add an endpoint"
    assert seen["kw"]["env"]["JEV_TAG"] == "label:role" and seen["kw"]["timeout"] == 30


@pytest.mark.parametrize("behaviour,reason", [
    (lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()), "command_not_found"),
    (lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)), "timeout"),
    (lambda *a, **k: Proc(4, "", "HTTP 500"), "exit_4"),
    (lambda *a, **k: Proc(0, "not json"), "bad_json"),
    (lambda *a, **k: Proc(0, answer("wizard: nope")), "unknown_option"),
])
def test_every_failure_is_an_abstain(roster, behaviour, reason):
    vote = CommandJudge(["jev-ask"], run=behaviour).ask("role", "q", options_for("role", roster), "Add an endpoint")
    assert vote["value"] is None and vote["confidence"] is None and vote["detail"]["error"] == reason


def test_non_english_and_empty_state_never_reach_the_command(roster):
    def boom(*a, **k):
        raise AssertionError("command must not run")
    j = CommandJudge(["jev-ask"], run=boom)
    assert j.ask("role", "q", options_for("role", roster), "הוסף נקודת קצה לשירות האימות")["detail"]["error"] == "non_english"
    assert j.ask("role", "q", options_for("role", roster), "   ")["detail"]["error"] == "empty_state"


def test_state_is_truncated(roster):
    seen = {}

    def run(cmd, **kw):
        seen["n"] = len(kw["input"])
        return Proc(0, answer("other: none of these fit"))
    CommandJudge(["jev-ask"], run=run).ask("role", "q", options_for("role", roster), "a " * 5000)
    assert seen["n"] == 4000


def test_lane_is_refused(roster):
    with pytest.raises(ValueError):
        CommandJudge(["jev-ask"]).ask("lane", "q", ["inline: x", "other: none of these fit"], "text")


def test_is_mostly_english():
    assert is_mostly_english("Add an endpoint") and not is_mostly_english("הוסף endpoint") and not is_mostly_english("1234 !!")


def _answer_with_confidence(conf_literal, probs_literal='{"backend: Server code, APIs, data access, scripts.": 0.8, "other: none of these fit": 0.2}'):
    return (
        '{"type": "choice", "choice": "backend: Server code, APIs, data access, scripts.", '
        '"confidence": %s, "probabilities": %s, "model": "m"}' % (conf_literal, probs_literal)
    )


@pytest.mark.parametrize("conf_literal", ["NaN", "Infinity", "-0.1", "1.5", '"high"', "true"])
def test_bad_confidence_is_an_abstain(roster, conf_literal):
    def run(cmd, **kw):
        return Proc(0, _answer_with_confidence(conf_literal))
    vote = CommandJudge(["jev-ask"], run=run).ask("role", "q", options_for("role", roster), "Add an endpoint")
    assert vote["value"] is None
    assert vote["confidence"] is None
    assert vote["detail"]["error"] == "bad_confidence"


@pytest.mark.parametrize("conf_literal", ["0", "1"])
def test_boundary_confidence_is_accepted(roster, conf_literal):
    def run(cmd, **kw):
        return Proc(0, _answer_with_confidence(conf_literal))
    vote = CommandJudge(["jev-ask"], run=run).ask("role", "q", options_for("role", roster), "Add an endpoint")
    assert vote["value"] == "backend"
    assert vote["confidence"] == float(conf_literal)


def test_non_finite_probabilities_are_dropped_not_fatal(roster):
    probs = '{"backend: Server code, APIs, data access, scripts.": NaN, "other: none of these fit": 0.2}'
    def run(cmd, **kw):
        return Proc(0, _answer_with_confidence("0.8", probs))
    vote = CommandJudge(["jev-ask"], run=run).ask("role", "q", options_for("role", roster), "Add an endpoint")
    assert vote["value"] == "backend"
    assert vote["confidence"] == 0.8
    assert vote["detail"]["probabilities"] == {"other": 0.2}


def test_huge_stdout_with_valid_json_does_not_raise(roster):
    padding = "x" * (1024 * 1024)
    payload = (
        '{"type": "choice", "choice": "backend: Server code, APIs, data access, scripts.", '
        '"confidence": 0.8, "probabilities": {"backend: Server code, APIs, data access, scripts.": 0.8, '
        '"other: none of these fit": 0.2}, "model": "m", "padding": "%s"}' % padding
    )
    def run(cmd, **kw):
        return Proc(0, payload)
    vote = CommandJudge(["jev-ask"], run=run).ask("role", "q", options_for("role", roster), "Add an endpoint")
    assert vote["value"] == "backend"
    assert vote["confidence"] == 0.8


def test_empty_stdout_is_bad_json(roster):
    def run(cmd, **kw):
        return Proc(0, "")
    vote = CommandJudge(["jev-ask"], run=run).ask("role", "q", options_for("role", roster), "Add an endpoint")
    assert vote["value"] is None
    assert vote["detail"]["error"] == "bad_json"


def test_out_of_range_probabilities_are_dropped_not_recorded(roster):
    out = json.dumps({"choice": "backend", "confidence": 0.9,
                      "probabilities": {"backend": 7.5, "frontend": -0.1, "docs": 0.05}})
    vote = CommandJudge(["judge"], run=lambda cmd, **kw: Proc(0, out)).ask(
        "role", "q", options_for("role", roster), "Add an endpoint")
    assert vote["value"] == "backend" and vote["detail"]["probabilities"] == {"docs": 0.05}


def test_eval_judge_passes_the_roster_model(monkeypatch, tmp_path):
    import ale.cli as cli
    seen = {}
    real = cli._make_judge

    def spy(roster):
        seen["model"] = roster["judge"].get("model")
        return real(roster)
    monkeypatch.setattr(cli, "_make_judge", spy)
    roster_path = tmp_path / "roster.json"
    roster_doc = json.load(open(cli.os.path.join(cli.os.path.dirname(cli.__file__), "example_roster.json")))
    roster_doc["judge"]["model"] = "judge-model-x"
    roster_path.write_text(json.dumps(roster_doc))
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("")
    cli.main(["eval", "judge", "--corpus", str(corpus), "--roster", str(roster_path), "--out", str(tmp_path / "out")])
    assert seen["model"] == "judge-model-x"
