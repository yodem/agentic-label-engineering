import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ale import setup as S
from ale.dispatch import render_prompt
from ale.roster import _apply_vocab_defaults

ROOT = Path(__file__).resolve().parents[1]

TOML = '''
[hosts.dev-server]
ssh = "user@box"
repo_root = "/srv/repos"

[hosts.mac]
ssh = ""
'''

EMPTY = {"harnesses": {}, "ck": False, "hosts": []}


@pytest.fixture
def example_roster():
    with open(ROOT / "ale" / "example_roster.json", encoding="utf-8") as handle:
        roster = json.load(handle)
    _apply_vocab_defaults(roster)
    return roster


def _fake(directory, name, body):
    path = Path(directory) / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_bin(tmp_path):
    """A PATH with only fake tools: no real harness, ssh or network is ever reached."""
    directory = tmp_path / "fakebin"
    directory.mkdir()
    for tool in ("git", "sh", "cat", "env"):
        real = subprocess.run(["/usr/bin/env", "which", tool], stdout=subprocess.PIPE).stdout.decode().strip()
        if real:
            os.symlink(real, str(directory / tool))
    return directory


@pytest.fixture
def run_ale(fake_bin, tmp_path):
    def run(args, cwd, env_extra=None):
        env = {"PATH": str(fake_bin), "PYTHONPATH": str(ROOT), "HOME": str(tmp_path / "home"),
               "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-m", "ale"] + list(args), cwd=str(cwd), env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=60)
    return run


def test_parse_hosts_toml():
    hosts = S.parse_hosts_toml(TOML)
    assert hosts[0] == {"name": "dev-server", "ssh": "user@box", "repo_root": "/srv/repos"}
    assert hosts[1] == {"name": "mac", "ssh": ""}


def test_parse_hosts_toml_ignores_other_tables_comments_and_non_strings():
    text = '# c\n[other]\nssh = "x"\n[hosts."quoted.name"]  # note\nssh = "a@b" # trailing\nport = 22\nrepo_root = 5\n'
    assert S.parse_hosts_toml(text) == [{"name": "quoted.name", "ssh": "a@b"}]


def test_detect_harnesses_from_path(tmp_path):
    (tmp_path / "codex").write_text("#!/bin/sh\n"); (tmp_path / "codex").chmod(0o755)
    found = S.detect({"PATH": str(tmp_path)}, probe=False)
    assert found["harnesses"] == {"claude": False, "codex": True, "pi": False} and found["ck"] is False


def test_detect_records_version_first_line_and_hosts_without_probe(tmp_path):
    _fake(tmp_path, "pi", 'echo "pi 1.2.3"; echo second')
    _fake(tmp_path, "ssh", 'echo called >> "%s"; exit 0' % (tmp_path / "ssh.log"))
    hosts = tmp_path / "hosts.toml"
    hosts.write_text(TOML)
    found = S.detect({"PATH": str(tmp_path), "ALE_HOSTS_FILE": str(hosts)}, probe=False)
    assert found["versions"] == {"pi": "pi 1.2.3"}
    assert found["hosts"][0] == {"name": "dev-server", "ssh": "user@box", "repo_root": "/srv/repos",
                                 "reachable": None, "ale_version": None}
    assert not (tmp_path / "ssh.log").exists()


def test_detect_probe_uses_batch_mode_ssh(tmp_path):
    log = tmp_path / "ssh.log"
    _fake(tmp_path, "ssh", 'echo "$@" > "%s"; echo "ale 0.3.0"' % log)
    hosts = tmp_path / "hosts.toml"
    hosts.write_text(TOML)
    found = S.detect({"PATH": str(tmp_path), "ALE_HOSTS_FILE": str(hosts)}, probe=True)
    assert found["hosts"][0]["reachable"] is True and found["hosts"][0]["ale_version"] == "ale 0.3.0"
    assert found["hosts"][1]["reachable"] is None
    assert log.read_text().split() == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "--", "user@box", "ale", "--version"]


def test_detect_probe_unreachable_host(tmp_path):
    _fake(tmp_path, "ssh", "exit 255")
    hosts = tmp_path / "hosts.toml"
    hosts.write_text(TOML)
    found = S.detect({"PATH": str(tmp_path), "ALE_HOSTS_FILE": str(hosts)}, probe=True)
    assert found["hosts"][0]["reachable"] is False


def test_questions_cover_every_layer():
    layers = {q["layer"] for q in S.questions({"harnesses": {"claude": True, "codex": True, "pi": False}, "ck": True, "hosts": []})}
    assert layers == {"harness", "models", "refs", "remote", "judge", "notes"}


