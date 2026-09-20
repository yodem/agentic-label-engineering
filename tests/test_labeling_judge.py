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
