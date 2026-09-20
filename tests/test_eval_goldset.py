import json

from ale.cli import main
from ale.evalharness import goldset


def row(task_id, role, risk="low"):
    return {"id": task_id, "role": role, "model_tier": "standard", "risk": risk, "effort": "M"}


def test_agreement_becomes_gold_with_basis():
    built = goldset.build([row("A1", "backend")], [row("A1", "backend")], [])

    assert built["gold"]["role"] == {"A1": "backend"}
    assert built["basis"]["role"] == {"A1": "agree"}
    assert built["queue"] == []
    assert built["agreement"]["role"] == 1.0


def test_disagreement_goes_to_queue():
    built = goldset.build([row("A1", "backend")], [row("A1", "frontend")], [])

    assert {"id": "A1", "field": "role", "a": "backend", "b": "frontend"} in built["queue"]
    assert built["gold"]["role"] == {}
    assert built["basis"]["role"] == {}


def test_ruling_moves_pair_from_queue_to_human_gold():
    built = goldset.build(
        [row("A1", "backend")],
        [row("A1", "frontend")],
        [{"id": "A1", "field": "role", "value": "docs"}],
    )

    assert built["gold"]["role"] == {"A1": "docs"}
    assert built["basis"]["role"] == {"A1": "human"}
    assert built["queue"] == []


def test_kappa_per_field_uses_common_ids():
    built = goldset.build(
        [row("A1", "backend"), row("A2", "backend"), row("A3", "docs")],
        [row("A1", "backend"), row("A2", "docs"), row("A3", "docs")],
        [],
    )

    assert round(built["kappa"]["role"], 3) == 0.4
    assert built["kappa"]["risk"] is None


def test_missing_ids_are_counted_and_skipped():
    built = goldset.build(
        [row("A1", "backend"), row("A2", "docs")],
        [row("A1", "backend"), row("B3", "test")],
        [],
    )

    assert built["missing"]["role"] == 2
    assert built["gold"]["role"] == {"A1": "backend"}
    assert all(item["id"] != "A2" for item in built["queue"])
    assert all(item["id"] != "B3" for item in built["queue"])


def test_cli_gold_writes_expected_files(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    rulings = tmp_path / "rulings.jsonl"
    out = tmp_path / "out"
    a.write_text(json.dumps(row("A1", "backend")) + "\n")
    b.write_text(json.dumps(row("A1", "frontend")) + "\n")
    rulings.write_text(json.dumps({"id": "A1", "field": "role", "value": "backend"}) + "\n")

    assert main(["eval", "gold", "--a", str(a), "--b", str(b), "--rulings", str(rulings), "--out", str(out)]) == 0
    written = json.loads((out / "gold.json").read_text())
    assert written["gold"]["role"] == {"A1": "backend"}
    assert written["basis"]["role"] == {"A1": "human"}
    assert (out / "gold-queue.jsonl").read_text() == ""


def test_cli_gold_bad_a_jsonl_is_clean_error(tmp_path, capsys):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    out = tmp_path / "out"
    a.write_text("{bad json}\n")
    b.write_text(json.dumps(row("A1", "backend")) + "\n")

    assert main(["eval", "gold", "--a", str(a), "--b", str(b), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert str(a) in err
    assert ":1:" in err
    assert "Traceback" not in err


def test_cli_gold_non_object_jsonl_is_clean_error(tmp_path, capsys):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    out = tmp_path / "out"
    a.write_text("[]\n")
    b.write_text(json.dumps(row("A1", "backend")) + "\n")

    assert main(["eval", "gold", "--a", str(a), "--b", str(b), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert str(a) in err
    assert ":1:" in err
    assert "expected JSON object" in err