def test_questions_defaults_from_roster_and_detection(example_roster):
    detected = {"harnesses": {"claude": True, "codex": True, "pi": False}, "ck": False, "private": True,
                "hosts": [{"name": "dev-server", "ssh": "user@box"}, {"name": "mac", "ssh": ""}]}
    items = {q["id"]: q for q in S.questions(detected, example_roster, env={})}
    assert items["models.codex.standard"]["default"] == "gpt-5-codex"
    assert items["models.claude.frontier"]["default"] == "claude-opus-5-5"
    assert "models.pi.cheap" not in items
    assert items["harness.default"]["options"] == ["claude", "codex"]
    assert items["remote.host"]["options"] == ["none", "dev-server"]
    assert items["judge.mode"]["default"] == "off" and items["project.private"]["default"] is True
    assert items["refs.book"]["default"] == "" and items["refs.file"]["default"] == ""
    for item in items.values():
        assert set(item) >= {"id", "layer", "prompt", "kind", "default"}
        assert item["kind"] in ("choice", "text", "bool")


def test_apply_answers_writes_roster_keys(example_roster):
    answers = {"harness.default": "codex", "models.codex.standard": "gpt-6-luna", "refs.file": "~/refs.json",
               "models.codex.cheap": "gpt-6-mini", "models.codex.frontier": "gpt-6",
               "remote.host": "dev-server", "notes.project": "Run ruff before submit."}
    roster, report = S.apply(example_roster, answers, {"harnesses": {"codex": True}, "ck": True, "hosts": []})
    assert roster["remote_host"] == "dev-server" and roster["refs_file"] == "~/refs.json"
    assert {"role": "*", "model_tier": "standard", "executor": "codex", "model": "gpt-6-luna"} in roster["routing"]
    assert roster["notes"] == "Run ruff before submit." and any("refs" in line for line in report)


def test_apply_model_for_non_default_harness_adds_wildcard_row_after_default(example_roster):
    roster, _ = S.apply(example_roster, {"models.pi.cheap": "some/model"}, EMPTY)
    rows = [row for row in roster["routing"] if row["role"] == "*" and row["model_tier"] == "cheap"]
    assert rows[0]["executor"] == "claude-headless"
    assert rows[-1] == {"role": "*", "model_tier": "cheap", "executor": "pi", "model": "some/model"}


def test_apply_records_harness_versions(example_roster):
    detected = {"harnesses": {"codex": True, "pi": False}, "versions": {"codex": "codex-cli 1.0"}, "ck": False, "hosts": []}
    roster, _ = S.apply(example_roster, {}, detected)
    assert roster["harnesses"] == {"codex": {"version": "codex-cli 1.0"}}


def test_apply_does_not_mutate_input_and_none_host_clears(example_roster):
    example_roster["remote_host"] = "dev-server"
    roster, _ = S.apply(example_roster, {"remote.host": "none"}, EMPTY)
    assert roster["remote_host"] is None and example_roster["remote_host"] == "dev-server"


def test_apply_refuses_secret_looking_answers(example_roster):
    with pytest.raises(ValueError, match="export"):
        S.apply(example_roster, {"notes.project": "use sk-abcdefghijklmnopqrstuvwxyz0123"}, {"harnesses": {}, "ck": False, "hosts": []})
    with pytest.raises(ValueError, match="export"):
        S.apply(example_roster, {"notes.project": "the token is ab12cd34ef56gh78ij90"}, EMPTY)


def test_apply_accepts_long_ordinary_notes(example_roster):
    notes = ("The key files are in src/ and tests use a fake token store; run the linter first. "
             "See src/components/KeyboardShortcuts.tsx for the handler.")
    roster, _ = S.apply(example_roster, {"notes.project": notes}, EMPTY)
    assert roster["notes"] == notes


def test_private_repo_defaults_judge_off(example_roster):
    roster, _ = S.apply(example_roster, {"project.private": True}, {"harnesses": {}, "ck": False, "hosts": []})
    assert roster["judge"]["default"] == "off"


def test_explicit_judge_mode_wins(example_roster):
    roster, report = S.apply(example_roster, {"project.private": False, "judge.mode": "shadow"}, EMPTY)
    assert roster["judge"]["default"] == "shadow"


def test_cli_answers_file_is_non_interactive(tmp_path, run_ale):
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"remote.host": "dev-server"}))
    assert run_ale(["setup", "--force", "--answers", str(answers)], cwd=tmp_path).returncode == 0
    assert json.load(open(tmp_path / ".ale" / "roster.json"))["remote_host"] == "dev-server"


