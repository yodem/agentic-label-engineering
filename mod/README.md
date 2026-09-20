# ale-board

A Claude Code Mod (function-hook plugin): `/ale-board` opens a live task
board for an ALE run — a docked/inline **Pane** with one row per task,
grouped into columns by state, and a one-line **AbovePrompt** band while the
board is open.

It is a **read-only observation plane**: the only thing it does to the
outside world is run `python3 -m ale status --json --run-dir <dir> --roster
<path>` (or whatever `aleCommand` is configured to) and read label files
under the run directory with `$.fs.read`. It never writes into the run
directory, never calls `$.prompt.submit`, and is LLM-free.

## Requirements

`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` must be set — without it the plugin
installs and does nothing (the `modules` key in `hooks/hooks.json` is
silently ignored).

## Commands

| Command | What it does |
| --- | --- |
| `/ale-board` | Opens the board. Shows the cached report at once if any, then refreshes. |
| `/ale-board refresh` | Re-runs `ale status --json` now. |
| `/ale-board run <run_id_or_path>` | Points the board at a specific run (persisted in `$.store`). |
| `/ale-board close` | Closes the pane and the band. |

## Run discovery

1. `ALE_RUN_DIR`, if set.
2. Otherwise the most recently modified directory under
   `<session cwd>/.ale/runs/`.
3. If neither resolves to a run, the pane says so and names the two
   commands that create one (`ale init-run`, then `ale status --json`).

The mod never guesses a path outside the session's cwd.

The roster path follows the same resolution the `ale` CLI itself uses:
`ALE_ROSTER`, else `<cwd>/roster.json`, else `<cwd>/.ale/roster.json`.

## Board columns

In order: `input-required`, `working`/`claimed`, `submitted`, `ready`,
`rejected`/`stale`/`released`, `planned`, then `accepted`, `failed`,
`canceled`. A task whose breaches list is non-empty is marked with `!`.

When the pane's rows do not fit `maxRows`, `accepted` collapses to a count
line first, then `planned` — the same priority order named in the task
brief, implemented as the pure `budgetRows` in `lib.ts`.

## Refresh triggers

- On `turn.complete` for the main loop only (`e.agentId === undefined`),
  debounced 1.5s.
- On `/ale-board refresh`.
- On a `$.clock.every(10_000, …)` timer, started only while the pane is
  open and cancelled when it closes. This is a UI-refresh timer with no
  model call, not the LLM-polling anti-pattern the ALE protocol bans for
  monitors.
- Every refresh is single-flight: never two `ale status` processes run at
  once.

## Configuration

`aleCommand` (plugin `userConfig`, default `"python3 -m ale"`): the command
used to invoke the `ale` CLI, split on whitespace (there is no shell — see
`$.process.run`'s argv contract). Change it if `python3 -m ale` is not
importable from your session's cwd, e.g. to point at a venv's `python` or an
installed `ale` entry point.

## Persistence

The pane's open/closed state and the selected run (if `/ale-board run` was
used) are kept in `$.store` and restored at `session.start`, per the mod
book's Rule 6 (hot reload resets module-level state; `$.store` survives).

## Files

| File | What |
| --- | --- |
| `hooks/hooks.json` | The `modules` key that makes this plugin a mod (paths relative to `hooks/`). |
| `register.tsx` | The hooks module: state, the five hooks, drawing. The only file with JSX or a `claude-code` import. |
| `lib.ts` | Pure helpers: parsing, grouping, truncation, row budgeting, formatting. No JSX, no `claude-code` import — `bun test` runs it on a bare clone. |
| `lib.test.ts` | `bun test` cases over `lib.ts`: grouping order, truncation, row-budget collapse order, malformed-JSON handling, the no-run message, and the band line. |
| `fixtures/status-sample.json` | A representative `ale status --json` shape, covering every board column. |
| `fixtures/live/` | Captured evidence from a live tmux run (see below), when it could be produced. |

## Type checking

Type checking needs the early-access declarations, generated per Claude
Code build and git-ignored. This repo ships a snapshot at
`mod/.claude/types/claude-code.d.ts`, copied from
`~/.claude/plugins/marketplaces/claude-code-plugins/mods/types/claude-code.d.ts`
(written by Claude Code **2.1.277**; the installed build when this mod was
written was **2.1.278**). Regenerate for your own build:

```sh
cd mod && CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 command claude
# inside the session:
/plugin-types .claude/types
```

then:

```sh
cd mod && bunx tsc --noEmit -p tsconfig.json
```

## Testing

```sh
cd mod && bun test                              # pure lib.ts, no early-access types needed
cd mod && bunx tsc --noEmit -p tsconfig.json     # type-checks register.tsx + lib.ts
command claude plugin validate ~/dev/agentic-label-engineering   # lists hooked events and every $ call
```

Live test, per the mod book's Chapter 9 tmux recipe:

```sh
env -u CLAUDECODE CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 \
  command claude --plugin-dir ~/dev/agentic-label-engineering --debug-file /tmp/ale-board.log
# in another pane/terminal:
tmux load-buffer -b ale-board-cmd <(printf '/ale-board\n')
tmux paste-buffer -b ale-board-cmd -t <session>
tmux send-keys -t <session> Enter
tmux capture-pane -p -t <session> > mod/fixtures/live/board.txt
grep -n 'a hook returned a tree that does not validate' /tmp/ale-board.log   # should print nothing
```

## What it does not do (by design)

- No writes into the run directory, ever (`ACI-11`).
- No `$.prompt.submit` in v1 — it is an observation plane.
- No polling of an LLM; the `$.clock` timer only asks `ale status --json`,
  a deterministic CLI, and only while the pane is open.
