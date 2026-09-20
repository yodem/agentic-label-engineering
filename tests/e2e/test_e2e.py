import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FAKES = os.path.join(ROOT, "tests", "e2e", "fakes")


def make_label(tid, deps):
    return {
        "schema_version": "1.0", "run_id": "e2e", "task_id": tid, "title": "Write out/%s.txt" % tid,
        "labels": {"role": "backend", "model_tier": "cheap", "lane": "inline", "risk": "low", "effort": "S"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "specs/%s.md" % tid, "pointers": [], "allowed_paths": ["out/%s.*" % tid], "depends_on": deps},
        "acceptance": [{"id": "A1", "cmd": "test -f out/%s.txt" % tid, "expect": "exit0"},
                       {"id": "A2", "cmd": "test -s out/%s.txt" % tid, "expect": "exit0"}],
        "provenance": {"lane_reason": "Tiny fixture task, runs inline."},
    }


class Project:
    def __init__(self, tmp_path, tasks):
        self.dir = str(tmp_path / "project")
        self.run_dir = os.path.join(self.dir, ".ale", "runs", "e2e")
        os.makedirs(os.path.join(self.run_dir, "labels"))
        self.roster = os.path.join(self.dir, "roster.json")
        shutil.copy(os.path.join(ROOT, "examples", "roster.json"), self.roster)
        for tid, deps in tasks:
            with open(os.path.join(self.run_dir, "labels", tid + ".json"), "w") as f:
                json.dump(make_label(tid, deps), f)
        with open(os.path.join(self.dir, ".gitignore"), "w") as f:
            f.write(".ale/\n")
        git = ["git", "-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e"]
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.dir, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.dir, check=True)
        subprocess.run(git + ["commit", "-q", "-m", "base"], cwd=self.dir, check=True)
        assert self.ale(0, "init-run") == 0

    def env(self, now):
        env = dict(os.environ, ALE_RUN_DIR=self.run_dir, ALE_ROSTER=self.roster, ALE_NOW=str(now),
                   ALE_BIN="%s -m ale" % sys.executable, PYTHONPATH=ROOT)
        return env

    def ale(self, now, *args):
        return subprocess.run([sys.executable, "-m", "ale"] + list(args), cwd=self.dir, env=self.env(now)).returncode

    def fake(self, now, script, task, agent):
        return subprocess.run([os.path.join(FAKES, script), task, agent], cwd=self.dir, env=self.env(now)).returncode

    def task(self, tid):
        out = subprocess.run([sys.executable, "-m", "ale", "status", "--json"], cwd=self.dir, env=self.env(0),
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)["tasks"][tid]


def test_honest_executor_is_accepted(tmp_path):
    p = Project(tmp_path, [("T1", [])])
    assert p.fake(1, "honest.sh", "T1", "h1") == 0
    assert p.ale(2, "verify", "--task", "T1", "--base", "HEAD") == 0
    assert p.task("T1")["state"] == "accepted"


def test_liar_is_rejected_then_honest_retry_is_accepted(tmp_path):
    p = Project(tmp_path, [("T1", [])])
    assert p.fake(1, "liar.sh", "T1", "liar") == 0
    assert p.ale(2, "verify", "--task", "T1") == 1
    st = p.task("T1")
    assert (st["state"], st["attempt"], st["rejections"]) == ("rejected", 2, 1)
    assert p.fake(3, "honest.sh", "T1", "h1") == 0
    assert p.ale(4, "verify", "--task", "T1") == 0
    assert p.task("T1")["state"] == "accepted"


def test_crashed_executor_is_released_and_successor_sees_progress(tmp_path):
    p = Project(tmp_path, [("T1", [])])
    assert p.fake(0, "crasher.sh", "T1", "c1") == 0
    assert p.ale(100, "watchdog") == 0
    assert p.ale(5000, "watchdog") == 6
    st = p.task("T1")
    assert st["state"] == "released" and st["last_step"] == "half way"
    assert "half way" in open(os.path.join(p.run_dir, "handoff", "T1.c1.md")).read()
    assert p.fake(5001, "honest.sh", "T1", "h1") == 0
    assert p.ale(5002, "verify", "--task", "T1") == 0


def test_strayer_is_rejected_for_path_violation(tmp_path):
    p = Project(tmp_path, [("T1", [])])
    assert p.fake(1, "strayer.sh", "T1", "s1") == 0
    assert p.ale(2, "verify", "--task", "T1", "--base", "HEAD") == 1
    st = p.task("T1")
    assert st["state"] == "rejected" and "path_violation" in st["last_reject_reason"] and "UNRELATED.md" in st["last_reject_reason"]


def test_dependency_order_is_enforced(tmp_path):
    p = Project(tmp_path, [("T1", []), ("T2", ["T1"])])
    assert p.fake(1, "honest.sh", "T2", "early") != 0
    assert p.fake(2, "honest.sh", "T1", "h1") == 0
    assert p.ale(3, "verify", "--task", "T1") == 0
    assert p.fake(4, "honest.sh", "T2", "h2") == 0
    assert p.ale(5, "verify", "--task", "T2") == 0
    assert p.ale(6, "doctor") == 0
