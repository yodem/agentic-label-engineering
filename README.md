# Agentic Label Engineering (ALE)

Typed task labels, an append-only board, and a zero-LLM watchdog for multi-agent coding work.

When several coding agents work from one plan, the weak point is the word "done". An agent can say it
finished while the tests fail, drift outside the files it was meant to touch, or stall without anyone
noticing. ALE moves those judgments out of the agents and into code:

- An orchestrator writes one **label** per task: who should do it, which files it may change, and the
  2 to 5 shell commands that prove it is finished.
- Every claim, heartbeat and submission goes into an append-only **event log**. State is computed from
  the log, never stored.
- Only `ale verify` can mark a task **accepted**, and only after it runs the acceptance commands
  itself. **No agent can mark its own work done.**
- A **watchdog** with no model in it flags stale claims, stuck tasks and overruns.

A visual explainer lives in [`docs/index.html`](docs/index.html). Open it in a browser.

```text
PLAN.md ──bake──▶ labels ──init-run──▶ board (events.jsonl)
                                          │
        dispatch ─▶ claim ─▶ heartbeat ─▶ submit ─▶ ale verify ─┬─▶ accepted ─▶ integrate
                                                                └─▶ rejected ─▶ fix task
```

Status: pre-1.0. The executor protocol works with Claude Code, Pi and Codex. Interfaces may still change.

## Install

Requirements: Python 3.9 or newer, git, and a POSIX system (macOS or Linux).

Install the `ale` CLI from a clone. Use an **editable** install: the agent catalog (`agents/`) is
read from the checkout, so a plain `pip install` of the package cannot find it.

```sh
git clone https://github.com/yodem/agentic-label-engineering.git
cd agentic-label-engineering
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
ale --help
```

### Claude Code plugin

The plugin adds the executor hooks, the `/label-layer` and `/ale:board` skills, and the `/ale-board`
status Mod. It calls the `ale` CLI, so install that first.

```text
/plugin marketplace add yodem/agentic-label-engineering
/plugin install ale@agentic-label-engineering
```

The `/ale-board` Mod also needs `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`; without it the rest of the
plugin works and the Mod stays silent. See [mod/README.md](mod/README.md).

### Pi and Codex

- Pi: load [`adapters/pi/ale.ts`](adapters/pi/README.md) as an extension.
- Codex: copy [`adapters/codex/hooks.json`](adapters/codex/README.md) into your Codex hook config.

## Quick start: one task by hand, no agent

This five-minute walkthrough plays both roles, orchestrator and executor, so you can watch the
protocol refuse a false "done". Run it with the virtualenv from **Install** active.

**1. A throwaway project with a two-task plan.** `.ale/` holds run state and stays out of git.

```sh
mkdir /tmp/ale-demo && cd /tmp/ale-demo
git init -q && printf '.ale/\n' > .gitignore && git add .gitignore && git commit -qm init
ale setup
cat > PLAN.md <<'EOF'
# Plan

## Task 1: Add a greeting module

**Files:** Create: `src/greet.py`

Write `greet(name)` returning `Hello, <name>!`.

Run: `test -f src/greet.py`
Run: `python3 -c "import sys; sys.path.insert(0, 'src'); from greet import greet; assert greet('Ada') == 'Hello, Ada!'"`

## Task 2: Document the greeting

**Files:** Modify: `README.md`

Depends on Task 1.

Run: `test -f README.md`
Run: `grep -q greet README.md`
EOF
```

**2. Bake labels.** ALE reads the plan literally: `Files:` lines become allowed paths, `Run:` lines
become acceptance checks, and `Depends on Task N` becomes a dependency. It writes an `ale-label`
block under each task heading and a `PLAN.md.ale-provenance.json` record, then exits 1 and lists
the gaps. That's expected.

```sh
ale plan bake PLAN.md --write      # exit 1: gaps listed
```

```text
T1: lane
T1: lane_reason
T2: lane
T2: lane_reason
```

Lane (`inline`, `workflow` or `pane`) and the reason for it are always the planner's call; no
classifier guesses them. Fill them in, check the plan, and commit it:

```sh
sed -i.bak 's/"lane":null/"lane":"inline"/; s/"lane_reason": null/"lane_reason": "Small and watched, so inline."/' PLAN.md && rm PLAN.md.bak
ale plan bake PLAN.md
git add -A && git commit -qm plan      # PLAN.md and its .ale-provenance.json
```

**3. Start the run and dispatch.** `init-run` freezes the labels. `ready` lists tasks whose
dependencies are met. `dispatch --no-exec` creates a git worktree for T1 without launching an agent.
It prints a spawn request (the prompt an agent would get), saved here to a file.

```sh
ale init-run --plan PLAN.md --set-current
ale ready
ale dispatch --no-exec > .ale/dispatch.jsonl
AGENT=$(python3 -c "import json; print(json.loads(open('.ale/dispatch.jsonl').readline())['agent_id'])")
WT=.ale/runs/PLAN/wt/T1
```

**4. Act as the executor, and claim done without doing the work.**

```sh
ale claim --task T1 --agent "$AGENT"
ale heartbeat --task T1 --agent "$AGENT" --step "writing greet.py"
ale submit --task T1 --agent "$AGENT" --summary "done"
ale verify --task T1 --cwd "$WT"     # acceptance failed: A1, A2  (exit 1)
ale status                           # T1 rejected
```

**5. Do the work, then verify again.** `reopen` is the lead's decision to retry verification.

