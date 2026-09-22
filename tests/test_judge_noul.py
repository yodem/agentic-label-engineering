"""CommandJudge.noul parsing, model recording, and the locality Noul in the cascade."""

import json
import subprocess

import pytest

from ale.labeling import cascade as CAS
from ale.labeling.judge import CommandJudge


class _Run:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr="")


def test_noul_returns_probability_model_and_latency():
    run = _Run(json.dumps({"type": "noul", "noul": 0.93, "model": "jev-1.13.0"}))
    answer = CommandJudge(["fake-judge"], run=run).noul("unattended", "Can it run alone?", "{\"task_text\": \"x\"}")
    assert answer["p"] == 0.93 and answer["model"] == "jev-1.13.0"
    assert isinstance(answer["detail"]["latency_ms"], int)
    cmd, kwargs = run.calls[0]
    assert cmd == ["fake-judge", "noul", "Can it run alone?"]
    assert kwargs["env"]["JEV_TAG"] == "evidence:unattended"


@pytest.mark.parametrize("stdout,code,error", [
    (json.dumps({"type": "choice", "choice": "a", "confidence": 0.9, "probabilities": {}}), 0, "bad_json"),
    ("not json", 0, "bad_json"),
    (json.dumps({"noul": 1.5}), 0, "bad_probability"),
    (json.dumps({"noul": True}), 0, "bad_probability"),
    ("", 4, "exit_4"),
])
def test_noul_abstains_on_bad_answers(stdout, code, error):
    answer = CommandJudge(["fake-judge"], run=_Run(stdout, code)).noul("k", "Q?", "state text")
    assert answer["p"] is None and answer["detail"]["error"] == error


def test_noul_refuses_empty_and_non_english_state_without_calling():
    run = _Run(json.dumps({"noul": 0.9}))
    judge = CommandJudge(["fake-judge"], run=run)
    assert judge.noul("k", "Q?", "  ")["detail"]["error"] == "empty_state"
    assert judge.noul("k", "Q?", "שלום עולם")["detail"]["error"] == "non_english"
    assert run.calls == []


def test_roster_model_is_sent_and_recorded_when_the_answer_has_none():
    run = _Run(json.dumps({"noul": 0.2}))
    answer = CommandJudge(["fake-judge"], run=run, model="jev-1.13.0").noul("k", "Q?", "state text")
    assert run.calls[0][1]["env"]["JEV_MODEL"] == "jev-1.13.0"
    assert answer["model"] == "jev-1.13.0"


def test_choice_answer_records_its_model():
    run = _Run(json.dumps({"choice": "backend: x", "confidence": 0.8,
                           "probabilities": {"backend: x": 0.8}, "model": "jev-1.13.0"}))
    answer = CommandJudge(["fake-judge"], run=run).ask("role", "Which role?", ["backend: x"], "state text")
    assert answer["value"] == "backend" and answer["model"] == "jev-1.13.0"


def test_cascade_asks_locality_as_a_noul_and_maps_it(roster):
    run = _Run(json.dumps({"noul": 0.9, "model": "jev-1.13.0"}))
    vote = CAS._ask_judge(CommandJudge(["fake-judge"], run=run), "locality", roster, "task needs the keychain")
    assert run.calls[0][0][1] == "noul"
    assert run.calls[0][0][2] == roster["judge"]["questions"]["locality"]
    assert vote["value"] == "local" and vote["confidence"] == pytest.approx(0.9)
    assert vote["detail"]["evidence"] == {"locality": 0.9} and vote["detail"]["rule"] == "needs_planner_machine"
    low = CAS._ask_judge(CommandJudge(["fake-judge"], run=_Run(json.dumps({"noul": 0.1}))),
                         "locality", roster, "plain task")
    assert low["value"] == "any"
