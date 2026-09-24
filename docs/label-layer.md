# ALE label layer

ALE turns a plan into a typed, append-only task board for multi-agent coding. Labels contain the plan's declared intent. The roster resolves a label's model tier to an executor and model. Events record claims, progress, verification, and lead decisions. No executor can accept its own work.

The `locality` label is `any` by default, or `local` when a task requires planner-machine resources such as a GUI, keychain, or unsynced files.

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

Executors never commit. When verification runs in the task's own worktree it records the exact allowed-change tree without touching the worktree's index. `ale integrate` compares the current allowed changes with that tree before it stages anything, and refuses if they differ, leaving the worktree as it was; reopen and verify the task again after any post-acceptance edit. `ale dispatch --json` prints one JSON object per line, one spawn request per line. `ale dispatch --spawn` starts executor requests concurrently up to the roster's parallel cap, while monitors remain sequential.

`ale dispatch --no-exec` is for a lead who will execute the task manually. It creates the task worktree and branch and records the worktree on the task without launching an executor. `ale verify --reject "reason"` records a lead's manual rejection of a submitted task, including the reason, without running the acceptance commands. The verifier can ignore ALE's `.ale-setup-done` setup marker when checking changed paths.

After `ale init-run`, `context.allowed_paths` is frozen for that run. If the scope must change, use a focused fix task rather than editing the initialized label. `ale reopen` returns a rejected or accepted-but-unintegrated task to verification after the lead addresses the failure or makes a post-review change; an integrated task cannot be reopened. `ale fix` creates a work task for failed acceptance checks. A parent can have at most two fix tasks, and a fix task cannot create another fix task. After that, escalate the decision to the lead.

Binding an in-session subagent before it is spawned remains a deferred design question. Dispatch currently creates the configured per-task worktree for `claude-subagent` assignments, but does not bind a live in-session subagent as part of dispatch.

Use `ale timeline [--task TASK] [--json]` for the event stream. Use `ale meta [--json]` for per-task, per-agent, and run usage metadata; add `--csv` for CSV output.

In shadow mode, accepting a task adjudicates each non-null Jev vote against the accepted label, once per decision: an agreeing vote is recorded as `agreement_then_accepted`, a disagreeing one as `disagreement_then_accepted` with Jev's choice in `jev_choice`. For a disagreement `ale verify` also prints the `ale adjudicate` command, so the lead can side with Jev; that explicit adjudication replaces the automatic one. A vote on a field the accepted label leaves unset records nothing and stays pending. `ale judge-stats` reports pending adjudications for accepted tasks, and a decision's bar cannot be met while any remain.

The runner's normal loop is:

```text
write plan -> bake --write -> fill gaps -> ale run
                                            dispatch -> verify -> integrate
                                            rejected -> fix -> verify parent
                                            report -> status / timeline / meta
```

## What the plan parser reads

`ale plan bake` reads a plan with a deliberately literal parser. It never guesses, so a
task can read as complete to a human and still arrive with no files, no commands, and no
dependencies. These are the only forms it recognizes.

| Field | Recognized | Not recognized |
| --- | --- | --- |
| Task boundary | A heading `## Task 3: Title`, `### Step 4. Title`, or `#### Phase 5 Title`, two to four hashes. Failing that, top-level `3. Title` numbered items, then `- [ ] Title` checkboxes. At least two of one kind are required. | A bold line, a bare heading without `Task`, `Step`, or `Phase`, or a single task. |
| Task ID | The heading's own number, so `## Task 12` is `T12`. List forms number by position. | Any other identifier written in the text. |
| Files | Backticked paths on a line that starts with `**Files:**`, `Create:`, `Modify:`, or `Test:`. A path needs a `/` or a `.` and no spaces. A trailing `:10-40` line range is stripped. | Paths in prose, in a table, in a fenced block, or on a continuation line. |
| Commands | `Run: ` followed by a backticked command, anywhere in the task body. Also the first non-empty line of a `bash` or `sh` fence whose preceding line contains `Run` or `Verify`. | Bare backticked commands, and `git add`, `git commit`, or `git push`, which are dropped because executors never commit. |
| Dependencies | `depends on Task 4`, `after Task 4`, and `Consumes: ... Task 4`. | `depends on: Task 4`, `depends on Tasks 4, 5`, and `depends on T4`, all of which yield nothing. |

