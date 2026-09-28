# Changelog

## 0.3.1 (2026-09-28)

- `ale rescope` changes a task's `allowed_paths` or `depends_on` after `init-run` as lead-authored `label_changed` events with a reason, refusing cycles, unknown dependencies, overlaps with independent tasks and terminal tasks. Label files are no longer edited by hand.

## 0.3.0 (2026-09-28)

- Executors name a harness: `claude`, `codex`, `pi`, or one declared under the roster's `harnesses` (headless argv with `{model}`/`{prompt}`, usage parser, herdr kind). Mode (`in-session`, `headless`, `pane`) and host are derived by code from lane, harness, model and locality; a non-Anthropic model never runs in-session, and non-in-session work goes to the roster's `remote_host` unless locality is `local`. `ale plan route` prints the decision.
- Migration: legacy executor ids (`claude-headless`, `codex-exec`, `pi-print`, `claude-subagent`, `claude_code`, `herdr-pane`) still load and keep their mode. Dispatch output keeps `executor` as the spawn id and adds `harness`, `mode`, `host`, `family`, `argv`.
- `bin/ale-spawn` runs any harness argv without `eval`, closes stdin, passes `--kind`/`--host` to herdr panes, and refuses remote headless work. A remote task without a provisioned worktree is released, never run locally. Codex runs with `--skip-git-repo-check`.
- In-session dispatch records its worktree (so `ale verify` runs there) and re-prints the request while the task is unclaimed.
- Baked plan blocks show a derived `route` (harness, model, mode); a pinned `assignments[0].model` needs `pin_reason`. The model stays owned by the roster. The example roster's frontier model is `claude-opus-5-5`.
- Deep references are prefetched at dispatch into `<run-dir>/refs/<task>.md` with a content token; the prompt makes reading them required; `ale refs-ack`, the hook and `ale verify`'s `refs_read` report whether they were read (report only). Default agents now resolve their `<role>/_default` chapter.
- `ale setup` configures harnesses (with versions), models per tier, the CandleKeep refs file, the remote host, the judge mode and project notes, via `--answers`, a terminal, or the new `/ale:setup` skill; `--questions --json` and `--check`. It never stores credentials.

## 0.2.15 (2026-09-28)

- Hook latency tests measure best-of-5 and median-of-20 against the same 50 ms / 25 ms budgets, so a loaded machine no longer fails them while a slow hook still does.
- Verification now rejects out-of-scope worktree changes before commit, records validated files in evidence, keeps judge exclusions tied to explicit project roots, and dispatches stacked children before integrating their accepted parent.

## 0.2.14 (2026-09-27)

- Stacked tasks. `context.worktree.stack: true` (or `ale plan bake --stack`) lets a task with one dependency start from that dependency's accepted commit instead of waiting for integration. `ale verify` in a task's own worktree now commits the verified tree (`ale: <task> <title>`, `evidence.commit`), and `ale integrate` merges that tip after checking it still matches `evidence.tree`. Integrate enforces parent-first order; reopening an accepted parent emits `restack_needed`, and `ale restack --task T` rebases the child onto the parent's new commit (`restacked`) and sends it back to verification. `ale run` follows the same order.
- `ale evidence TASK [--out FILE]` renders a deterministic requirement-to-evidence markdown table.
- `ale register-worktree` records an external or remote worktree as the task's own (`spawned` with `executor: external`, optional `host`); `bin/ale-spawn` passes `--host "$ALE_HERDR_HOST"` to a herdr launcher.
- Deep references: `ALE_REFS_FILE` or roster `refs_file` maps agents to handbook entries, rendered in the prompt as "Deep reference (read on demand; not pasted)". The new `ale:agent-handbook` skill builds such a handbook from your own library.
- Roster `judge.exclude_paths` makes every judge and decision vote abstain (`excluded`) for matching project roots, with no judge call.
- `ale --version`.

## 0.2.13 (2026-09-24)

- `ale dispatch --spawn` starts executor processes concurrently up to the roster's existing parallel cap while preserving request-ordered output and failure events; monitor spawns remain sequential.
- `ale reopen` accepts an unintegrated accepted task and returns it to verification, while integrated tasks remain final.
- `ale verify` run in a task's own worktree records the verified tree, and `ale integrate` refuses (before staging or committing) when the allowed changes differ, until the lead reopens and verifies again. `verify --base` still checks paths against the base ref.
- Shadow adjudication measures agreement instead of assuming it. Accepting a task now also records a disagreeing Jev vote (`by: disagreement_then_accepted`, `jev_choice`), so `adjudicated_count` counts every accepted case and disagreements no longer sit pending forever. An explicit `ale adjudicate` replaces the automatic record once. A vote on a field left unset still records nothing.
- `ale judge-stats` adds `disagreement_count` per decision, uses the latest adjudication as a case's truth, and takes `--all-runs [--runs-dir DIR]` to sum every run.

## 0.2.12 (2026-09-24)