def test_cli_answers_update_existing_roster_without_force(tmp_path, run_ale):
    assert run_ale(["setup"], cwd=tmp_path).returncode == 0
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"notes.project": "Use tabs."}))
    out = run_ale(["setup", "--answers", str(answers)], cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    assert json.load(open(tmp_path / ".ale" / "roster.json"))["notes"] == "Use tabs."


def test_cli_answers_secret_is_refused_and_nothing_written(tmp_path, run_ale):
    assert run_ale(["setup"], cwd=tmp_path).returncode == 0
    before = (tmp_path / ".ale" / "roster.json").read_text()
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"notes.project": "password=Hunter2Hunter2Hunter2x"}))
    out = run_ale(["setup", "--answers", str(answers)], cwd=tmp_path)
    assert out.returncode == 1 and "export" in out.stderr
    assert (tmp_path / ".ale" / "roster.json").read_text() == before


def test_cli_plain_setup_without_tty_keeps_old_behaviour(tmp_path, run_ale):
    out = run_ale(["setup"], cwd=tmp_path)
    assert out.returncode == 0 and "Next: /label-layer PLAN.md" in out.stdout
    assert run_ale(["setup"], cwd=tmp_path).returncode == 1
    assert run_ale(["setup", "--judge", "shadow"], cwd=tmp_path).returncode == 0
    assert json.load(open(tmp_path / ".ale" / "roster.json"))["judge"]["default"] == "shadow"


def test_cli_questions_json(tmp_path, run_ale):
    out = run_ale(["setup", "--questions", "--json"], cwd=tmp_path)
    ids = [q["id"] for q in json.loads(out.stdout)]
    assert "refs.book" in ids and "remote.host" in ids and "notes.project" in ids
    assert not (tmp_path / ".ale").exists()


def test_cli_questions_fill_models_for_fake_harness(tmp_path, run_ale, fake_bin):
    _fake(fake_bin, "codex", 'echo "codex-cli 9.9"')
    out = run_ale(["setup", "--questions", "--json"], cwd=tmp_path)
    ids = [q["id"] for q in json.loads(out.stdout)]
    assert ["models.codex.cheap", "models.codex.standard", "models.codex.frontier"] == [i for i in ids if i.startswith("models.")]


def test_cli_check_reports_broken_refs_and_login_commands(tmp_path, run_ale, fake_bin):
    _fake(fake_bin, "codex", 'echo "codex-cli 9.9"')
    _fake(fake_bin, "pi", 'echo "pi 1.0"')
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"refs.file": str(tmp_path / "missing.json")}))
    assert run_ale(["setup", "--force", "--answers", str(answers)], cwd=tmp_path).returncode == 0
    out = run_ale(["setup", "--check"], cwd=tmp_path)
    assert out.returncode == 1 and "BROKEN refs file" in out.stdout
    assert "`codex login`" in out.stdout and "`pi /login`" in out.stdout
    (tmp_path / "missing.json").write_text("{}")
    assert run_ale(["setup", "--check"], cwd=tmp_path).returncode == 0


def test_cli_check_unreachable_remote_host(tmp_path, run_ale, fake_bin):
    _fake(fake_bin, "ssh", "exit 255")
    hosts = tmp_path / "hosts.toml"; hosts.write_text(TOML)
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"remote.host": "dev-server"}))
    env = {"ALE_HOSTS_FILE": str(hosts)}
    assert run_ale(["setup", "--force", "--answers", str(answers)], cwd=tmp_path, env_extra=env).returncode == 0
    out = run_ale(["setup", "--check"], cwd=tmp_path, env_extra=env)
    assert out.returncode == 1 and "unreachable" in out.stdout
    _fake(fake_bin, "ssh", 'echo "ale 0.3.0"')
    assert run_ale(["setup", "--check"], cwd=tmp_path, env_extra=env).returncode == 0


def test_cli_writes_only_the_roster(tmp_path, run_ale):
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"harness.default": "claude", "notes.project": "n", "judge.mode": "off"}))
    repo = tmp_path / "repo"; repo.mkdir()
    home = tmp_path / "home"  # the interpreter may cache bytecode under HOME; setup writes nothing there
    before = {p for p in tmp_path.rglob("*") if home not in p.parents}
    assert run_ale(["setup", "--force", "--answers", str(answers)], cwd=repo).returncode == 0
    created = {p for p in tmp_path.rglob("*") if home not in p.parents and p != home} - before
    assert created == {repo / ".ale", repo / ".ale" / "roster.json"}


