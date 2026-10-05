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

Defaults use the nearest `.ale/roster.json` and `.ale/runs/<run-id>` under the repository's `.ale` directory. Run IDs come from `--run-id`, plan provenance (the sidecar's `run_id`, so an existing run is never renamed), the name of the run directory when the plan sits in `.ale/runs/<id>/` (`plan.md` there bakes run `<id>`, not `plan`), or the plan filename. `ale init-run --plan` records which rule applied as `run_id_from` (`explicit`, `provenance`, `run-dir`, `plan-stem`) and the plan's absolute path as `plan_path` on `run_started`. Existing-run commands resolve to the last initialized run recorded in `.ale/runs/current`; pass `--run-id` to select another run. Explicit flags and `ALE_ROSTER` or `ALE_RUN_DIR` override defaults.

Dispatch creates the default per-task worktree for a write task. `ale claim` refuses (exit 1) a task that has no ALE worktree to work in: its effective worktree mode (dispatch's own rule, so `allowed_paths` with no worktree block is `per_task`) is not `none` or `shared`, and no ALE `spawned` event for the task, in any attempt, recorded a `worktree` that still exists on disk or a `remote_worktree`. Releases do not matter, so rework after a rejection or a release passes in the same worktree. The refusal says "ALE has not spawned it", names a recorded worktree that is gone, and prints the remedy on its own line, scoped to the one task with shell-quoted paths and `--roster` when the claim had one or `ALE_ROSTER` is set: `ale dispatch --run-dir <run> --cwd <repo> [--roster <path>] --task <T> --no-exec`, which records the spawn and creates the worktree and launches nothing (it also works for a rejected task that was first claimed outside the worktree, and for a remote-routed task). A gated claim prints `claimed <T> in ALE worktree <path>` on stderr (`host:path` for a remote worktree) and a warning naming the path when the current directory is not inside a local worktree; that is never a refusal. A lead that must work outside ALE on purpose claims with `--no-worktree --reason "<why>"` (at least 10 characters; either flag alone exits 2); inside the gate the claim then carries `deviation: {"code": "worktree-outside-ale", "reason": "<why>"}`, even when a worktree exists, and `ale analyze` counts it. For `none` and `shared` tasks the flag is accepted and no deviation is written. A claim that loses the race still exits 3. A claim by the task's current owner while the task is `claimed` or `working` (an `ale-exec` launch claims for the agent before the harness starts, and the executor claims again) prints `already claimed by <agent>` on stderr and exits 0 without a new `claimed` event; any other agent still gets exit 3. `ale dispatch --task T` (repeatable) limits a dispatch to the named tasks, so the remedy does not mark other due tasks as spawned; the roster's parallel cap and the path-overlap check then apply to the named tasks alone, one selection pass reports every named task it did not dispatch on stderr as `not dispatched: <T>: <reason>` (held, `overlapping paths with <id>`, `parallel cap <n>`, or nothing due) and still exits 0, saved in-session requests of other tasks are not reprinted, and an unknown task exits 2. A spawn bin that cannot run (missing or not executable) releases the spawn with `spawn failed: cannot run spawn bin ...` and dispatch exits 1. Executors claim, send heartbeats, submit, and run the label's acceptance through `ale verify`. After a fix is accepted, the runner verifies the parent's full acceptance again. Integrate an accepted branch with the runner's `ale integrate --task TASK` action.

Executors never commit. Verification in the task's own worktree records the changed files in evidence, checks them against `allowed_paths` and `deny_paths`, then pins and commits the validated tree as `ale: <task> <title>`, recording `evidence.commit`. A path violation is rejected before any commit or acceptance. `ale integrate` merges that committed tip after checking that the tip's tree still equals `evidence.tree` and that no uncommitted task changes remain, and refuses otherwise (reopen and verify again). An acceptance recorded without a commit (verified outside the worktree, or by an older ALE) is staged and committed at integrate, as before. `ale dispatch --json` prints one JSON object per line, one spawn request per line. `ale dispatch --spawn` starts executor requests concurrently up to the roster's parallel cap, while monitors remain sequential.

The file list is the task's changes since its base (so work an executor already committed still counts), together with the uncommitted and untracked changes against HEAD; every task path that differs from HEAD is then committed. The base is `git merge-base HEAD <base_ref>` when the spawn's `base_ref` resolves, so an executor that merged or rebased onto the updated branch is not blamed for upstream files; else the `diff_base` its `spawned` event recorded, or a later `restacked` event's `new_base`. The merge-base is skipped when it is older than that recorded base (a stacked task, or a label with its own `worktree.base`). So a stacked task that merges `base_ref` before its parent is integrated is still diffed from its stack base, and the merged upstream files count as its own; restack it (`ale restack`) instead of merging. Without either, verify diffs against HEAD only. An accept that finds no changed files for a task whose `worktree.mode` is not `none` writes a lead `note` naming what was compared (`verify: no changed files found against base <sha>`, or that no diff ran because verification happened outside the task worktree).

`ale dispatch --no-exec` is for a lead who will execute the task manually. It creates the task worktree and branch and records the worktree on the task without launching an executor. Its `spawned` event carries `no_exec: true` and names the lead (`executor` and `agent_id_minted` are `lead`, `model` is null), never the routed executor or a minted executor agent id; `ale analyze` reads it as in-session lead work. `ale verify --reject "reason"` records a lead's manual rejection of a submitted task, including the reason, without running the acceptance commands. The verifier can ignore ALE's `.ale-setup-done` setup marker when checking changed paths.

`ale relabel --task T --field F --value V --reason "…"` changes a label (`role`, `model_tier`, `risk`, `effort`, `locality`) or the task's `assignments` or `acceptance` (`--json`), and also rewrites that task's `ale-label` block in the plan recorded as `plan_path`, so `ale plan route` and the labels table read the new value. The block is rewritten the way `ale plan bake` writes one (a complete block through bake's renderer, with its `route` view recomputed from the roster; a partial block as bake's compact JSON); a label field lands in the block's `labels`, `assignments` and `acceptance` at its top level, and the rest of the plan file, its newline style included, is kept byte for byte. It writes only when the plan holds exactly one parseable block for the task and the block's current value equals the value relabeled from; the `note` then reads `relabel: plan block for <T> updated in <plan>; re-approve the plan`, because the plan changed after approval. Otherwise it writes nothing and the `note` reads `relabel: plan block for <T> not updated: <why>` (no `plan_path` recorded, plan file missing or unreadable, no block or more than one, blocks that do not parse, or a value that differs, such as a hand edit). A sync problem never changes the relabel's exit code. Relabeling `model_tier` or `role` also moves the task's executor assignments that follow the label, in `labels/<T>.json` (with a second `label_changed` event, field `assignments`) and in the plan block, so `ale plan route` and dispatch pick the new tier or role: an assignment follows when its `kind` is `executor` (the default), its own value of that field equals the value relabeled from, and it pins no `model` and no `pin_reason`; an assignment that pins a harness (`executor`) follows only when the roster has a row for that harness at the new value, else it stays and a `note` (and stderr) says `relabel: assignment pinned to <harness> has no <value> row; relabel assignments to move it`. Monitor assignments, pinned assignments and a deliberate split (an assignment whose value already differed from the label's) stay as they are; relabel `assignments` to change those. Every relabel except `acceptance` also recomputes the label's `routing` (harness, model, mode, host) from the roster, keeping its agent, so `ale analyze` and the timeline read the routing dispatch spawns. A relabel to the value the task already has changes nothing and writes no note. The plan is written through a symlink, so the link survives, and keeps its file mode.

After `ale init-run`, never edit a label file by hand. When a task's scope must change, run `ale rescope --task T [--add-path GLOB]… [--remove-path GLOB]… [--add-dep TASK]… [--remove-dep TASK]… --reason "…"`. It records one lead-authored `label_changed` event per changed field (`context.allowed_paths`, `context.depends_on`, with `old`, `new` and `reason`), rewrites the task's label file to match, and adds a line to `decisions.md`. It refuses, without writing anything, a change that would create a dependency cycle, an unknown dependency, an overlap with an independent task's paths, an empty `allowed_paths` or an otherwise invalid label set (exit 1), and a terminal or integrated task (reopen it first). A task that has not started (planned, ready, released, rejected) may change freely; once started, only `--add-path` is allowed, since widening cannot turn files the executor already wrote into violations. A stacked task that has spawned keeps its dependencies. Patterns are normalized (`./x` → `x`); a bare directory (`lib/`) or a path leaving the repository exits 2. Only problems the change itself introduces block it. Removing a path or dependency that is not there, or passing nothing to change, exits 2. `ale reopen` returns a rejected or accepted-but-unintegrated task to verification after the lead addresses the failure or makes a post-review change; an integrated task cannot be reopened. `ale fix` creates a work task for failed acceptance checks. A parent can have at most two fix tasks, and a fix task cannot create another fix task. After that, escalate the decision to the lead.

For an in-session task, dispatch creates the per-task worktree, records `spawned` (with `mode: in-session` and the worktree), saves the request as `requests/<agent>.in-session.json` and prints it; the lead starts the subagent. Every later dispatch prints that request again while the task is still unclaimed, without a second `spawned` event, so a lead that lost its session can recover the task.

## Harness, mode and host

`ale/harness.py` decides these by code; `ale plan route <plan> --task T [--lane L] --json` prints the result (`harness`, `model`, `mode`, `host`, `resolved_from`).

| Input | Rule |
| --- | --- |
| Harness | The executor assignment's `executor` if set (a pin), else the roster row for `(role, model_tier)`. Legacy ids normalize: `claude-headless`, `claude-subagent`, `claude_code`, `herdr-pane` → `claude`; `codex-exec` → `codex`; `pi-print` → `pi`. Roster `harnesses` adds or overrides entries (`headless` argv with `{model}` and `{prompt}`, `usage_from`, `herdr_kind`, `in_session`, `family`). |
| Model | `assignments[0].model` when pinned (requires a non-empty `pin_reason`), else the roster row. The model stays owned by the roster: re-baking follows roster changes for unpinned tasks. |
| Mode | A legacy id fixes its mode (`claude-headless` stays headless). Otherwise: `pane` lane → `pane`; `inline`/`workflow` → `in-session` when the harness can run in-session and the model is Anthropic (or of unknown family), else `headless`. A non-Anthropic model never runs in-session. |
| Host | `in-session`, locality `local`, or no roster `remote_host` → `local`; otherwise the `remote_host` name. Remote headless work runs as a pane on that host. |

Baked plan blocks show a derived `route` (`harness`, `model`, `mode`). Bake rewrites it on every run and never reads it back; when only `route` changed, bake patches that key and keeps the rest of a hand-formatted block byte-for-byte. Dispatch requests carry `executor` (the spawn id `bin/ale-spawn` understands), `harness`, `mode`, `host`, `family` (for cross-family review gates), `argv`, `usage_from` and `herdr_kind`. `bin/ale-spawn` switches on `mode`; a headless request for a remote host exits 2. A remote task needs `ALE_REMOTE_WORKTREE_<TASK>` (or `ALE_REMOTE_WORKTREE` when it is the only remote task in the dispatch); without one, dispatch records one `released` with reason `remote worktree not provisioned`. A remote task gets no local worktree: its `spawned` event carries `host` and `remote_worktree`, the local launcher runs from the project checkout, and the round trip's fetch-back registers the local copy (`ale register-worktree`) before `ale verify`; until then `ale verify` refuses the task (exit 1) rather than run acceptance in the lead's checkout. The request carries `remote_base`, the commit the remote worktree must start from (a stacked child's is its parent's accepted commit), and worktree setup commands are the remote host's job. Non-claude harnesses do not receive the Claude-only harness sections of an agent definition.

## Deep references

When a task's agent has a refs entry and runs on the local host, dispatch runs its `ck items get` or `trove items get` commands (no shell, 30 s timeout; any other command is refused, see `refs.allowed_command`) and writes `<run-dir>/refs/<task>.md`, whose first line is `ALE-REFS-TOKEN: <8 hex>`, and records `refs_fetched`. The prompt section is `Deep reference (required: read before you edit)` with the prefetched path and the acknowledgement command `$ALE_BIN refs-ack --token <token> --summary "…"`. Lookup order: `<role>/<sub>`, `<role>/<name>`, the name without its role prefix, `<role>/_default` for default agents, then `<role>`. The hook records `refs_read` (`via: hook`) when an executor runs `ck items get` or `trove items get`, or reads the prefetched file; `refs-ack` records `via: ack` with `token_ok`. `ale verify` reports, counting reads made since the task's latest fetch whatever the attempt (so a reopened task keeps a read from an earlier attempt), `refs_read: true|false|null` (null when the task has no refs) and `refs_token_mismatch`; it never changes the verify outcome.

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
| Task boundary | A heading `## Task 3: Title`, `### Step 4. Title`, `#### Phase 5 Title`, or `### Task T2: Title`, two to four hashes. Failing that, top-level `3. Title` numbered items, then `- [ ] Title` checkboxes. At least two of one kind are required. | A bold line, a bare heading without `Task`, `Step`, or `Phase`, or a single task. |
| Task ID | The heading's own id: `## Task 12` is `T12`; a letter prefix of up to three letters is kept and upper-cased, so `### Task T2` is `T2` and `### Task p1a` is `P1a`. List forms number by position. | Any other identifier written in the text. |
| Files | Backticked paths on a line that starts with `**Files:**`, `Files:`, `Create:`, `Modify:`, or `Test:`, optionally indented and after a `- ` or `* ` bullet (the writing-plans template lists `- Create:` lines under a bare `**Files:**` line). A path needs a `/` or a `.` and no spaces. A trailing `:10-40` line range is stripped. | Paths in prose, in a table, in a fenced block, or on a continuation line. |
| Commands | `Run: ` followed by a backticked command, anywhere in the task body. Also the first non-empty line of a `bash` or `sh` fence whose preceding line contains `Run` or `Verify`. | Bare backticked commands, and `git add`, `git commit`, or `git push`, which are dropped because executors never commit. |
| Dependencies | `depends on Task 4`, `after Task 4`, and `Consumes: ... Task 4`, with the same id forms (`after Task T2`). | `depends on: Task 4`, `depends on Tasks 4, 5`, and `depends on T4`, all of which yield nothing. |

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

`context.worktree.mode` defaults to `per_task` when the task has allowed paths. Dispatch creates `run/wt/TASK` and uses branch `ale/RUN/TASK`; the request's working directory points there. A fix task reuses its parent's worktree. Use `none` only for tasks that do not write project files. Use `shared` only with an explicit reason.

A `none` is deliberate only when it carries a reason: `"worktree": {"mode": "none", "worktree_reason": "<why>"}`. A bare `"none"` is treated as derived by an earlier bake (bake once set the mode from the paths it found in the task body, so blocks whose `allowed_paths` were filled in later kept `none`). When a block has non-empty `allowed_paths` and a derived `none`, `ale plan bake` (with or without `--write`) prints `warning: <T>: allowed_paths set but worktree.mode is none (derived at an earlier bake); baking per_task. Set worktree_reason to keep none.` on stderr and bakes `per_task`; `ale init-run --plan` (and `ale plan compile`) apply the same rule to the label files only, print the same warning and, at init-run, add a `decisions.md` line naming the task. The plan file is not rewritten there, so an approved plan's sha256 does not change. A block with `allowed_paths` and no `worktree` key bakes `per_task` with `phase: implement`, silently. A deliberate `none` is kept, reason included, with no warning. Write tasks should use per-task worktrees so concurrent changes cannot overlap accidentally.

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

`ale integrate --task TASK` is lead-side only and requires `accepted`. It merges the recorded task branch into the base checkout and removes the worktree after a successful merge. A `none` or `shared` task has no branch: integrating it records an `integrated` event with `commit: null` and `noop: true`, runs no git command and exits 0 (a second integrate still fails "already integrated"). `ale analyze` does not count a no-op integration as a write, so `ale.task.write_has_worktree` stays n/a for a mode-`none` task that changed no files. A conflict is aborted and leaves the worktree in place. When the checkout has uncommitted changes to tracked files the error names it and the first five of them; untracked files (an `.ale/` run directory, scratch files) do not block it.

### Stacked tasks

A label opts in with `"worktree": {"mode": "per_task", "stack": true}`. `ale plan bake --stack` sets that flag on every per-task task that has exactly one dependency.

Dispatch starts a stacked task from its parent's accepted commit, before that parent is integrated. The `spawned` event records `stack_parent` and `base_commit`; `ale run` schedules that due stacked child before integrating the accepted parent. Unstacked tasks, and tasks with two or more dependencies, are still held until the dependency is integrated. A dependency whose worktree mode is `none` or `shared` never holds its dependent: it has no branch to integrate. `ale dispatch` prints `holding T2: dependency T1 is accepted but not integrated` on stderr for those. A per-task worktree that is not stacked is branched from the checkout's HEAD as it stands at dispatch time. Before spawning, `ale dispatch` compares that HEAD with its upstream as of the last fetch (it never fetches); when HEAD is behind, it prints `ale dispatch: base HEAD is N commit(s) behind its upstream; fetch and fast-forward first` on stderr and writes the same lead `note`, and dispatches anyway.

`ale integrate` refuses a stacked child whose parent is not integrated yet, with `integrate <parent> first`. It also refuses while the child's base differs from the parent's latest accepted commit, and names `ale restack --task <child>`.

Reopening an accepted parent emits `restack_needed` for each unintegrated stacked child that was built on it. `ale restack --task <child>` rebases the child onto the parent's latest accepted commit, emits `restacked`, and returns the child to `submitted`. On a conflict it aborts the rebase, writes a note, and exits 1. It refuses a worktree that has uncommitted changes.

`ale run` proposes restack, then verify, then integrate, in that order. It does not propose integrating a child before its parent.

`ale evidence TASK [--out FILE]` renders deterministic markdown for a task that has verification evidence. The acceptance table has columns for id, requirement, command, expected, exit / ok, and the last 5 output lines. The requirement column shows the manual text for manual checks and `-` for command checks. The file also lists required commands, the files changed, the pinned tree and commit, and the sign-off.

`ale register-worktree --task T --path P --branch B --base SHA [--host NAME] [--cwd CHECKOUT]` records an external or remote worktree as the task's own. The `spawned` event uses `executor: external` and may include `host`. It refuses, before writing an event, a path that is not a worktree of the checkout's repository, a branch mismatch, a base that is not a commit, and a base that is not an ancestor of the branch. An unsafe host name exits 2.

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

The label-layer events are `task_added`, `label_changed`, `label_removed`, `spawned`, `integrated`, `restack_needed`, and `restacked`. Every `spawned` event that has a task worktree, stacked or not, records `diff_base`: the commit the worktree branch was created from (its merge-base with the plan base; for a branch that already existed, such as a fix task working in its parent's worktree, the base the log recorded for that branch). It also records `base_ref`, the branch the project checkout was on at dispatch (absent when HEAD was detached). Verify diffs against these. `base_commit` and `stack_parent` appear only on a stacked spawn: `base_commit` is the parent's accepted commit the child was built on, which the stack commands read. `ale register-worktree` records its `--base` commit as both `base_commit` and `diff_base`. A `spawned` event may also carry `host`. `restack_needed` records `parent` and `base_commit`; `restacked` records `parent`, `old_base`, and `new_base`. `monitor_verdict` is also lead-authority: it records the minted monitor ID, one of the four verdicts, and up to 1500 characters of the monitor response. Lead-authority events have no `agent_id`; executor-authored label changes are ignored. The stop hook emits `input_required` at most once per attempt: while an ask in the current attempt has no `input_answered` after it, a later stop emits nothing and lets the session end. `task_added` records the label filename rather than embedding the label body, keeping event lines below the 4096-byte cap.

## Eval loop

Every `ale init-run` adds the run to an index under `$ALE_HOME/.ale/` (default `~/.ale/`). `ale analyze` scores every indexed run against committed bars (first-pass rate, path scope, worktrees, resolution, `input_required` bounds, usage, acceptance drift, absolute paths, stale runs), writes a dated report and `findings.json`, and exits 1 on a breached check. `ale eval cases --ci` replays the regression cases in `evalcases/cases.jsonl`, each grown from a real run failure, and exits 1 on a failure or a regression. Both append to `$ALE_HOME/.ale/eval-ledger.jsonl`. See [analyze.md](analyze.md).

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
