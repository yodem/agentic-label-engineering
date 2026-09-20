# Agentic Label Engineering (ALE)

Typed task labels, an append-only board, and a zero-LLM watchdog for multi-agent coding work.
An orchestrator plans tasks and writes one label per task. Code reads the label and decides who may
claim the task, when a claim goes stale, and whether the work is accepted. No agent can mark its own
work done.

Status: core only (v0.1). Labeling cascade, executor adapters, analytics and the Claude Code plugin are
later milestones.

## Quick start

```bash
uv tool install .            # or: python3 -m ale ...
export ALE_RUN_DIR=.ale/runs/my-run ALE_ROSTER=roster.json
cp examples/roster.json roster.json
mkdir -p $ALE_RUN_DIR/labels && cp examples/run/labels/*.json $ALE_RUN_DIR/labels/
ale validate && ale init-run
ale ready                                  # claimable tasks
ale claim --task T01 --agent me
ale heartbeat --task T01 --agent me --step "wrote tests"
ale submit --task T01 --agent me --summary "endpoint added, tests pass"
ale verify --task T01 --base main          # runs the label's acceptance commands, checks paths
ale watchdog                               # run from a loop or scheduler; exit 6 means breaches
ale status
```

Add `.ale/` to your `.gitignore`.

## Concepts

- **Label**: `labels` (closed vocabulary from `roster.json`: role, model_tier, lane, risk, effort),
  `context` (spec, pointers, allowed paths, dependencies), `acceptance` (2 to 5 commands), `watch` thresholds.
- **Roster**: your vocabulary plus the `(role, model_tier) -> executor, model` table. A new model is a
  one-line change here.
- **Board**: `events.jsonl`, append only. State is computed from it. Handoff files under `handoff/` are
  rendered views for humans and successor agents.
- **Lease**: a claim lives while heartbeats arrive. The watchdog releases dead claims.
- **Completion barrier**: only `ale verify` writes `accepted`, after running the acceptance commands itself.
- **Event authorship**: system events (`verified`, `accepted`, `rejected`, `failed`, `canceled`,
  `lease_expired`, `released`, `input_answered`) are applied only when written with no agent id.
  Executor events (`claim`, `heartbeat`, `submit`, `input-required`, `note`, `usage`) must come from
  the task's current owner.

## Exit codes

0 ok, 1 check failed, 2 usage, 3 claim lost, 4 lease lost, 5 needs sign-off, 6 breaches found.

## Limits and trust boundary

- POSIX only. Local filesystems only: the append guarantee does not hold on network mounts.
- Acceptance commands run with `shell=True`. Labels are code. Only run labels you or your orchestrator wrote.
- `verify --base` expects one task per working tree. Use a git worktree per executor.
- `lane` is never chosen by a classifier. The planner answers three questions and records `lane_reason`.
- `agent_id` is self-asserted. The log guards against accidents and honest mistakes, not against a
  malicious local process that forges events.
- Changed paths are normalised before containment is checked; absolute paths and paths that escape
  the project are always violations.

## Watchdog

`ale watchdog` scans open tasks for breaches: a stale lease (no heartbeat within
`heartbeat_timeout_s`), a claim stuck without progress past `stuck_after_s`, a run past
`max_duration_s`, a task left in `submitted` longer than `heartbeat_timeout_s` (breach type
`unverified` — nobody ran `ale verify` on it in time), and attempts past `max_attempts`. Run it from
a loop or scheduler; exit 6 means it found at least one breach.

## Tests

`uvx --python 3.9 pytest -q`

## License

MIT. See `LICENSE`.