- Installed packages run workers correctly. The wheel now bundles the whole Claude Code plugin (`hooks/`, `.claude-plugin/`, `skills/`, the Mod, `agents/`, `catalog/`, `bin/`), so `claude-headless` workers started from a pip or `uv tool` install load the ALE hooks (path guard, heartbeats, stop gate). Dispatch passes `ALE_IMPORT_ROOT` and `ALE_PYTHON`, so `ale-spawn`, `ale-exec` and `ale-hook` import ALE with the installing interpreter even when its virtualenv is not activated.
- `ale.paths.plugin_root()` recognises a checkout by `.claude-plugin/plugin.json` and `bin/ale-spawn`, not by any `agents/` directory, so an unrelated `agents` package in site-packages no longer hijacks it.
- `ale eval judge` passes the roster's `judge.model` to the judge, like every other judge call.
- Judge `probabilities` entries outside [0, 1] are dropped instead of recorded; `docs/judge.md` states the exact abstain rules.
- `scripts/release-check.sh` also watches `catalog/` and `EXECUTOR.md`, which ship in the wheel.

## 0.2.11 (2026-09-24)

- `pip install` and `uv tool install` from the repository now work outside a checkout: the wheel bundles `agents/`, `catalog/` and `bin/` as `ale/_bundle`, and `ale.paths.plugin_root()` finds them. Before, `ale init-run` failed with "no agent resolved" on a non-editable install.
- Docs for public use: README install and a verified hand-run quick start, a visual explainer (`docs/index.html`), the judge command contract (`docs/judge.md`) with a model-free example judge, AGENTS.md and CLAUDE.md for contributors, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT and issue templates.
- Private references removed from comments and docs; board mockups, fixtures and the Mod regression test now use neutral demo data.

## 0.2.10 (2026-09-23)

- Jev shadow cases now accumulate on real runs. When `ale verify` accepts a task in shadow mode, each label field (role, sub, phase, model_tier, risk, effort, locality) where Jev's vote equals the accepted label is recorded as an adjudicated case (`agreement_then_accepted`). Disagreements are never scored automatically: verify prints the `ale adjudicate --task --decision --value` command for each, listing the valid values when the planner left the field unset.
- `ale judge-stats` reports `pending_adjudication` per label field, and a field never shows `bar_met` while cases are pending, so skipped disagreements cannot inflate agreement.
- Turning it on: set `judge.default: shadow` and a `judge.bar` in the roster (the shipped roster stays `off`). Bake then asks Jev on every label field, also when the plan's blocks already set it; the block still wins.

## 0.2.9 (2026-09-23)

- The boards show the run you are working on. `ale board`, `/ale:board` and the `/ale-board` Mod no longer follow `.ale/runs/current` (another session may own it): they take an explicit run, then `$ALE_RUN_DIR`, then the run with the newest event. `/ale-board <run>` switches.
- `ale runs [--json]` lists the runs under `.ale/runs`, newest event first, and marks `current`.
- Both board headers name up to three other runs active in the last 24 hours, with the age of their last event.

## 0.2.8 (2026-09-23)

- `/ale:board` opens the web board from Claude Code: it finds the run (argument, `current`, else the newest), reuses a live board or starts one, and prints the URL or the error. The `/ale-board` Mod points to it.
- Both boards say when the data is stale: the header shows "Last event 3h ago", an Idle pill replaces the running verdict when nothing has moved for an hour, and a board started on a copied run dir shows the directory path.
- `ale board` removes its `board.json` on SIGTERM and SIGINT and replaces the file a dead server left behind.
- Plan bake: one-task plans bake and init-run; plans with `## Task N` headings or ale-label blocks never fall back silently to list items; the checkbox fallback no longer crashes; `bake --write` merges into an existing hand-written block instead of adding a second one; assignments take role and model tier from the task's labels.
- Run pointer: `.ale/runs/current` stores the run directory name (old files holding a run id still resolve); `init-run` leaves a live `current` alone unless given `--set-current`; the roster is found from `$ALE_ROSTER`, then the repository that owns the run dir, then the cwd, so commands work inside task worktrees.
- `provenance.lane_reason` accepts reasons of 3 characters or more (`flow lane` writes "effort L").
- `ale verify --reject "<reason>"` lets the lead reject a submitted task after a failed manual check; path checks ignore ALE's own `.ale-setup-done` marker.
- `ale dispatch --no-exec` creates and records the worktree for a lead-executed task without starting anything, so `ale integrate` accepts it; `claude-subagent` executors get their per-task worktree too.
- Docs: allowed paths are frozen after init-run (use a fix task), `reopen` returns a task to verification while `fix` returns it to work, the fix cap (two per parent, no fix of a fix), and the deferred question of binding an in-session subagent before it spawns.

## 0.2.7 (2026-09-23)

