import json
import os
import random
import stat

from ale.cli import main
from ale.evalharness import jevrun
from ale.labeling.judge import key_of


class FakeJudge:
    def __init__(self, choices=None):
        self.calls = []
        self.choices = choices or {}

    def ask(self, field, question, options, state):
        self.calls.append({"field": field, "question": question, "options": options, "state": state})
        value = self.choices.get((field, len(self.calls)), key_of(options[0]))
        if value is None:
            return {"field": field, "value": None, "by": "judge:fake", "confidence": None, "detail": {"error": "skip_case"}}
        return {
            "field": field,
            "value": value,
            "by": "judge:fake",
            "confidence": 0.8,
            "detail": {"latency_ms": 12, "probabilities": {value: 0.8}},
        }


def corpus_rows():
    return [
        {"id": "C1", "text": "Add a simple counter view.", "source": "fixture", "kind": "short"},
        {"id": "C2", "text": "Update a small settings guide.", "source": "fixture", "kind": "full"},
    ]


def test_perm_zero_uses_vocab_order(roster):
    roster = dict(roster)
    roster["vocab"] = dict(roster["vocab"])
    roster["vocab"]["role"] = {"backend": "Backend", "docs": "Documentation"}
    judge = FakeJudge()
    rows = list(jevrun.run(corpus_rows()[:1], roster, judge, ["role"], perms=1, seed=7, sensitivity_sample=0))

    assert rows[0]["perm"] == 0
    assert [key_of(o) for o in judge.calls[0]["options"]] == ["backend", "docs", "other"]


def test_seeded_permutation_is_deterministic_and_keeps_other_last(roster):
    judge = FakeJudge()
    rows = list(jevrun.run(corpus_rows()[:1], roster, judge, ["role"], perms=3, seed=7, sensitivity_sample=1))

    rng = random.Random("%s|%s|%d" % (7, "C1", 1))
    expected = list(roster["vocab"]["role"].keys())
    rng.shuffle(expected)
    assert [r["perm"] for r in rows] == [0, 1, 2]
    assert [key_of(o) for o in judge.calls[1]["options"]] == expected + ["other"]
    assert all(call["options"][-1] == "other: none of these fit" for call in judge.calls)


def test_sensitivity_sample_uses_seeded_item_shuffle(roster):
    judge = FakeJudge()
    rows = list(jevrun.run(corpus_rows(), roster, judge, ["role"], perms=2, seed=11, sensitivity_sample=1))
    sampled = list(corpus_rows())
    random.Random(11).shuffle(sampled)
    repeated_id = sampled[0]["id"]

    assert [r["id"] for r in rows if r["perm"] == 1] == [repeated_id]


def test_resume_skips_done_triples(roster):
    judge = FakeJudge()
    rows = list(jevrun.run(corpus_rows()[:1], roster, judge, ["role"], perms=2, seed=7, sensitivity_sample=1,
                           done=set([("C1", "role", 0)])))

    assert [(r["id"], r["field"], r["perm"]) for r in rows] == [("C1", "role", 1)]
    assert len(judge.calls) == 1


def test_abstain_row_carries_error(roster):
    judge = FakeJudge({("role", 1): None})
    rows = list(jevrun.run(corpus_rows()[:1], roster, judge, ["role"], perms=1, seed=7, sensitivity_sample=0))

    assert rows[0]["choice"] is None
    assert rows[0]["confidence"] is None
    assert rows[0]["error"] == "skip_case"


def test_cli_judge_max_calls_appends_exactly_and_resumes(tmp_path, roster):
    corpus = tmp_path / "corpus.jsonl"
    roster_path = tmp_path / "roster.json"
    out = tmp_path / "out"
    stub = tmp_path / "stub-judge.py"
    corpus.write_text("".join(json.dumps(r) + "\n" for r in corpus_rows()))
    roster["judge"]["command"] = [str(stub)]
    roster_path.write_text(json.dumps(roster))
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "choice = sys.argv[3]\n"
        "print(json.dumps({'type':'choice','choice':choice,'confidence':0.7,'probabilities':{choice:0.7}}))\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    args = ["eval", "judge", "--corpus", str(corpus), "--roster", str(roster_path), "--out", str(out),
            "--perms", "1", "--sensitivity-sample", "0", "--max-calls", "3"]
    assert main(args) == 0
    assert len((out / "judge.jsonl").read_text().splitlines()) == 3
    assert main(args) == 0
    lines = (out / "judge.jsonl").read_text().splitlines()
    assert len(lines) == 6
    assert len({(json.loads(line)["id"], json.loads(line)["field"], json.loads(line)["perm"]) for line in lines}) == 6


def test_cli_judge_resume_truncates_torn_tail_before_appending(tmp_path, roster):
    corpus = tmp_path / "corpus.jsonl"
    roster_path = tmp_path / "roster.json"
    out = tmp_path / "out"
    stub = tmp_path / "stub-judge.py"
    corpus.write_text(json.dumps(corpus_rows()[0]) + "\n")
    roster["judge"]["command"] = [str(stub)]
    roster_path.write_text(json.dumps(roster))
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "choice = sys.argv[3]\n"
        "print(json.dumps({'type':'choice','choice':choice,'confidence':0.7,'probabilities':{choice:0.7}}))\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    out.mkdir()
    complete = {"id": "C1", "field": "role", "perm": 0, "choice": "backend", "confidence": 0.7}
    (out / "judge.jsonl").write_text(json.dumps(complete) + "\n" + '{"id": "C1", "field":')

    assert main(["eval", "judge", "--corpus", str(corpus), "--roster", str(roster_path),
                 "--out", str(out), "--perms", "1", "--sensitivity-sample", "0"]) == 0
    lines = (out / "judge.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    triples = [(row["id"], row["field"], row["perm"]) for row in rows]
    assert len(triples) == len(set(triples))
    assert ("C1", "role", 0) in triples
    assert all(isinstance(row, dict) for row in rows)


def test_cli_judge_malformed_middle_line_is_clean_error(tmp_path, roster, capsys):
    corpus = tmp_path / "corpus.jsonl"
    roster_path = tmp_path / "roster.json"
    out = tmp_path / "out"
    corpus.write_text(json.dumps(corpus_rows()[0]) + "\n")
    roster_path.write_text(json.dumps(roster))
    out.mkdir()
    (out / "judge.jsonl").write_text(
        json.dumps({"id": "C1", "field": "role", "perm": 0}) + "\n"
        "{bad json}\n"
    )

    assert main(["eval", "judge", "--corpus", str(corpus), "--roster", str(roster_path),
                 "--out", str(out), "--perms", "1", "--sensitivity-sample", "0"]) == 1
    err = capsys.readouterr().err
    assert "judge.jsonl:2:" in err
    assert "Traceback" not in err


def test_cli_judge_refuses_tracked_output_directory(tmp_path, roster):
    repo = tmp_path / "repo"
    repo.mkdir()
    os.system("git -C %s init >/dev/null 2>&1" % repo)
    corpus = repo / "corpus.jsonl"
    roster_path = repo / "roster.json"
    corpus.write_text(json.dumps(corpus_rows()[0]) + "\n")
    roster_path.write_text(json.dumps(roster))

    assert main(["eval", "judge", "--corpus", str(corpus), "--roster", str(roster_path),
                 "--out", str(repo / "eval-out")]) == 1
