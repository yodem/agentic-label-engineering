# Changelog

## 0.2.3 (2026-09-22)

- Add `labels.locality` (`any` or `local`) and document the label-layer lifecycle.

## 0.2.2 (2026-09-22)

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
