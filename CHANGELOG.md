# Changelog

## 0.2.0 (2026-09-22)

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
