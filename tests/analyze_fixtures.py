"""Build ALE run directories in code for the ``ale analyze`` tests.

A run is labels (one JSON file per task) plus a list of events. ``good_task`` produces the
events of a task that passes every check; tests break one input on purpose to make a check fail.
"""
import json
import os

HOUR = 3600.0


def label(task_id, run_id, role="backend", effort="M", risk="medium", model_tier="standard",
          worktree_mode="per_task", executor="codex", model="gpt-x", mode="headless"):
    return {
        "schema_version": "1.0", "task_id": task_id, "run_id": run_id, "title": "Task %s" % task_id,
        "labels": {"role": role, "effort": effort, "risk": risk, "model_tier": model_tier,
                   "phase": "implement", "lane": "pane", "locality": "any"},
        "context": {"allowed_paths": ["src/%s/*" % task_id.lower()], "depends_on": [],
                    "worktree": {"mode": worktree_mode, "base": None, "branch": None}},
        "routing": {"executor": executor, "model": model, "mode": mode},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                       {"id": "A2", "cmd": "true", "expect": "exit0"}],
    }


def ev(kind, run_id, ts, task_id=None, agent_id=None, attempt=1, **extra):
    event = {"schema_version": "1.0", "type": kind, "run_id": run_id, "ts": float(ts),
             "task_id": task_id, "agent_id": agent_id, "attempt": attempt}
    event.update(extra)
    return event


def evidence(files, passed=True):
    return {"passed": passed, "files": files, "results": [], "path_violations": []}


def good_task(run_id, task_id, t0, run_dir, files=("src/a.py",), executor="codex-exec",
              usage=True, worktree=None, register=False):
    """A dispatched, headless task: claimed, submitted, accepted on its first verify, integrated."""
    agent = "%s-executor-backend-1" % task_id
    spawn = {"agent_id_minted": agent, "assignment_kind": "executor", "executor": executor,
             "model": "gpt-x", "trigger_instance": "ready",
             "worktree": worktree or os.path.join(run_dir, "wt", task_id),
             "branch": "ale/%s/%s" % (run_id, task_id), "base_commit": "0" * 40}
    if register:
        spawn.update(executor="external", model=None)
    else:
        spawn["pane"] = "pane-1"
    events = [
        ev("spawned", run_id, t0, task_id, **spawn),
        ev("claimed", run_id, t0 + 10, task_id, agent),
        ev("heartbeat", run_id, t0 + 20, task_id, agent, step="work"),
        ev("submitted", run_id, t0 + 30, task_id, agent, summary="done"),
        ev("verified", run_id, t0 + 40, task_id, evidence=evidence(list(files))),
        ev("accepted", run_id, t0 + 40, task_id, evidence=evidence(list(files))),
        ev("integrated", run_id, t0 + 50, task_id, commit="c" * 40, files=list(files)),
    ]
    if usage:
        events.insert(4, ev("usage", run_id, t0 + 31, task_id, agent, **{
            "gen_ai.request.model": "gpt-x", "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.output_tokens": 200, "usage_source": "adapter"}))
    return events


def run_header(run_id, t0, task_ids, roster_hash="abcd1234"):
    events = [ev("run_started", run_id, t0, attempt=None)]
    for task_id in task_ids:
        events.append(ev("labeled", run_id, t0, task_id, labels={}, roster_hash=roster_hash))
    return events


def write_run(base, run_id, labels, events, raw_lines=(), dir_name=None):
    """Write ``<base>/.ale/runs/<dir_name or run_id>`` and return its absolute path."""
    run_dir = os.path.join(str(base), ".ale", "runs", dir_name or run_id)
    os.makedirs(os.path.join(run_dir, "labels"), exist_ok=True)
    for item in labels:
        with open(os.path.join(run_dir, "labels", item["task_id"] + ".json"), "w") as handle:
            json.dump(item, handle)
    with open(os.path.join(run_dir, "events.jsonl"), "w") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        for line in raw_lines:
            handle.write(line + "\n")
    return os.path.abspath(run_dir)


def run_dir_for(base, run_id):
    return os.path.abspath(os.path.join(str(base), ".ale", "runs", run_id))


def good_run(base, run_id, t0, n_tasks=3, dir_name=None, **task_kwargs):
    """A finished run of ``n_tasks`` tasks that passes every check."""
    run_dir = run_dir_for(base, dir_name or run_id)
    task_ids = ["T%d" % (i + 1) for i in range(n_tasks)]
    events = run_header(run_id, t0, task_ids)
    for index, task_id in enumerate(task_ids):
        events.extend(good_task(run_id, task_id, t0 + 100 * (index + 1), run_dir, **task_kwargs))
    return write_run(base, run_id, [label(t, run_id) for t in task_ids], events, dir_name=dir_name)


def entry(run_dir, run_id, roster_hash="abcd1234", ale_version="0.4.0"):
    return {"ts": 0.0, "run_id": run_id, "run_dir": run_dir,
            "repo_root": os.path.dirname(os.path.dirname(os.path.dirname(run_dir))),
            "ale_version": ale_version, "roster_hash": roster_hash}