- `ale board`: a local web board (127.0.0.1, random URL token, server-sent events, one static page, no CDN). It answers "is this run OK, what needs me, what is moving" at the top: a verdict headline with a health pill and a per-task ribbon, then Needs you / Running / Waiting / Done strips with an icon and a word for every state, the reason each waiting task is not running, superseded fixes filed under Done, a detail drawer only while a task is selected, dark mode and reduced motion. Readable from 390 px phones to wide screens.
- The board's data layer fixes six defects seen on real runs: finished tasks no longer show a live step, a failed spawn beats a later automatic heartbeat, board state equals `ale status`, token totals add up, every task not running says why, and "agent unknown" is never printed.
- The `/ale-board` Mod is rebuilt to the same design for the terminal (80 and 120 columns). It reads state only from `ale status --json` through the plugin's own `ale` package, and logs a refresh error once instead of on every tick.
- herdr pane labels bind only to the executor's own pane: a lead claiming for an executor, or a dispatcher emitting `spawned`, no longer labels its own pane; finished tasks clear their label after 60 s; `ale claim --pane` binds explicitly; spawned executors get `ALE_AGENT_ID`.
- `scripts/board-check.mjs`: a headless Chrome gate for the board (text overlap, overflow, icon size, hidden elements shown, console errors, missing tasks) at desktop and phone widths in both themes. It never launches Chrome inside the Codex sandbox.

## 0.2.6 (2026-09-23)

- Jev shadow decisions (opt-in: roster `judge.default: shadow`). Ten decisions collect a shadow vote where they happen (bake, init-run, dispatch, verify, fix, monitor breach). Votes are recorded as events and never change a label, lane, routing or state.
- Verdict decisions ask narrow yes/no evidence questions about the text and compute the choice in code: `needs_monitor` (at init-run, on the planner's final labels), `rejection_action`, `monitor_verdict`. `lane` and `executor` are deterministic and not judged (`lane` restates `flow lane`). `locality` is a yes/no question mapped to `any|local`.
- `ale judge-stats` reports agreement per decision, split inside and outside the uncertain confidence band, with median latency and progress toward 100 adjudicated cases; `effort` is flagged as a grey-zone estimate.
- `ale adjudicate --decision` refuses a second adjudication of the same task and decision.
- The roster gains optional `judge.default`, `judge.bar` and `judge.model`; old rosters load unchanged and the shipped roster keeps `judge.default: off`.
- Tests: a root conftest guard fails any test that would call the real `jev-ask`.

## 0.2.5 (2026-09-22)

- Reference skills move from `agents/_refs` to `catalog/refs` so Claude Code stops loading them as plugin subagents.
- Every bundled agent has a description (the agent schema gains an optional `description` field).
- `agents/backend/_default.md` is renamed `backend-default`: it shared the name `backend-architecture`, which made one agent fail to load.
- New plugin agent contract test.
- ale verify: evidence fits the event limit on large diffs.

## 0.2.4 (2026-09-22)

- Make roster vocabulary, judge modes, and worktree setup defaults additive; old rosters are filled in memory before validation and hashing.

## 0.2.3 (2026-09-22)

- Add `labels.locality` (`any` or `local`) and document the label-layer lifecycle.

## 0.2.2 (2026-09-22)

- `ale run` and `ale dispatch` resume a task released after its executor died: each release makes the executor assignment due again (new trigger instance, same worktree), and a release counts as an attempt.
- `ale run` no longer stops on liveness breaches (stuck, lease_expired) of a released task; it prints one line and re-dispatches it.

## 0.2.1 (2026-09-22)

- `/ale-board` answers its command again, with or without an argument: the hooks module called `process.cwd()`, which does not exist in the function-hook environment, and two resolver helpers it called were never imported, so every `command.run` and every board refresh threw
- Run discovery reads the live session directory from `$.session.cwd()`, so the band and board find the run from a git worktree cwd
- `.ale/runs/current` is followed whether it is a file naming a run id or path, a symlink to a run directory, or a directory
- A failing `/ale-board` now answers with the error instead of leaving the command unanswered

## 0.2.0 (2026-09-22)

- Worktree setup commands before an executor starts (`worktree_setup_defaults`, label `context.worktree.setup`, `setup_outputs`); `ale note` by the lead on an unowned task

- Agent layer: labels gain sub and phase, roster vocabulary for subs, cross subs and phases, catalog-aware label checks
- Agent-resolved prompts and enforcement: reads, checklist, harness split, deny paths and tools, required commands, agents list and show, relabel re-resolution, stale marker, tier floor on assignments
- Rule voters and judge shadow modes for sub and phase; roster schema accepts judge modes for sub and phase; voters follow the rule contract
- Agent bundle integrated: `_default` and `_cross` conventions, empty reads rejected
- Per-agent analytics and the `--agent-variant` A/B overlay
- One-command flow: `ale setup`, `ale run` loop, judge opt-in, label-layer skill rewritten with defaults for roster and run dir
- `/ale-board` resolves the run from `ALE_RUN_DIR`, the live cwd, then the launch dir, and accepts a run dir or id
- `ale reopen`, and exhaustion waits for the last fix to be verified
- Label compatibility migration: readers accept labels compiled by intermediate builds, migrated in memory
- Fixes from running the label layer on real plans, including a duplicate submit, a blind integrate error, a cross-role sub, integrating an accepted task before dispatching dependents, a stale-base dispatch fix, and reusing a worktree and branch on a second attempt
- Shipped rosters carry a role vote for every role-scoped sub rule
