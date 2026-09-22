# ale-board

A Claude Code Mod (function-hook plugin): `/ale-board` opens a live task
board for an ALE run, with tasks grouped by state and a one-line AbovePrompt
summary. Rows show task id, state, owner or assignees, attempt, worktree,
integrated status, monitor verdict, breaches, tokens, and last step. Fix tasks
are indented beneath their parent id.

It is a **read-only observation plane**: it reads `events.jsonl` and label
files under the run directory with `$.fs`. It never writes into the run
directory, never calls `$.prompt.submit`, and is LLM-free.

## Requirements

`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` must be set. Without it the plugin
installs and does nothing (the `modules` key in `hooks/hooks.json` is
silently ignored).

## Commands

| Command | What it does |
| --- | --- |
| `/ale-board` | Opens the board. Shows the cached report at once if any, then refreshes. |
| `/ale-board refresh` | Refreshes the event log now. |
| `/ale-board <run-id>` | Opens the named run under `.ale/runs/`, overriding automatic selection. |
| `/ale-board close` | Closes the pane and the band. |

## Run discovery

1. `ALE_RUN_DIR`, if set.
2. Otherwise find the nearest `.ale/` from the current working directory,
   read `.ale/runs/current`, and open that run id.
3. `/ale-board <run-id>` explicitly selects `.ale/runs/<run-id>`.
4. If no run resolves, the pane displays a single no-run line.

The mod never guesses a path outside the session's cwd.

## Board columns

In order: `input-required`, `working`/`claimed`, `submitted`, `ready`,
`rejected`/`stale`/`released`, `planned`, then `accepted`, `failed`,
`canceled`. A task whose breaches list is non-empty is marked with `!`.

When the pane's rows do not fit `maxRows`, `accepted` collapses to a count
line first, then `planned`, the same priority order named in the task
brief, implemented as the pure `budgetRows` in `lib.ts`.

## Refresh triggers

- On `turn.complete` for the main loop only (`e.agentId === undefined`),
  debounced 1.5s.
- On `/ale-board refresh`.
- On a `$.clock.every(10_000, …)` timer, started only while the pane is
  open and cancelled when it closes. This is a UI-refresh timer with no
  model call, not the LLM-polling anti-pattern the ALE protocol bans for
  monitors.
- Every refresh is single-flight: never two event-log reads run at once.

## Configuration

The board does not need an ALE CLI command to render a run.

## Persistence

The pane's open/closed state is kept in `$.store` and restored at
`session.start`. A run selected with `/ale-board <run-id>` overrides discovery
for the current session only.

## Files

| File | What |
| --- | --- |
| `hooks/hooks.json` | The `modules` key that makes this plugin a mod (paths relative to `hooks/`). |
| `register.tsx` | The hooks module: state, the five hooks, drawing. The only file with JSX or a `claude-code` import. |
| `lib.ts` | Pure event reducer and board helpers. No JSX or `claude-code` import. |
| `lib.test.ts` | Unit tests plus real CLI fixture comparisons for the TypeScript reducer. |
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
- No `$.prompt.submit` in v1; it is an observation plane.
- No polling of an LLM; the `$.clock` timer only asks `ale status --json`,
  a deterministic CLI, and only while the pane is open.