def test_ask_keeps_default_and_reasks_on_secret():
    replies = iter(["", "token sk-aaaaaaaaaaaaaaaaaaaaaaaa1", "plain"])
    shown = []
    question = {"id": "notes.project", "kind": "text", "prompt": "p", "default": "d"}
    assert S.ask(question, read=lambda _: next(replies), write=shown.append) == "d"
    assert S.ask(question, read=lambda _: next(replies), write=shown.append) == "plain"
    assert shown and "export" in shown[0]
    bool_q = {"id": "project.private", "kind": "bool", "prompt": "p", "default": False}
    assert S.ask(bool_q, read=lambda _: "yes") is True


def test_render_prompt_includes_project_notes_before_payload(tmp_path):
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps({"notes": "Run ruff before submit."}))
    label = {"task_id": "T1", "title": "t", "labels": {}, "acceptance": []}
    text = render_prompt(label, {"roster": str(roster)})
    assert "Project notes (from ale setup)\n```\nRun ruff before submit.\n```" in text
    assert text.index("Project notes") < text.index("ALE_PROMPT_JSON")
    assert "Project notes" not in render_prompt(label, {})
    long_notes = render_prompt(label, {}, notes="x" * 5000)
    assert "x" * 2000 in long_notes and "x" * 2001 not in long_notes


def test_setup_skill_is_portable_and_names_the_procedure():
    from ale.frontmatter import parse_frontmatter
    text = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"\bc[a-z0-9]{24}\b|/Users/|/home/|~/|@gmail|@[a-z0-9-]+\.[a-z]|—", text) is None
    frontmatter, body = parse_frontmatter(text)
    assert frontmatter["name"] == "setup"
    for phrase in ("set up ALE", "configure ALE", "ale setup"):
        assert phrase in frontmatter["description"]
    for needle in ("ale setup --questions --json", "ale setup --answers", "ale setup --check",
                   "Anything else executors should know about this project?", "notes.project",
                   "agent-handbook", "Recommended"):
        assert needle in body, needle


def _roster_bytes(tmp_path):
    return (tmp_path / ".ale" / "roster.json").read_bytes()


def test_cli_unknown_harness_is_usage_and_roster_unchanged(tmp_path, run_ale):
    assert run_ale(["setup"], cwd=tmp_path).returncode == 0
    before = _roster_bytes(tmp_path)
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"harness.default": "gpt"}))
    out = run_ale(["setup", "--answers", str(answers)], cwd=tmp_path)
    assert out.returncode == 2 and "unknown harness gpt" in out.stderr and "Traceback" not in out.stderr
    assert _roster_bytes(tmp_path) == before


def test_cli_never_writes_a_roster_harness_check_rejects(tmp_path, run_ale):
    assert run_ale(["setup"], cwd=tmp_path).returncode == 0
    path = tmp_path / ".ale" / "roster.json"
    roster = json.loads(path.read_text())
    roster["harnesses"] = {"mine": {"headless": ["mine", "--no-prompt"]}}
    path.write_text(json.dumps(roster))
    before = _roster_bytes(tmp_path)
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"notes.project": "n"}))
    out = run_ale(["setup", "--answers", str(answers)], cwd=tmp_path)
    assert out.returncode == 2 and "{prompt}" in out.stderr
    assert _roster_bytes(tmp_path) == before


def test_models_touch_only_wildcard_rows_and_defaults_are_idempotent(example_roster):
    example_roster["routing"].append({"role": "frontend", "model_tier": "standard",
                                      "executor": "claude-headless", "model": "claude-pinned"})
    detected = {"harnesses": {"claude": True, "codex": True, "pi": False}, "versions": {"codex": "codex 1"},
                "ck": False, "hosts": [], "private": True}
    defaults = {q["id"]: q["default"] for q in S.questions(detected, example_roster, env={})}
    first, _ = S.apply(example_roster, defaults, detected)
    assert {"role": "frontend", "model_tier": "standard", "executor": "claude-headless",
            "model": "claude-pinned"} in first["routing"]
    assert first["routing"] == example_roster["routing"]
    again = {q["id"]: q["default"] for q in S.questions(detected, first, env={})}
    second, _ = S.apply(first, again, detected)
    assert json.dumps(second, sort_keys=True) == json.dumps(first, sort_keys=True)
    changed, _ = S.apply(first, {"models.claude.standard": "claude-new"}, detected)
    assert {"role": "frontend", "model_tier": "standard", "executor": "claude-headless",
            "model": "claude-pinned"} in changed["routing"]
    assert {"role": "backend", "model_tier": "standard", "executor": "codex-exec",
            "model": "gpt-5-codex"} in changed["routing"]
    star = [r for r in changed["routing"] if r["role"] == "*" and r["model_tier"] == "standard"]
    assert star[0]["model"] == "claude-new"


