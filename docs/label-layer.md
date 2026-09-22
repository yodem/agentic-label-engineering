# ALE label layer

ALE turns a plan into a typed, append-only task board for multi-agent coding. Labels contain the plan's declared intent. The roster resolves a label's model tier to an executor and model. Events record claims, progress, verification, and lead decisions. No executor can accept its own work.

## Flow

From any Git repository, initialize the local roster once, then use the label-layer skill or the manual commands:

```sh
ale setup
/label-layer PLAN.md
```

Alternatively, bake and run by hand with `ale plan bake PLAN.md --write` followed by `ale run PLAN.md`. The runner checks that blocks have no gaps before starting. It initializes once, resumes from the event log on later invocations, dispatches ready assignments synchronously, verifies submissions, integrates accepted tasks, and creates focused fixes for rejections. It stops after two fix tasks for a rejected task, on monitor escalation, or after the cycle limit.

Plan baking does not call an external judge by default. Add `--judge` to opt in; this sends task text to the configured external API. `--no-judge` remains available for compatibility.

Defaults use the nearest `.ale/roster.json` and `.ale/runs/<run-id>` under the repository's `.ale` directory. Run IDs come from `--run-id`, plan provenance, or the plan filename. Existing-run commands resolve to the last initialized run recorded in `.ale/runs/current`; pass `--run-id` to select another run. Explicit flags and `ALE_ROSTER` or `ALE_RUN_DIR` override defaults.

Dispatch creates the default per-task worktree for a write task. Executors claim, send heartbeats, submit, and run the label's acceptance through `ale verify`. After a fix is accepted, the runner verifies the parent's full acceptance again. Integrate an accepted branch with the runner's `ale integrate --task TASK` action.

Executors never commit. `ale integrate` checks and commits the executor's allowed changes before merging them. `ale dispatch --json` prints one JSON object per line, one spawn request per line.

Use `ale timeline [--task TASK] [--json]` for the event stream. Use `ale meta [--json]` for per-task, per-agent, and run usage metadata; add `--csv` for CSV output.

The runner's normal loop is:

```text
write plan -> bake --write -> fill gaps -> ale run
                                            dispatch -> verify -> integrate
                                            rejected -> fix -> verify parent
                                            report -> status / timeline / meta
```

## Label fields

Each label is a JSON object in an `ale-label` fenced block or in `run/labels/TASK.json`.

| Field | Meaning |
| --- | --- |
| `schema_version` | Label schema version, currently `1.0`. |
| `run_id` | Safe identifier shared by the run's labels. |
| `task_id` | Safe task identifier. |
| `title` | Human-readable task goal. |
| `labels.role` | Work vocabulary such as `backend`, `frontend`, `test`, or `docs`. |
| `labels.model_tier` | Capability tier. It names a tier, not a model. |
| `labels.risk` | Risk vocabulary used for review and monitoring. |
| `labels.effort` | Expected effort, such as `S`, `M`, or `L`. |
| `labels.lane` | `inline`, `workflow`, or `pane`; the planner supplies it. |
| `routing` | Resolved executor, model, and provenance. The roster supplies the model. |
| `context.spec_path` | Task specification path. |
| `context.pointers` | Relevant source or documentation pointers. |
| `context.allowed_paths` | Files and globs the task may modify. |
| `context.depends_on` | Task IDs that must be accepted first. |
| `context.worktree` | Isolation policy: `per_task`, `shared`, or `none`, plus branch, base, and reason. |
| `acceptance` | Two to five command or manual checks. `ale verify` runs the commands. |
| `watch` | Heartbeat, stuck, duration, token, and attempt limits. |
| `assignments` | Executor, monitor, or fixer assignments. Exactly one assignment is the executor. |
| `assignments[].trigger` | `ready`, `on_breach`, `on_submit`, or `milestone`. |
| `fixes` | Parent task ID on a generated fix label. |
| `provenance` | Rule, planner, or judge evidence, including `lane_reason`. |

### Worktrees

