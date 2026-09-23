# ale-board

A Claude Code Mod (function-hook plugin): `/ale-board` opens a read-only
attention-first board for an ALE run. The terminal order is Needs you,
Running, Waiting, Done. Every row carries a glyph, state word, full title and
the sentence explaining why it needs attention or is waiting. Fix tasks retain
their parent relationship.

It is a **read-only observation plane**: it reads task and run state from
the ALE package bundled with the plugin and reads label files for display
names. It never writes into the run directory, never calls `$.prompt.submit`,
and is LLM-free.

## Requirements

`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` must be set. Without it the plugin
installs and does nothing (the `modules` key in `hooks/hooks.json` is
silently ignored).

## Commands

| Command | What it does |
| --- | --- |
| `/ale-board` | Opens the board. Shows the cached report at once if any, then refreshes. |
| `/ale-board refresh` | Refreshes status from ALE now. |
| `/ale-board <run-id-or-absolute-dir>` | Opens that run id under the nearest `.ale/runs/`, or the absolute run directory, overriding automatic selection. |
| `/ale-board close` | Closes the pane and the band. |

## Run discovery

Resolution precedence is `/ale-board <absolute-run-dir>` or `<run-id>` first (run ids resolve under the nearest `.ale/runs`), then `ALE_RUN_DIR`, then the first entry from the plugin's bundled `ale runs --json --runs-dir <nearest .ale/runs>`. The Mod does not read `.ale/runs/current`. The band and pane identify the selected source as `arg`, `env`, or `latest`. Live-cwd discovery uses the session API and falls back to the session launch directory if it fails. If `ale runs` fails, its error is logged once and the board shows no run. When recent alternatives exist, the pane names up to three runs with their ages and ends with `/ale-board <run> switches`.

## Terminal board layout

The state vocabulary is shared with the web board: `Needs your answer`,
`Failed to start`, `Out of attempts`, `Rejected`, `Failed`, `No heartbeat`,
`Running`, `Verifying`, `Waiting on fix`, `Ready`, `Retrying`, `Waiting`,
`Done`, and `Canceled`. Every state has a glyph as well as text; colour is
optional and never the only cue. Unknown states remain visible as
`Unknown: <state>` in Waiting.

The pane uses the DESIGN-V2 terminal layout: a three-row run summary followed
by Needs you, Running, Waiting and Done. Task prefixes reserve 29 columns for
the glyph, id and state label. At 80 columns titles use 16 cells; at 120 they
use 32 cells and Done rows can show token totals. Needs-you reasons wrap and
are kept in full, Waiting shows its blocker, and Done shows the latest three
tasks followed by a count. If columns fall below 80, rows keep the fixed
prefix and use the remaining space for a shortened title. Named terminal
colours add status cues while glyphs and labels continue to carry the state.

## Refresh triggers

- On `turn.complete` for the main loop only (`e.agentId === undefined`),
  debounced 1.5s.
- On `/ale-board refresh`.
- On a `$.clock.every(10_000, …)` status refresh, started only while the pane is
  open and cancelled when it closes. This never polls an LLM.
- Every refresh is single-flight: never two status commands run at once.

## Configuration

Each refresh runs the plugin's ALE package through `python3 -c` with
`status --json --run-dir <run>`, so it does not depend on an ALE package in the
user's default Python environment. When the run's repository has no
`.ale/roster.json`, the Mod passes the configured roster path with
`--roster`. Repeated refresh failures with the same message are logged once;
the board continues to show the error until a refresh succeeds.

## Persistence

The pane's open/closed state is kept in `$.store` and restored at
`session.start`. A run selected with `/ale-board <run-id>` overrides discovery
for the current session only.

## Files

| File | What |
| --- | --- |
| `hooks/hooks.json` | The `modules` key that makes this plugin a mod (paths relative to `hooks/`). |
| `register.tsx` | The hooks module: status refresh, state, hooks and drawing. The only file with JSX or a `claude-code` import. |
| `lib.ts` | Pure status parsing and board helpers, plus the retained event reducer for compatibility tests. No JSX or `claude-code` import. |
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
command claude plugin validate /path/to/agentic-label-engineering   # lists hooked events and every $ call
```

Live test in tmux:

```sh
env -u CLAUDECODE CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 \
  command claude --plugin-dir /path/to/agentic-label-engineering --debug-file /tmp/ale-board.log
# in another pane/terminal:
tmux load-buffer -b ale-board-cmd <(printf '/ale-board\n')
tmux paste-buffer -b ale-board-cmd -t <session>
tmux send-keys -t <session> Enter
tmux capture-pane -p -t <session> > mod/fixtures/live/board.txt
grep -n 'a hook returned a tree that does not validate' /tmp/ale-board.log   # should print nothing
```

## What it does not do (by design)

- No writes into the run directory, ever (`ACI-11`).
- A live web-board URL is shown only when validated metadata has a current
  process id, a literal loopback host, a non-empty instance id, and a fresh
  timestamp. It is plain text, not a clickable Link.
- No `$.prompt.submit` in v1; it is an observation plane.
- No polling of an LLM; the `$.clock` timer only asks `ale status --json`,
  a deterministic CLI, and only while the pane is open.