```sh
mkdir -p "$WT/src" && printf 'def greet(name):\n    return "Hello, %%s!" %% name\n' > "$WT/src/greet.py"
ale reopen --task T1 --reason "greet.py written"
ale verify --task T1 --cwd "$WT"     # exit 0
ale integrate --task T1              # commits the allowed changes and merges branch ale/PLAN/T1
ale status                           # T1 accepted, integrated=yes; T2 ready
ale watchdog                         # [] - no breaches
```

With an agent, you write the plan, fill the gaps, and the runner does steps 3 to 5 for every task:

```sh
ale run PLAN.md          # or, in Claude Code: /label-layer PLAN.md
```

`ale run` dispatches through the executors in your roster, verifies submissions, integrates accepted
work, and opens up to two focused fix tasks per rejection. Watch it with `ale status`,
`ale timeline`, `ale meta`, or the web board (`ale board --open`, or `/ale:board` in Claude Code).

## Concepts

- **Label**: `labels` (closed vocabulary from `roster.json`: role, model_tier, lane, risk, effort, locality),
  `context` (spec, pointers, allowed paths, dependencies), `acceptance` (2 to 5 commands), `watch` thresholds.
- **Roster**: your vocabulary plus the `(role, model_tier) -> executor, model` table. A new model is a
  one-line change here. `ale setup` writes one to `.ale/roster.json`.
- **Board**: `events.jsonl`, append only. State is computed from it. Handoff files under `handoff/` are
  rendered views for humans and successor agents.
- **Lease**: a claim lives while heartbeats arrive. The watchdog releases dead claims.
- **Completion barrier**: only `ale verify` writes `accepted`, after running the acceptance commands itself.
- **Event authorship**: system events (`verified`, `accepted`, `rejected`, `failed`, `canceled`,
  `lease_expired`, `released`, `input_answered`) are applied only when written with no agent id.
  Executor events (`claim`, `heartbeat`, `submit`, `input-required`, `note`) must come from
  the task's current owner. Usage is recorded by the orchestrator or an adapter, not the
  executor, and is therefore not owner-guarded.

## Executors

The roster maps each `(role, model_tier)` to an executor. `bin/ale-spawn` launches it:

| Executor | Runs | Needs |
| --- | --- | --- |
| `claude-headless` | `claude -p` with the ALE plugin loaded | Claude Code CLI |
| `codex-exec` | `codex exec --json` wrapped by `bin/ale-exec` | Codex CLI |
| `pi-print` | `pi --mode json -p` wrapped by `bin/ale-exec` | Pi CLI |
| `claude-subagent` | an in-session subagent the lead starts | a Claude Code session |
| `herdr-pane` | a visible terminal pane | a launcher script you supply in `ALE_HERDR_EXEC` with `start --cwd DIR` and `send AGENT --spec FILE` subcommands |

The shipped roster uses `claude-headless` and `codex-exec` only.

## Optional judge

ALE can collect second-opinion votes on labels from an external judge command. It is **off by
default**, runs in shadow mode (it never changes a decision), and any executable that speaks a small
JSON contract works. See [docs/judge.md](docs/judge.md).

## Exit codes

0 ok, 1 check failed, 2 usage, 3 claim lost, 4 lease lost, 5 needs sign-off, 6 breaches found.

## Watchdog

`ale watchdog` scans open tasks for breaches: a stale lease (no heartbeat within
`heartbeat_timeout_s`), a claim stuck without progress past `stuck_after_s`, a run past
`max_duration_s`, a task left in `submitted` longer than `heartbeat_timeout_s` (breach type
`unverified`: nobody ran `ale verify` on it in time), and attempts past `max_attempts`. Run it from
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
fields are recorded in [docs/harness-facts.md](docs/harness-facts.md). Shell commands can write
anywhere, so `ale verify --base` remains the containment backstop.

## Limits and trust boundary

- POSIX only. Local filesystems only: the append guarantee does not hold on network mounts.
- Acceptance commands run with `shell=True`. **Labels are code.** Only run labels you or your
  orchestrator wrote. See [SECURITY.md](SECURITY.md).
- `verify --base` expects one task per working tree. Use a git worktree per executor.
- `lane` is never chosen by a classifier. The planner answers three questions and records `lane_reason`.
- `agent_id` is self-asserted. The log guards against accidents and honest mistakes, not against a
  malicious local process that forges events.
- Changed paths are normalised before containment is checked; absolute paths and paths that escape
  the project are always violations.
- `ale integrate` needs a clean checkout. Keep `.ale/` and virtualenvs in `.gitignore`.
- `cost_gate.max_concurrent` is part of the roster schema but is not enforced yet.

## Documentation

| Read | For |
| --- | --- |
| [docs/index.html](docs/index.html) | A visual walkthrough of the whole idea |
| [docs/label-layer.md](docs/label-layer.md) | Plan format, label fields, worktrees, fix tasks, the run loop |
| [docs/agents.md](docs/agents.md) | Agent taxonomy, catalog lookup, rule enforcement |
| [docs/hooks.md](docs/hooks.md) | What the hooks enforce, per harness |
| [docs/labeling.md](docs/labeling.md) | The label cascade and judge shadow decisions |
| [docs/judge.md](docs/judge.md) | The optional judge command and its contract |
| [docs/harness-facts.md](docs/harness-facts.md) | Verified hook facts for Claude Code, Pi and Codex |
| [EXECUTOR.md](EXECUTOR.md) | The protocol an executor agent follows |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests: `uvx --python 3.9 pytest -q` and `cd mod && bun test`.

## License

MIT. See [LICENSE](LICENSE). Parts of `agents/` and `catalog/refs/` derive from
[OrchestKit](https://github.com/yonatangross/orchestkit) under MIT; see [NOTICE](NOTICE).