def test_cli_defaults_rerun_is_byte_idempotent(tmp_path, run_ale):
    assert run_ale(["setup"], cwd=tmp_path).returncode == 0
    items = json.loads(run_ale(["setup", "--questions", "--json"], cwd=tmp_path).stdout)
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({q["id"]: q["default"] for q in items}))
    assert run_ale(["setup", "--answers", str(answers)], cwd=tmp_path).returncode == 0
    first = _roster_bytes(tmp_path)
    assert run_ale(["setup", "--answers", str(answers)], cwd=tmp_path).returncode == 0
    assert _roster_bytes(tmp_path) == first


def test_cli_judge_kept_when_judge_layer_not_answered(tmp_path, run_ale):
    assert run_ale(["setup"], cwd=tmp_path).returncode == 0
    assert run_ale(["setup", "--judge", "shadow"], cwd=tmp_path).returncode == 0
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"remote.host": "none"}))
    assert run_ale(["setup", "--answers", str(answers)], cwd=tmp_path).returncode == 0
    assert json.load(open(tmp_path / ".ale" / "roster.json"))["judge"]["default"] == "shadow"


def test_switching_harness_without_models_is_refused_unless_answered(example_roster):
    with pytest.raises(S.AnswerError) as info:
        S.apply(example_roster, {"harness.default": "pi"}, EMPTY)
    assert "models.pi.cheap" in str(info.value) and "models.pi.frontier" in str(info.value)
    answers = {"harness.default": "pi", "models.pi.cheap": "a/b", "models.pi.standard": "c/d", "models.pi.frontier": "e/f"}
    roster, _ = S.apply(example_roster, answers, EMPTY)
    star = {r["model_tier"]: r for r in roster["routing"] if r["role"] == "*"}
    assert star["cheap"] == {"role": "*", "model_tier": "cheap", "executor": "pi", "model": "a/b"}


def test_cli_switching_harness_without_models_exits_2(tmp_path, run_ale):
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"harness.default": "pi"}))
    out = run_ale(["setup", "--force", "--answers", str(answers)], cwd=tmp_path)
    assert out.returncode == 2 and "models.pi.cheap" in out.stderr


@pytest.mark.parametrize("answers", [
    {"project.private": "yes"}, {"notes.project": 5}, {"harness.default": ["codex"]},
    {"judge.mode": "loud"}, {"models.claude.huge": "m"}, {"models.nope.cheap": "m"}, {"no.such": "x"},
])
def test_answer_types_are_checked(example_roster, answers):
    with pytest.raises(S.AnswerError):
        S.apply(example_roster, answers, EMPTY)


def test_cli_bad_answer_type_is_usage_without_traceback(tmp_path, run_ale):
    answers = tmp_path / "a.json"; answers.write_text(json.dumps({"project.private": "yes"}))
    out = run_ale(["setup", "--force", "--answers", str(answers)], cwd=tmp_path)
    assert out.returncode == 2 and "Traceback" not in out.stderr and "project.private" in out.stderr
    answers.write_text(json.dumps(["not", "an", "object"]))
    out = run_ale(["setup", "--force", "--answers", str(answers)], cwd=tmp_path)
    assert out.returncode == 2 and "Traceback" not in out.stderr


def test_ssh_values_starting_with_dash_are_rejected_and_probe_ends_options(tmp_path):
    assert S.parse_hosts_toml('[hosts.evil]\nssh = "-oProxyCommand=touch x"\n') == [{"name": "evil"}]
    log = tmp_path / "ssh.log"
    _fake(tmp_path, "ssh", 'echo "$@" > "%s"' % log)
    assert S.probe_host("-oProxyCommand=x", {"PATH": str(tmp_path)}) == (False, None)
    assert not log.exists()
    S.probe_host("user@box", {"PATH": str(tmp_path)})
    assert log.read_text().split()[4:6] == ["--", "user@box"]


def test_refuse_secret_skips_paths_and_refuses_known_prefixes(example_roster):
    for qid, value in (("refs.file", "/srv/config/ale/api-token-refs-2026.json"),
                       ("notes.project", "/srv/secrets/password-policy-2026.md")):
        S.refuse_secret(qid, value)
    for value in ("ghp_abcdefgh1234", "github_pat_ABCDEFGH12", "sk-abcdefgh12", "xoxb-12345678",
                  "export GH=ghp_abcdefgh1234"):
        with pytest.raises(S.SecretAnswer):
            S.refuse_secret("notes.project", value)
    S.refuse_secret("notes.project", "check the task-list and disk-usage before submit")
