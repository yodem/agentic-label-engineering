"""Fake Jev judges for tests. Nothing here ever executes the real jev-ask.

``write_fake_judge`` creates a script with the jev-ask command-line shape
(``choice|noul QUESTION [OPTIONS...]``, state on stdin). It logs every call as
one JSON line and answers deterministically:

* ``conflict`` mode: every Noul answers 0.9 and every Choice picks the last
  offered option before ``other``. Those answers disagree with the planner's
  usual values, so a leak of any vote into authority would show.
* ``agree`` mode: every Noul answers 0.1 and every Choice picks the first option.
"""

import json
import os
import stat
import sys

_SCRIPT = r'''
import json, sys
log_path, mode = sys.argv[1], sys.argv[2]
kind, question, options = sys.argv[3], sys.argv[4], sys.argv[5:]
state = sys.stdin.read()
with open(log_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"kind": kind, "question": question, "options": options,
                             "state": state}) + "\n")
if kind == "noul":
    print(json.dumps({"type": "noul", "noul": 0.9 if mode == "conflict" else 0.1, "model": "fake-jev"}))
else:
    live = [option for option in options if not option.startswith("other:")] or options
    choice = live[-1] if mode == "conflict" else live[0]
    print(json.dumps({"type": "choice", "choice": choice, "confidence": 0.8,
                      "probabilities": {choice: 0.8}, "model": "fake-jev"}))
'''


def write_fake_judge(directory, mode="conflict"):
    """Return (command, log_path) for a roster ``judge.command``."""
    directory = str(directory)
    script = os.path.join(directory, "fake_jev.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(_SCRIPT)
    log_path = os.path.join(directory, "fake_jev_calls.jsonl")
    return [sys.executable, script, log_path, mode], log_path


def read_calls(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_spawn_bin(directory, verdict="nudge"):
    """An ALE_SPAWN_BIN stand-in: monitors print a verdict, executors do nothing."""
    path = os.path.join(str(directory), "fake-spawn")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!%s\n" % sys.executable)
        handle.write(
            "import json, sys\n"
            "request = json.load(open(sys.argv[1], encoding='utf-8'))\n"
            "if request.get('env', {}).get('ALE_READ_ONLY') == '1':\n"
            "    print('The agent has not reported progress since the lease elapsed.')\n"
            "    print('Verdict: %s')\n" % verdict)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# A two-task plan and the CLI sequence that reaches every judged firing site.

PLAN = """# Shadow judge fixture plan

## Task 1: Add the parser

Write the parser module for the record format.
**Files:** `src/parser.py`
Run: `false`

## Task 2: Import the records

Load the parsed records into the local store.
**Files:** `src/importer.py`
Run: `true`
"""

LANE_REASON = "The planner stays available while this task runs."


def fill_blocks(text):
    """What a planner does by hand after bake: lane, reason, acceptance, monitor."""
    import re

    def edit(match):
        block = json.loads(match.group(1))
        block["labels"]["lane"] = "inline"
        block["lane_reason"] = LANE_REASON
        block["worktree"] = "none"
        block["acceptance"].append({"id": "A2", "cmd": "true", "expect": "exit0"})
        if block["task_id"] == "T2":
            block["assignments"].append({"kind": "monitor", "role": "monitor", "model_tier": "standard",
                                         "executor": "claude-headless", "trigger": "on_breach"})
        return "```ale-label\n%s\n```" % json.dumps(block, indent=1)
    return re.sub(r"```ale-label\n(.*?)\n```", edit, text, flags=re.S)


def setup_repo(base, monkeypatch, judge_default="shadow", mode="conflict"):
    """A git repo with `ale setup --judge <default>` and a fake judge command."""
    import subprocess
    from ale.cli import main

    repo = os.path.join(str(base), "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert main(["setup", "--judge", judge_default]) == 0
    roster_path = os.path.join(repo, ".ale", "roster.json")
    with open(roster_path, encoding="utf-8") as handle:
        roster = json.load(handle)
    command, log_path = write_fake_judge(base, mode)
    roster["judge"]["command"] = command
    with open(roster_path, "w", encoding="utf-8") as handle:
        json.dump(roster, handle)
    monkeypatch.setenv("ALE_SPAWN_BIN", write_spawn_bin(base, "nudge"))
    return repo, roster_path, roster, log_path


def drive_run(repo, roster):
    """bake, fill, init-run, dispatch, reject, fix, fix-of-fix, monitor breach.

    Returns (run_dir, exit_codes). Every command is a real ``ale`` CLI call.
    """
    from ale.cli import main

    plan = os.path.join(repo, "plan.md")
    with open(plan, "w", encoding="utf-8") as handle:
        handle.write(PLAN)
    run_dir = os.path.join(repo, ".ale", "runs", "plan")
    common = ["--run-dir", run_dir, "--roster", roster]
    codes = {}
    codes["bake"] = main(["plan", "bake", plan, "--write", "--roster", roster])
    with open(plan, encoding="utf-8") as handle:
        filled = fill_blocks(handle.read())
    with open(plan, "w", encoding="utf-8") as handle:
        handle.write(filled)
    codes["init"] = main(["init-run", "--plan", plan, "--now", "10"] + common)
    codes["dispatch1"] = main(["dispatch", "--spawn", "--cwd", repo, "--now", "20"] + common)
    codes["claim"] = main(["claim", "--task", "T1", "--agent", "a1", "--now", "30"] + common)
    codes["submit"] = main(["submit", "--task", "T1", "--agent", "a1", "--summary", "parser written",
                            "--now", "40"] + common)
    codes["verify"] = main(["verify", "--task", "T1", "--cwd", repo, "--now", "50"] + common)
    codes["fix"] = main(["fix", "--task", "T1", "--now", "60"] + common)
    codes["claim_fix"] = main(["claim", "--task", "T1.fix1", "--agent", "f1", "--now", "70"] + common)
    codes["submit_fix"] = main(["submit", "--task", "T1.fix1", "--agent", "f1", "--summary", "retried",
                                "--now", "80"] + common)
    codes["verify_fix"] = main(["verify", "--task", "T1.fix1", "--cwd", repo, "--now", "90"] + common)
    codes["fix_of_fix"] = main(["fix", "--task", "T1.fix1", "--now", "95"] + common)
    codes["claim2"] = main(["claim", "--task", "T2", "--agent", "a2", "--now", "100"] + common)
    codes["watchdog"] = main(["watchdog", "--now", "100000"] + common)
    codes["dispatch2"] = main(["dispatch", "--spawn", "--cwd", repo, "--now", "100001"] + common)
    return run_dir, codes