Write one dependency phrase per dependency: `Depends on Task 4. Depends on Task 5.` A
dependency on a task the plan does not define, or on the task itself, is dropped. A
forward reference is allowed: a task may depend on one defined later in the file.

Whatever the parser misses can still be written by hand into the task's `ale-label` block,
which is authoritative. Read the baked block before starting a run.

## Acceptance checks

`expect` is exit-code only. It is `exit0` or `exit:N`, and ALE compares the process exit
code and nothing else. It never matches output text, so express the intent as an exit
code:

| Intent | Write |
| --- | --- |
| A command must succeed | `{"cmd": "pytest -q", "expect": "exit0"}` |
| A pattern must be absent | `{"cmd": "grep -rn TODO src/", "expect": "exit:1"}`, because grep exits 1 when it finds nothing |
| A value must match | `{"cmd": "test \"$(cat VERSION)\" = 0.2.0", "expect": "exit0"}` |

Each command runs through the shell in the task's working directory with a 600 second
timeout. The last 300 characters of combined stdout and stderr are kept as evidence. An
entry shaped `{"id": "A3", "manual": "..."}` is recorded for the lead and is not run.

## Label fields

Labels may include the `sub` and `phase` axes, which resolve to an agent
definition. See [docs/agents.md](agents.md) for the taxonomy, catalog lookup,
resolution order, rule enforcement, and agent analytics.

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

Token budgets and estimated input cost use billable tokens: `max(0, input_tokens - cache_read_input_tokens) + output_tokens`. Cached reads are excluded from the budget and input-cost calculation; cache writes remain included in input tokens.

### Worktrees

`context.worktree.mode` defaults to `per_task` when the task has allowed paths. Dispatch creates `run/wt/TASK` and uses branch `ale/RUN/TASK`; the request's working directory points there. A fix task reuses its parent's worktree. Use `none` only for tasks that do not write project files. Use `shared` only with an explicit reason. Write tasks should use per-task worktrees so concurrent changes cannot overlap accidentally.

Per-task worktrees can run setup commands before the executor starts. Set `context.worktree.setup` on a label to override roster-level `worktree_setup_defaults`; otherwise the roster defaults apply. Commands run in the worktree with `ALE_WORKTREE` and `ALE_CHECKOUT` set, and each has a 600-second timeout. Successful setup writes `.ale-setup-done`, so retries do not repeat it. List generated paths in `context.worktree.setup_outputs`: integrate excludes them, along with the marker, from commits. For example, a Node project can reuse installed modules:

```json
{
  "worktree_setup_defaults": ["ln -s \"$ALE_CHECKOUT/node_modules\" node_modules"],
  "context": {
    "worktree": {
      "setup_outputs": ["node_modules"]
    }
  }
}
```

Alternatively, use `"setup": ["npm ci"]` on the label (or `worktree_setup_defaults` on the roster) and list `"node_modules"` under `setup_outputs`.

`ale integrate --task TASK` is lead-side only and requires `accepted`. It merges the recorded task branch into the base checkout and removes the worktree after a successful merge. A conflict is aborted and leaves the worktree in place. When the checkout is dirty the error names it and the first five uncommitted paths.

Accepted work is integrated before anything that depends on it is dispatched. `ale run` integrates every task that became accepted during a cycle before that cycle dispatches, and `ale dispatch` refuses to spawn a per-task-worktree task whose `depends_on` names a task that is accepted but not yet integrated, printing `holding T2: dependency T1 is accepted but not integrated` on stderr. A per-task worktree is therefore branched from the checkout's HEAD as it stands at dispatch time, which contains every dependency merged so far. Without this, a dependent branches from a base that lacks its dependency's files, recreates them, and its own integration fails on conflicts.

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

A value already in a block is authoritative and survives a re-bake, so `bake(bake(x))` equals `bake(x)` even for a block you edited by hand. The rules fill only what a block does not already carry, and a preserved value's provenance records `by: block`. To have a label derived from the plan text again, delete that value, or the whole block, before re-baking.

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
