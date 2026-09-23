# Agentic Label Engineering (ALE)

Typed task labels, an append-only board, and a zero-LLM watchdog for multi-agent coding work.
An orchestrator plans tasks and writes one label per task. Code reads the label and decides who may
claim the task, when a claim goes stale, and whether the work is accepted. No agent can mark its own
work done.

Status: core executor protocol with Claude Code and Pi adapters, plus Codex hook and wrapper support.

## Quick start

```bash
Install the ALE plugin for your agent.
ale setup
/label-layer PLAN.md
```

In Claude Code, open the live task board with `/ale:board [run-dir]`. It starts or reuses the web board and returns its URL.

For a manual run, use `ale plan bake PLAN.md --write` followed by `ale run PLAN.md`.
Plan baking does not call an external judge by default. Add `--judge` to opt in; this sends task text to the configured external API. `--no-judge` remains available for compatibility.

## Label layer

For the plan-to-dispatch workflow, typed label fields, worktree isolation, fix tasks, derived status, and timeline/meta reporting, read [docs/label-layer.md](docs/label-layer.md). For agent definitions, taxonomy, resolution, and enforcement, read [docs/agents.md](docs/agents.md).

## Concepts

- **Label**: `labels` (closed vocabulary from `roster.json`: role, model_tier, lane, risk, effort, locality),
  `context` (spec, pointers, allowed paths, dependencies), `acceptance` (2 to 5 commands), `watch` thresholds.
- **Roster**: your vocabulary plus the `(role, model_tier) -> executor, model` table. A new model is a
  one-line change here.
- **Board**: `events.jsonl`, append only. State is computed from it. Handoff files under `handoff/` are
  rendered views for humans and successor agents.
- **Lease**: a claim lives while heartbeats arrive. The watchdog releases dead claims.
- **Completion barrier**: only `ale verify` writes `accepted`, after running the acceptance commands itself.
- **Event authorship**: system events (`verified`, `accepted`, `rejected`, `failed`, `canceled`,
  `lease_expired`, `released`, `input_answered`) are applied only when written with no agent id.
  Executor events (`claim`, `heartbeat`, `submit`, `input-required`, `note`) must come from
  the task's current owner. Usage is recorded by the orchestrator or an adapter, not the
  executor, and is therefore not owner-guarded.

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
- `cost_gate.max_concurrent` is part of the roster schema, but v0.1 does not enforce it yet.
  Enforcement arrives with `dispatch`.

## Watchdog

`ale watchdog` scans open tasks for breaches: a stale lease (no heartbeat within
`heartbeat_timeout_s`), a claim stuck without progress past `stuck_after_s`, a run past
`max_duration_s`, a task left in `submitted` longer than `heartbeat_timeout_s` (breach type
`unverified` - nobody ran `ale verify` on it in time), and attempts past `max_attempts`. Run it from
a loop or scheduler; exit 6 means it found at least one breach.

## Harness support

The status in each cell describes the shipped integration, not a claim about
what the underlying harness could support in a future adapter.

| Rule | Claude Code hooks | Codex hooks | Pi extension | ale-exec wrapper |
| --- | --- | --- | --- | --- |
| Path guard | Enforced: PreToolUse edit denial | Enforced: PreToolUse denial | Enforced: edit and write events | Not possible: verify only |
| Auto heartbeat | Enforced: PostToolUse, 60 s throttle | Not possible: current adapter has no PostToolUse entry | Enforced: post-tool hook | Enforced: timer heartbeat |
| Stop/submit gate | Enforced: Stop acceptance gate | Not possible: current adapter has no Stop entry | Advisory: settlement runs the gate but print mode cannot block | Enforced: exit-time `check` then submit or input-required |
| Usage capture | Enforced: transcript IDs are deduplicated | Advisory: `--usage-from codex-json` on wrapper | Enforced: assistant message usage | Enforced: printed JSON usage when configured |
| Session context | Enforced: SessionStart stdout | Not possible: current adapter has no SessionStart entry | Enforced: session start injection | Not possible: wrapper has no context injection |

The Claude Code and Codex hook contracts, Pi event limits, and transcript
fields are recorded in `docs/harness-facts.md`. Shell commands can write
anywhere, so `ale verify --base` remains the containment backstop.

## Tests

`uvx --python 3.9 pytest -q`

## License

MIT. See `LICENSE`.
