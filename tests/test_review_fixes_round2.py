import os

from ale import analyze as A
from ale import evalledger as EL
from ale import runindex as RI
from analyze_fixtures import entry, good_run

T0 = 1_700_000_000.0


def test_same_named_repositories_have_distinct_run_identities(tmp_path, monkeypatch):
    runs = [A.load_run(entry(good_run(tmp_path / side / "project", "plan", T0,
                                    n_tasks=1), "plan")) for side in ("a", "b")]
    assert {run["run_key"] for run in runs} == {os.path.realpath(run["run_dir"]) for run in runs}
    alias = tmp_path / "alias"
    alias.symlink_to(runs[0]["run_dir"], target_is_directory=True)
    assert A.run_key(str(alias)) == runs[0]["run_key"]
    captured = []
    original = A.summarize_shadow

    def capture(votes, outcomes, adjudications, bar, accepted):
        captured.extend(accepted)
        return original(votes, outcomes, adjudications, bar, accepted)

    monkeypatch.setattr(A, "summarize_shadow", capture)
    report = A.evaluate(runs, A.load_thresholds(), T0 + 3600, None)
    assert len(captured) == 2
    cases = report["case_results"]
    assert len({(c["case_id"], c["evaluator"]) for c in cases}) == len(cases)
    assert EL.append_changed_rows(cases, home=str(tmp_path / "home")) == len(cases)
    assert EL.append_changed_rows(cases, home=str(tmp_path / "home")) == 0
    assert len(EL.latest_by_case(EL.read_rows(str(tmp_path / "home")))) == len(cases)
