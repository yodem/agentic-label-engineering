import subprocess

from ale.verify import paths_within, run_acceptance


class Fake:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_all_pass(label_t01, tmp_path):
    got = run_acceptance(label_t01, str(tmp_path))
    assert got["passed"] and [r["ok"] for r in got["results"]] == [True, True] and got["manual"] == []


def test_failure_captured_with_tail(label_t01, tmp_path):
    label_t01["acceptance"][0]["cmd"] = "echo boom; exit 7"
    got = run_acceptance(label_t01, str(tmp_path))
    assert not got["passed"] and got["results"][0]["exit"] == 7 and "boom" in got["results"][0]["tail"]


def test_expected_nonzero_exit(label_t01, tmp_path):
    label_t01["acceptance"][0] = {"id": "A1", "cmd": "exit 3", "expect": "exit:3"}
    assert run_acceptance(label_t01, str(tmp_path))["passed"]


def test_manual_entries_listed_not_run(label_t01, tmp_path):
    label_t01["acceptance"][1] = {"id": "A2", "manual": "Reviewer confirms API shape"}
    got = run_acceptance(label_t01, str(tmp_path))
    assert got["manual"] == ["A2"] and len(got["results"]) == 1


def test_timeout_is_a_failure(label_t01, tmp_path):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    got = run_acceptance(label_t01, str(tmp_path), run=boom)
    assert not got["passed"] and got["results"][0]["exit"] == -1


def test_tail_is_bounded(label_t01, tmp_path):
    got = run_acceptance(label_t01, str(tmp_path), run=lambda *a, **k: Fake(0, "y" * 5000))
    assert len(got["results"][0]["tail"]) == 300


def test_paths_within():
    allowed = ["src/auth/**", "tests/auth/**"]
    assert paths_within(["src/auth/a.py", "src/auth/deep/b.py", "tests/auth/t.py"], allowed) == []
    assert paths_within(["src/auth/a.py", "README.md"], allowed) == ["README.md"]


def test_traversal_and_absolute_paths_are_violations():
    allowed = ["src/auth/**"]
    changed = ["src/auth/a.py", "src/auth/sub/../../../secrets.txt", "/etc/passwd", "../x", "..",
               "src/auth/./b.py", "src/auth/sub/../c.py", "src/auth/../../etc/passwd"]
    assert paths_within(changed, allowed) == ["src/auth/sub/../../../secrets.txt", "/etc/passwd", "../x", "..",
                                              "src/auth/../../etc/passwd"]
