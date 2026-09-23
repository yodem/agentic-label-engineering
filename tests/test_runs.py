import json
import argparse
import os
import time

from ale import board
from ale import cli


def _run(root, name, ts, run_id=None):
    path = root / name
    (path / "labels").mkdir(parents=True)
    rows = []
    if run_id:
        rows.append({"type": "run_started", "run_id": run_id, "ts": ts - 1})
    rows.append({"type": "heartbeat", "ts": ts})
    (path / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_list_runs_orders_by_event_and_marks_current(tmp_path):
    from ale import runs
    old = _run(tmp_path, "old", 100, "old-id")
    newest = _run(tmp_path, "new", 200, "new-id")
    (tmp_path / "current").write_text("old\n")
    got = runs.list_runs(str(tmp_path))
    assert [row["dir"] for row in got] == ["new", "old"]
    assert got[0] == {"dir": "new", "path": str(newest), "run_id": "new-id",
                      "last_event_ts": 200.0, "is_current": False}
    assert got[1]["is_current"] is True


def test_list_runs_skips_invalid_directories_and_symlinks(tmp_path):
    from ale import runs
    valid = _run(tmp_path, "valid", 10)
    (tmp_path / "no-labels").mkdir()
    (tmp_path / "no-events").mkdir()
    (tmp_path / "no-events" / "labels").mkdir()
    (tmp_path / "link").symlink_to(valid, target_is_directory=True)
    assert [row["dir"] for row in runs.list_runs(str(tmp_path))] == ["valid"]


def test_list_runs_reads_only_tail_of_large_event_file(tmp_path, monkeypatch):
    from ale import runs
    path = _run(tmp_path, "large", 1)
    with open(path / "events.jsonl", "w") as handle:
        handle.write((json.dumps({"ts": 1}) + "\n") * 200000)
        handle.write(json.dumps({"ts": 42}) + "\n")
    original = open
    reads = []

    class TailOnly:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.wrapped.close()
        def seek(self, *args):
            return self.wrapped.seek(*args)
        def tell(self):
            return self.wrapped.tell()
        def read(self, size=-1):
            reads.append(size)
            return self.wrapped.read(size)

    def tracking_open(filename, mode="r", *args, **kwargs):
        wrapped = original(filename, mode, *args, **kwargs)
        return TailOnly(wrapped) if str(filename).endswith("events.jsonl") else wrapped

    monkeypatch.setattr("builtins.open", tracking_open)
    assert runs.list_runs(str(tmp_path))[0]["last_event_ts"] == 42
    assert reads and all(size >= 0 for size in reads)


def test_runs_cli_json(tmp_path, capsys):
    _run(tmp_path, "one", 12)
    assert cli.main(["runs", "--json", "--runs-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["dir"] == "one"


def test_cmd_runs_default_uses_ale_dir(tmp_path, monkeypatch):
    target = tmp_path / ".ale" / "runs"
    target.mkdir(parents=True)
    seen = []
    monkeypatch.setattr(cli, "_ale_dir", lambda: str(tmp_path / ".ale"))
    monkeypatch.setattr(cli, "list_runs", lambda path: seen.append(path) or [])
    args = argparse.Namespace(runs_dir=None, json=True)
    assert cli.cmd_runs(args) == 0
    assert seen == [str(target)]


def test_board_default_uses_latest_without_starting_server(tmp_path, monkeypatch):
    from ale import cli
    _run(tmp_path, "old", 1)
    newest = _run(tmp_path, "new", 2)
    (tmp_path / "current").write_text("old\n")
    assert cli._choose_board_run(str(tmp_path)) == str(newest)


def test_cmd_board_default_uses_ale_dir(tmp_path, monkeypatch, capsys):
    target = tmp_path / ".ale" / "runs"
    target.mkdir(parents=True)
    selected = _run(target, "picked", time.time())
    chosen_dirs = []

    servers = []
    class FakeServer:
        url = "http://127.0.0.1:1234/token/"
        def __init__(self, run_dir, provider, **kwargs):
            self.run_dir = run_dir
            servers.append(self)
        def start(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(cli, "_ale_dir", lambda: str(tmp_path / ".ale"))
    monkeypatch.setattr(cli, "BoardServer", FakeServer)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    monkeypatch.setattr(cli.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.cmd_board(argparse.Namespace(run_dir=None, run_id=None, open=False, roster=None)) == 0
    assert str(selected) in capsys.readouterr().err
    assert servers[0].run_dir == str(selected)


def test_build_snapshot_projects_other_runs_with_cap_and_window(tmp_path):
    now = time.time()
    _run(tmp_path, "current-run", now)
    for index in range(5):
        _run(tmp_path, "other-%s" % index, now - index - 1, "id-%s" % index)
    _run(tmp_path, "stale", now - 90000)
    result = board.build_snapshot(str(tmp_path / "current-run"), {}, {}, [], {},
                                  runs_dir=str(tmp_path))
    assert [row["dir"] for row in result["run"]["other_runs"]] == ["other-0", "other-1", "other-2"]
    assert result["run"]["other_runs"][0]["id"] == "id-0"
    assert board.build_snapshot(str(tmp_path), {}, {}, [], {})["run"]["other_runs"] == []


def test_other_runs_are_discovered_from_explicit_run_parent(tmp_path, monkeypatch):
    from types import SimpleNamespace
    selected_parent = tmp_path / "selected-runs"
    sibling_parent = tmp_path / "cwd-runs"
    current = _run(selected_parent, "current", time.time())
    _run(selected_parent, "sibling", time.time() - 1, "sibling-id")
    _run(sibling_parent, "unrelated", time.time(), "unrelated-id")
    snapshots = []

    class FakeServer:
        url = "http://127.0.0.1:1234/token/"
        def __init__(self, run_dir, provider, **kwargs):
            self.provider = provider
        def start(self):
            snapshots.append(self.provider())
        def close(self):
            pass

    (sibling_parent / ".ale" / "runs").mkdir(parents=True)
    monkeypatch.setattr(cli, "_ale_dir", lambda: str(sibling_parent / ".ale"))
    monkeypatch.setattr(cli, "BoardServer", FakeServer)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    monkeypatch.setattr(cli.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="{}", stderr=""))
    monkeypatch.setattr(cli.L, "load_labels", lambda _: {})
    monkeypatch.setattr(cli.E, "read_events", lambda _: [])
    cli.cmd_board(argparse.Namespace(run_dir=str(current), run_id=None, open=False, roster=None))
    other_runs = snapshots[0]["run"]["other_runs"]
    assert [row["id"] for row in other_runs] == ["sibling-id"]


def test_board_html_has_other_runs_header_hidden_when_empty():
    html = open(os.path.join(os.path.dirname(board.__file__), "board.html"), encoding="utf-8").read()
    assert 'id="other-runs"' in html
    assert "otherRuns.length" in html
    assert "Other runs:" in html