`context.worktree.mode` defaults to `per_task` when the task has allowed paths. Dispatch creates `run/wt/TASK` and uses branch `ale/RUN/TASK`; the request's working directory points there. A fix task reuses its parent's worktree. Use `none` only for tasks that do not write project files. Use `shared` only with an explicit reason. Write tasks should use per-task worktrees so concurrent changes cannot overlap accidentally.

`ale integrate --task TASK` is lead-side only and requires `accepted`. It merges the recorded task branch into the base checkout and removes the worktree after a successful merge. A conflict is aborted and leaves the worktree in place.

## Assignments and monitoring

An assignment has `kind`, `role`, `model_tier`, `executor`, and `trigger`. The executor runs when the task is ready. A monitor is read-only and is useful for high-risk or unattended tasks; `on_breach` starts it when the watchdog reports a breach. `on_submit` waits for submission, and `milestone` waits until all tasks with the milestone have submitted or reached a later state. Fixers are created by `ale fix` and are not dispatched as ordinary monitor work.

Monitor prompts contain the triggering breach and heartbeat step, acceptance commands to run read-only, the handoff path, and the worktree. Monitors return `continue`, `nudge`, `fix`, or `escalate` with one line of reasoning; escalation stops the run, and a `fix` verdict can only trigger `ale fix` for a monitor assignment selected by the human in the plan. Executor prompts use `$ALE_BIN` for ALE commands. Spawned children receive `ALE_BIN` (default `python3 -m ale`) and the plugin root on `PYTHONPATH`.

## Derived status

`ale status --json` derives these labels from events and current labels:

| Key | Meaning |
| --- | --- |
| `state` | Planned, ready, claimed, working, submitted, fixing, accepted, or another derived state. |
| `blocked_by` | Missing or unaccepted dependencies. |
| `assignees` | Agents from claims and lead spawn records. |
| `attempt` | Current verification attempt. |
| `breaches` | Breach names recorded for the task. |
| `lease_expires_ts` | Computed heartbeat lease deadline. |
| `last_step` | Latest executor progress step. |
| `last_verdict` | Latest monitor verdict, minted monitor ID, and captured response text. |
| `fixes` | Fix task IDs belonging to this task. |
| `fixed_by` | Accepted fix task IDs. |

## Events

The board is append-only. Core events are `run_started`, `run_finished`, `labeled`, `label_vote`, `relabeled`, `adjudicated`, `dispatched`, `claimed`, `heartbeat`, `note`, `input_required`, `input_answered`, `relayed`, `submitted`, `verified`, `accepted`, `rejected`, `failed`, `canceled`, `lease_expired`, `released`, `breach`, `monitor_verdict`, `usage`, and `decision`.

The label-layer events are `task_added`, `label_changed`, `label_removed`, `spawned`, and `integrated`. `monitor_verdict` is also lead-authority: it records the minted monitor ID, one of the four verdicts, and up to 1500 characters of the monitor response. Lead-authority events have no `agent_id`; executor-authored label changes are ignored. `task_added` records the label filename rather than embedding the label body, keeping event lines below the 4096-byte cap.

## Compact baked block

`ale plan bake` writes only planner-facing fields into each block. Provenance and votes go to `PLAN.md.ale-provenance.json`; `ale plan compile` rebuilds the full label.

````markdown
```ale-label
{
 "task_id": "T1",
 "title": "Add a health endpoint",
 "labels": {"role": "backend", "model_tier": "standard", "risk": "low", "effort": "M", "lane": "pane"},
 "lane_reason": "Unattended implementation needs a pane.",
 "acceptance": [{"id": "A1", "cmd": "pytest -q", "expect": "exit0"}],
 "allowed_paths": ["src/health.py"],
 "depends_on": [],
 "worktree": "per_task",
 "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard", "executor": null, "trigger": "ready"}]
}
```
````

## Herdr sidebar

Add the ALE token to a row in your Herdr configuration's `[ui.sidebar.agents]` table:

```toml
{ token = "$ale" }
```

Run the publishing edge with `ALE_HERDR=1`. The token shows the task, role, tier, state, attempt, and stuck warning. Without that environment variable, ALE does not publish Herdr metadata.
