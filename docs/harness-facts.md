# Harness facts

Verified on 2026-09-20 from primary sources: official docs, shipped type declarations and source, the CLIs' own help output, and small throwaway experiments. Versions: Claude Code 2.1.278, Pi 0.86.0, Codex CLI 0.155.1. Every hook capability that ALE's enforcement layer relies on is recorded here with its evidence. Re-verify after a harness upgrade.

---

# Part 1: Claude Code

# Claude Code hook facts  -  verified

Installed version (verifying machine, `command claude --version`): **2.1.278 (Claude Code)**.
Verified 2026-09-20. Primary source pages fetched fresh from `code.claude.com/docs/en/...`
via `tvly extract` on this date; experiments run against the same installed binary. All
temp directories were deleted after the experiments (see "Experiments run" at the end).

---

## Item 1  -  Classic hook stdin JSON: exact field names

**Common fields on every hook event** (doc: https://code.claude.com/docs/en/hooks
§"Common input fields"):

> `session_id`, `prompt_id` (v2.1.196+), `transcript_path`, `cwd`, `permission_mode`,
> `effort` (`{level: ...}`), `hook_event_name`. When running with `--agent` or inside a
> subagent, two more: `agent_id` and `agent_type`.

Event-specific fields, confirmed both from docs and from experiment logs (`hook-log.jsonl`,
see Item 6 for full side-by-side JSON):

- `SessionStart`: `source` (`startup|resume|clear|compact|fork`), optional `model`,
  `agent_type`, `session_title`. Experiment log entry 1 (source `startup`) matched doc shape.
- `PreToolUse`: `tool_name`, `tool_input`, `tool_use_id`. Confirmed live: `tool_input.file_path`
  absolute, `tool_input.content` present for `Write`.
- `PostToolUse`: same plus `tool_response`, `duration_ms`.
- `Stop` / `SubagentStop`: `stop_hook_active`, `last_assistant_message`, `background_tasks`,
  `session_crons`. `SubagentStop` additionally: `agent_id`, `agent_type`,
  `agent_transcript_path` (a *different* file than `transcript_path`  -  see Item 7).
- `UserPromptSubmit`: `prompt` (the submitted text).

**Whether any field identifies the subagent**: yes  -  `agent_id` and `agent_type`. See Item 6
for the full decisive experiment.

Source: https://code.claude.com/docs/en/hooks §"Common input fields", §"SessionStart input",
§"PreToolUse input", §"PostToolUse input", §"Stop input", §"SubagentStop input",
§"UserPromptSubmit input". Verified on 2.1.278; also verified empirically (Experiment 1).

---

## Item 2  -  PreToolUse deny: exit code 2 vs JSON `permissionDecision`

Both work; exit 2 is unconditional (JSON can't override it), the JSON form is richer
(allow/deny/ask/defer + `updatedInput` + `additionalContext`).

- **Exit code 2**: "Exit 2 means a blocking error... even a JSON `permissionDecision` of
  `"allow"` can't override it... `PreToolUse` blocks the tool call." The blocking message
  shown to Claude is the hook's **stderr** text (or the JSON blocking-decision `reason` if
  present). Deprecated top-level `decision`/`reason` on PreToolUse still map: `"approve"` →
  `allow`, `"block"` → `deny`, but current docs say use `hookSpecificOutput.permissionDecision`.
- **JSON form**: `hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision:
  "allow"|"deny"|"ask"|"defer", permissionDecisionReason, updatedInput, additionalContext }`.
  For `"deny"`, `permissionDecisionReason` **is shown to Claude**; for `"allow"`/`"ask"` it is
  shown to the user but not Claude. Precedence when multiple hooks disagree: `deny > defer >
  ask > allow`.
- **What the model sees**: confirmed by experiment (Experiment 2). A `PreToolUse` hook on
  `Write` exited 2 with stderr `"DENIED: no writes allowed in this test"`. The headless run's
  final result text was: *"The Write to `c.txt` was blocked by the PreToolUse hook (`DENIED:
  no writes allowed in this test`), so the file was not created."*  -  the model saw the exact
  stderr string as the denial reason, and the `permission_denials` array in the `-p --output-format
  json` result recorded the blocked tool call (`tool_name: "Write"`, `tool_use_id`, `tool_input`).

Source: https://code.claude.com/docs/en/hooks §"Exit code 2", §"PreToolUse decision control".
Also Experiment 2 (this session). Verified on 2.1.278.

---

## Item 3  -  Stop / SubagentStop blocking, `stop_hook_active`, how the reason reaches the model

- **Decision fields**: top-level `decision: "block"` + required `reason` (shown to Claude as
  why it should continue), or `hookSpecificOutput.additionalContext` for non-error feedback
  that keeps the conversation going (shown in transcript as "Stop hook feedback" rather than a
  hook-error). A hook that blocks via exit code 2 routes the same way as `reason`: stderr
  becomes the continuation message Claude sees.
- **`stop_hook_active`**: `true` when Claude Code is already continuing as a result of a
  previous Stop-hook block on this turn. Docs: "Check this value... to avoid blocking on a
  condition that will never resolve. Claude Code overrides the hook and ends the turn after 8
  consecutive blocks."
- **SubagentStop**: identical decision-control shape; blocking a subagent's stop delivers
  `reason` "to the subagent as its next instruction" (its own conversation continues, not the
  parent's). To inject something into the *parent* after a subagent returns, use `PostToolUse`
  on the `Agent` tool instead.
- **Confirmed by experiment** (Experiment 2): a `Stop` hook script read stdin, and on the first
  Stop call (`stop_hook_active: false` in the logged JSON) it printed
  `{"decision":"block","reason":"Please say the exact word DONE-MARKER before stopping."}`
  and exited 0. Claude continued and its final result text began literally with `DONE-MARKER`
   -  i.e., the injected `reason` string reached the model and it acted on it verbatim. The
  logged second `Stop` call showed `stop_hook_active: true`; the hook then exited 0 with no
  JSON, and the run ended normally.

Source: https://code.claude.com/docs/en/hooks §"Stop input", §"Stop decision control",
§"SubagentStop". Also Experiment 2. Verified on 2.1.278.

---

## Item 4  -  SessionStart / UserPromptSubmit: stdout vs `additionalContext`

Both events add **plain stdout directly as context** on exit 0 (one of only four events where
this is true  -  the others are `UserPromptExpansion` and `PostModelSwitch`). For most other
events, stdout is only written to the debug log and Claude never sees it.

- **SessionStart**: "Claude Code adds stdout it treats as plain text to Claude's context." A
  hook that only loads context can print plain text with no JSON wrapper. The JSON form
  (`hookSpecificOutput.additionalContext`) is needed only to combine context with other
  fields like `sessionTitle`, `initialUserMessage`, `watchPaths`, `reloadSkills`. Delivery
  point: "at the start of the conversation, before the first prompt."
- **UserPromptSubmit**: same duality  -  plain stdout is added as context, or
  `hookSpecificOutput.additionalContext` for more control (plus `decision: "block"` to reject
  the prompt, `sessionTitle`, `suppressOriginalPrompt`). Delivery point: "alongside the
  submitted prompt." Neither channel produces a visible transcript entry; both are injected as
  a system reminder.
- Confirmed by experiment: our `SessionStart` hook only `cat`'d stdin to a log and printed
  nothing meaningful to stdout, so this is documentary, not independently re-derived; the doc
  text itself is unambiguous and consistent across two independently fetched sections.

Source: https://code.claude.com/docs/en/hooks §"Exit code 0", §"SessionStart decision
control", §"UserPromptSubmit decision control", §"Add context for Claude". Verified on
2.1.278.

---

## Item 5  -  Plugin `hooks/hooks.json`: classic entries beside `modules`; `${CLAUDE_PLUGIN_ROOT}`; matcher syntax; per-hook `timeout`

- **Real file inspected**: `<repo>/hooks/hooks.json`:
  ```json
  {
    "description": "ale-board: the /ale-board task board pane and status band above the prompt. Needs CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1; builds without function hooks ignore the modules key.",
    "modules": ["../mod/register.tsx"]
  }
  ```
  This file has **no classic `hooks` key**  -  only `modules`. Its own `description` string
  implies a "modules only, ignored by builds without function hooks" fallback, which suggests
  the two *can* coexist in principle (a build with function hooks reads `modules`; one without
  ignores it and presumably would read a `hooks` key if present) but this file does not
  actually demonstrate a `hooks` key and a `modules` key side by side.
- **Official plugins reference** (https://code.claude.com/docs/en/plugins-reference) documents
  `hooks/hooks.json` (classic hook config: `{"hooks": {...}}`, matchers, command/http/mcp_tool/
  prompt/agent hooks, `description` field) and the `hooks` field in `plugin.json` (points to a
  hooks config file). **It contains no mention of a `modules` key at all.** The `modules` /
  `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS` mechanism appears to be a separate, undocumented (in the
  hooks/plugins reference pages fetched) "function hooks" feature  -  **COULD NOT VERIFY** its
  registration semantics, event set, or whether it can coexist with classic `hooks` in the
  same `hooks.json` from the public docs. Tried: full-text search of both
  `code.claude.com/docs/en/hooks` and `.../plugins-reference` for "modules" and
  "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"  -  zero hits in either page. This env var and the
  `modules` key are plugin-repo-specific / internal-preview surface, not in the two canonical
  doc pages fetched.
- **`${CLAUDE_PLUGIN_ROOT}`**: confirmed. "the plugin's installation directory, for scripts
  bundled with a plugin. Changes on each plugin update." Also `${CLAUDE_PLUGIN_DATA}` (the
  plugin's persistent data dir, survives updates) and `${CLAUDE_PROJECT_DIR}` (project root
  where the session started, stable across worktree `cd`). All three are exported as env vars
  on the spawned hook process regardless of exec/shell form.
- **Matcher syntax for several tools**: `"Edit|Write"` or `"Edit, Write"` (pipe or comma,
  optional whitespace, requires v2.1.191+ for the comma form). Exact-string matching applies
  when the matcher contains only letters/digits/`_`/`-`/spaces/`,`/`|`; anything else (e.g. a
  bare regex char) is evaluated as an unanchored JS regex  -  `Edit.*` matches both `Edit` and
  `NotebookEdit`, so anchor with `^...$` for whole-string match. MCP tools: `mcp__<server>__.*`
  (the `.*` is required  -  a bare `mcp__memory` is treated as an exact string and matches
  nothing).
- **Per-hook `timeout`**: a field on each hook handler (seconds before cancel). Defaults: 600s
  for `command`/`http`/`mcp_tool`, 30s for `prompt`, 60s for `agent`; lowered to 30s on
  `UserPromptSubmit`/`PreModelSwitch`/`PostModelSwitch`, 10s on `MessageDisplay`. Example from
  docs: `{"type": "command", "command": "...", "timeout": 30}` in a plugin's `hooks.json`.

Source: `<repo>/hooks/hooks.json` (read directly, not modified);
https://code.claude.com/docs/en/plugins-reference §"Hooks", §"Reference scripts by path" table
(implicit via plugin.json fields); https://code.claude.com/docs/en/hooks §"Matcher patterns",
§"Match MCP tools", §"Reference scripts by path", §"Common fields" (`timeout` row). Verified on
2.1.278. The `modules`/function-hooks mechanism itself: **COULD NOT VERIFY** beyond the one
file's self-description.

---

## Item 6  -  THE BINDING QUESTION: does hook input distinguish subagents?

**Answer: YES  -  branch (A).** `agent_id` and `agent_type` are present on every hook event
fired from inside a subagent (in-session `Agent`/`Task` tool call), and absent on the parent's
own tool calls. `session_id` is identical between parent and subagent (same session), so the
correct compound key is **`(session_id, agent_id)`**, not `session_id` alone.

**Decisive evidence  -  experiment**, not docs alone (see "Experiments run" below for the full
setup). Ran `command claude -p "Create a file named a.txt containing the word hi, then use the
Agent tool to have a subagent create b.txt containing the word yo." --output-format json
--permission-mode acceptEdits` in a fresh temp dir with a `PreToolUse`/`PostToolUse` hook that
appended its stdin JSON to a log file. The two `PreToolUse` events for the two `Write` calls,
one from the parent and one from the subagent, redacting nothing:

**Parent's `PreToolUse` for `Write`:**
```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "transcript_path": "~/.claude/projects/-private-tmp-ale-hooks-exp-XXXX/00000000-0000-0000-0000-000000000000.jsonl",
  "cwd": "/private/tmp/ale-hooks-exp-XXXX",
  "prompt_id": "aa644b25-9336-4eeb-a9e9-14673da46184",
  "permission_mode": "acceptEdits",
  "effort": { "level": "high" },
  "hook_event_name": "PreToolUse",
  "tool_name": "Write",
  "tool_input": { "file_path": "/private/tmp/ale-hooks-exp-XXXX/a.txt", "content": "hi\n" },
  "tool_use_id": "toolu_01Ae8LwJjDnrjHX8dVLq5yuF"
}
```

**Subagent's `PreToolUse` for `Write`** (same session, same prompt, different tool call):
```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "transcript_path": "~/.claude/projects/-private-tmp-ale-hooks-exp-XXXX/00000000-0000-0000-0000-000000000000.jsonl",
  "cwd": "/private/tmp/ale-hooks-exp-XXXX",
  "prompt_id": "aa644b25-9336-4eeb-a9e9-14673da46184",
  "permission_mode": "acceptEdits",
  "agent_id": "a97c5bb6077993a17",
  "agent_type": "general-purpose",
  "effort": { "level": "high" },
  "hook_event_name": "PreToolUse",
  "tool_name": "Write",
  "tool_input": { "file_path": "/private/tmp/ale-hooks-exp-XXXX/b.txt", "content": "yo\n" },
  "tool_use_id": "toolu_01JCFdjjzdRMKgajhFPbYRac"
}
```

Note `session_id` and `transcript_path` are **identical** in both  -  a subagent does *not* get
its own `session_id`. Only `agent_id` (unique per subagent run) and `agent_type` (the subagent
kind, e.g. `"general-purpose"`, `"Explore"`) distinguish the subagent's calls. The `SubagentStop`
event for the same run additionally carries `agent_transcript_path` (see Item 7), pointing to a
*separate* transcript file for the subagent, distinct from the parent's `transcript_path`.

This matches the docs verbatim: "When running with `--agent` or inside a subagent, two
additional fields are included: `agent_id` ... `agent_type` ... Use this to distinguish
subagent hook calls from main-thread calls." (https://code.claude.com/docs/en/hooks
§"Common input fields"). The docs claim was independently reproduced by experiment, so this is
evidence type **experiment**, corroborated by doc text.

Source: Experiment (this session, log lines from `hook-log.jsonl` in a throwaway `/tmp` dir,
deleted after use  -  see "Experiments run"); https://code.claude.com/docs/en/hooks
§"Common input fields". Verified on 2.1.278.

---

## Item 7  -  Transcript JSONL: usage location, subagent messages, dedup key

Inspected the real transcript from Experiment 1, whose path came from `transcript_path` in the
hook input: `~/.claude/projects/-private-tmp-ale-hooks-exp-XXXX/00000000-...jsonl` (52
lines), plus the subagent's separate transcript at
`.../00000000-.../subagents/agent-<id>.jsonl` (26 lines, path taken from
`agent_transcript_path` on the logged `SubagentStop` event).

- **Where per-message usage lives**: on `assistant`-type transcript entries, at
  `message.usage`. Exact keys present in this session (Sonnet/Opus mixed run):
  `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`,
  `output_tokens_details` (nested, e.g. `thinking_tokens`), `server_tool_use`, `service_tier`,
  `cache_creation` (nested `ephemeral_1h_input_tokens` / `ephemeral_5m_input_tokens`),
  `inference_geo`, `iterations` (array of per-iteration sub-objects with their own token
  counts), `speed`. This matches the brief's expected keys
  (`input_tokens`/`output_tokens`/`cache_read_input_tokens`/`cache_creation_input_tokens`) plus
  several extra fields not mentioned in the brief.
- **Subagent messages  -  same file or separate?** **Separate file.** The main session
  transcript has zero `isSidechain: true` entries in this run (all 52 lines are
  `isSidechain: false` or have no such field, e.g. `queue-operation`, `attachment`,
  `last-prompt`, `atis-latch`, `cost-state` housekeeping types). The subagent's own file (path
  from `SubagentStop`'s `agent_transcript_path`) has 26 lines, **all** `isSidechain: true`,
  with its own `user`/`assistant`/`attachment` entries and `parentUuid` chaining. So in this
  installed version, `isSidechain` does exist as a field but subagent messages live in a
  wholly separate per-agent file under a `subagents/` subfolder of the session directory, not
  interleaved into the main file. (The brief asked to check "whether subagent messages are in
  the same file or a separate file"  -  answer: separate file; `isSidechain` marks entries within
  that separate file, not a flag distinguishing rows inside one shared file.)
- **What identifies a message uniquely for de-duplication**: two candidate keys were present
  on each `assistant` entry:
  - `uuid`  -  a transcript-local UUID unique per JSONL line (e.g. `60f7ed9e-088b-4b7c-8477-bf3f34b0bf7b`).
  - `message.id`  -  the underlying API response id, format `msg_...` (e.g.
    `msg_011CfEfi7omC5PkZ9Zsannry`), plus a top-level `requestId` (`req_...`).
  The hooks doc separately warns (§"Common input fields", `message_id` under `MessageDisplay`)
  that a *different* `message_id` field on `MessageDisplay` input "is not the API `msg_…` id, so
  it can't be correlated with transcript message ids"  -  meaning the transcript's own
  `message.id` **is** the API msg id and is the correlatable identity across a resume/replay,
  whereas the per-line `uuid` is a Claude-Code-internal identity. For usage de-duplication
  across a `--resume`, keying off `message.id` (the API id) is the safer choice, since a
  resumed session's hook-injected context can be replayed as noted under "Add context for
  Claude" ("Claude Code replays the saved text rather than re-running the hook for past
  turns"), and re-running the same API request an be visible as a repeated `uuid` in some
  code paths but must always carry the same `message.id`. **This exact recommendation is my
  inference from the fields observed, not a doc statement**  -  the docs did not explicitly
  state which field to use for usage dedup on resume; flagging this as inferred, not primary-
  sourced.

Source: Experiment 1 transcript files (paths above, read directly, not modified);
https://code.claude.com/docs/en/hooks §"MessageDisplay input" (for the `message_id` vs API-id
distinction) and §"Add context for Claude" (for replay-on-resume behavior). Verified on
2.1.278. The dedup-key recommendation itself: **partially COULD NOT VERIFY**  -  inferred from
field shapes, not stated by docs.

---

## Item 8  -  Headless flags; env var passthrough; do plugin hooks run in `-p` mode?

- **Flags** (from `command claude --help`, this install):
  - `-p, --print`: "Print response and exit (useful for pipes)... The workspace trust dialog is
    skipped when Claude is run in non-interactive mode (via -p, or when stdout is not a TTY)."
  - `--model <model>`: alias (`sonnet`, `opus`, `fable`) or full model name.
  - `--output-format <format>`: `text` (default), `json` (single result), `stream-json`
    (only with `-p`).
  - `--permission-mode <mode>`: `acceptEdits`, `auto`, `bypassPermissions`, `manual`,
    `dontAsk`, `plan`.
  - `--dangerously-skip-permissions` / `--allow-dangerously-skip-permissions`: bypass all
    permission checks.
  - `--bare`: "skip hooks, LSP, plugin sync, attribution, auto-memory, background prefetches,
    keychain reads, and CLAUDE.md auto-discovery."
  - `--add-dir`, `--agent`, `--agents <json>`, `--allowedTools`, `--permission-prompts none`
    (deny anything that would need a human, still governed by `PermissionRequest` hooks and
    permission rules first).
- **Env var passthrough**: a hook process "inherits the parent environment, apart from the
  `OTEL_*` exporter variables that Claude Code removes from every subprocess it spawns and,
  when `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` is set to `1`, the variables it strips." So ordinary
  env vars (like a hypothetical `ALE_TASK`) pass straight through to hook subprocesses  -  this
  is exactly why the brief's premise holds ("ALE_TASK cannot identify [a same-session
  subagent]"  -  because it's inherited unchanged by both parent and subagent hook processes).
- **Do plugin hooks run in `-p` mode?** **Yes, by default; no with `--bare`.** From the headless
  docs: "Without `--bare`, a `-p` session runs the hooks in a project's `.claude/settings.json`
  and connects the servers in its `.mcp.json`, even in a folder you've never trusted. A `-p`
  session shows no workspace trust dialog and no per-server approval prompt." And: "`--bare`...
  A hook in a teammate's `~/.claude`... won't run, because bare mode never reads them." This was
  independently confirmed by experiment: both experiment sessions ran `command claude -p ...`
  *without* `--bare`, from outside any git repo, in a directory with only a
  `.claude/settings.json` (no plugin involved)  -  the `SessionStart`, `PreToolUse`, `PostToolUse`,
  `Stop`, `SubagentStop`, and `UserPromptSubmit` hooks all fired and logged as expected (see
  Items 2, 3, 6). Plugin-specific hooks (`hooks/hooks.json` inside an installed plugin) were not
  separately tested  -  this experiment used project-scope `.claude/settings.json` hooks only, so
  the plugin-hooks-in-`-p` claim rests on the doc quote above, not a plugin-specific rerun of the
  experiment. Marking the plugin-specific sub-claim **partially COULD NOT VERIFY by experiment**
  (settings-file hooks in `-p` mode: verified by experiment; plugin-file hooks in `-p` mode:
  doc-only).

Source: `command claude --help` (this install, 2.1.278); https://code.claude.com/docs/en/hooks
§"Common input fields" (env inheritance); https://code.claude.com/docs/en/headless
§"Start faster with bare mode". Also Experiments 1 and 2 (settings-file hooks fired under `-p`
without `--bare`). Verified on 2.1.278.

---

## Design fork, item 6

**Branch (A) holds: hook input identifies subagents.** Every `PreToolUse`/`PostToolUse`/
`SubagentStop` event fired from inside an in-session `Agent`/`Task`-tool subagent carries
`agent_id` (a run-unique string, e.g. `"a97c5bb6077993a17"`) and `agent_type` (the subagent
kind, e.g. `"general-purpose"`), while the parent's own tool calls carry neither field.
`session_id` is shared between parent and subagent, so bindings should be keyed on the compound
`(session_id, agent_id)`, not `session_id` alone and not `agent_id` alone (an `agent_id` is only
guaranteed unique within a session, not globally  -  not independently confirmed, but the field is
documented as "Identifier for the subagent run," scoped language consistent with per-session
uniqueness).

This directly falsifies the brief's fallback premise ("if it does not, ... hooks bind one
executor per session")  -  bindings do not need to collapse to one-per-session for in-session
subagents; they can be `(session_id, agent_id)`-scoped, with `agent_id` absent on the parent's
own calls acting as the natural "top-level executor" sentinel.

---

## Experiments run (2 headless sessions; a 3rd attempt failed before any hook fired and doesn't
count against the 4-session cap; all temp dirs deleted afterward)

1. **Experiment 1** (subagent identity + transcript structure, Items 1, 6, 7): fresh
   `/tmp/ale-hooks-exp-*` dir, outside any repo/worktree, with `.claude/settings.json`
   registering `PreToolUse`/`PostToolUse`/`SessionStart`/`Stop`/`SubagentStop`/
   `UserPromptSubmit` hooks whose command was `cat >> <dir>/hook-log.jsonl; echo >> ...`. Ran:
   ```
   command claude -p "Create a file named a.txt containing the word hi, then use the Agent tool to have a subagent create b.txt containing the word yo." --output-format json --permission-mode acceptEdits
   ```
   (A first attempt additionally passed `--dangerously-skip-permissions`, was denied by the
   auto-mode classifier before any hook ran, and produced no log lines  -  excluded from the
   session count since nothing executed.) Result: 24 hook-log lines; both files created; used
   for Items 1, 6, 7 above.
2. **Experiment 2** (PreToolUse deny + Stop block, Items 2, 3, 8): fresh `/tmp/ale-hooks-exp2-*`
   dir, `.claude/settings.json` registering a `PreToolUse` hook on `Write` that logs and exits 2
   with a fixed stderr string, and a `Stop` hook that logs, then on the first call (
   `stop_hook_active` absent/false) prints `{"decision":"block","reason":"...DONE-MARKER..."}`
   and exits 0, and on the resumed call (`stop_hook_active: true`) exits 0 with no JSON. Ran:
   ```
   command claude -p "Create a file named c.txt containing the word test using the Write tool." --output-format json --permission-mode acceptEdits
   ```
   Result: `c.txt` never created (deny worked); result text began with `DONE-MARKER` (Stop
   block + `reason` reached the model verbatim); `permission_denials` in the JSON result
   recorded the blocked `Write` call; hook log showed `stop_hook_active: false` then `true`
   across the two `Stop` invocations.

Both `/tmp/ale-hooks-exp-*` and `/tmp/ale-hooks-exp2-*` directories, and all intermediate
`/tmp/hooks_full.txt` / `/tmp/plugins_ref_full.txt` / `/tmp/headless_full.txt` doc-fetch
scratch files, were deleted at the end of this task. No file under `~/.claude` was read,
written, or modified. Nothing was pushed. No credentials were printed (these were `-p`
sessions using the ambient `command claude` auth; no API key was ever echoed to logs or
transcript excerpts above).


---

# Part 2: Pi and Codex

# Harness facts: Pi and Codex (items 9–15)

Scope: items 9–15 cover Pi and Codex. Items 1–8 (Claude Code) are in Part 1 above.

Versions verified on:
- Pi: `pi --version` → `0.86.0`. Package: `@earendil-works/pi-coding-agent`, a global npm
  install (`$(npm root -g)/@earendil-works/pi-coding-agent`; `readlink -f $(which pi)` resolves
  to `.../dist/bundle/cli.js` inside the same package).
- Codex: `codex --version` → `codex-cli 0.155.1`. Standalone release
  binary (`0.155.1-aarch64-apple-darwin`).

No files under `~/.pi`, `~/.codex`, or `~/.claude` were modified. `~/.codex/config.toml` was read only for hook-related key names (`hooks`, `hooks.state.*`); no values were printed except the non-secret `hooks = <redacted-in-this-doc>` presence flag. `~/.codex/hooks.json` was read for its schema (event names, matcher, command)  -  see item 14; nothing in it is a secret. No login was performed, nothing was pushed.

---

## Item 9  -  `edit`/`write`/`bash` tool_call input field names

**Answer:** `edit`'s target-path field is `path` (not `file_path`). `write`'s target-path field is also `path`. `bash`'s command field is `command`.

Exact shapes, quoted from shipped `.d.ts` files:

- Edit (`dist/core/tools/edit.d.ts`):
  ```ts
  declare const editSchema: Type.TObject<{
      path: Type.TString;
      edits: Type.TArray<Type.TObject<{
          oldText: Type.TString;
          newText: Type.TString;
      }>>;
  }>;
  export type EditToolInput = Static<typeof editSchema>;
  ```
- Write (`dist/core/tools/write.d.ts`):
  ```ts
  declare const writeSchema: Type.TObject<{
      path: Type.TString;
      content: Type.TString;
  }>;
  export type WriteToolInput = Static<typeof writeSchema>;
  ```
- Bash (`dist/core/tools/bash.d.ts`):
  ```ts
  declare const bashSchema: Type.TObject<{
      command: Type.TString;
      timeout: Type.TOptional<Type.TNumber>;
  }>;
  export type BashToolInput = Static<typeof bashSchema>;
  ```

The `tool_call` event's `EditToolCallEvent`/`WriteToolCallEvent`/`BashToolCallEvent` (in `dist/core/extensions/types.d.ts`) carry these input types directly as `event.input`, e.g. `EditToolCallEvent extends ToolCallEventBase { toolName: "edit"; input: EditToolInput }`. So an ALE path-allowlist gate must read `event.input.path` for both `edit` and `write`.

Evidence: file paths above, quoted directly. Version: Pi 0.86.0.

---

## Item 10  -  What happens when a `tool_call` handler throws

**Answer for the design fork: a throwing `tool_call` handler results in DENY (the tool does not execute), not allow.**

This was determined by reading the dispatcher, not by experiment (reading was cheap and conclusive; no experiment was needed).

Two layers matter:

1. `runner.emitToolCall()` in `dist/core/extensions/runner.js` (line ~786) has **no try/catch** around the handler call  -  unlike its sibling `emitToolResult`/`emitUserBash`, which do wrap handlers in try/catch and call `this.emitError(...)` (swallowing the error and continuing). Quote:
   ```js
   async emitToolCall(event) {
       const ctx = this.createContext();
       let result;
       for (const { handlers } of snapshotEventHandlers(this.extensions, "tool_call")) {
           for (const handler of handlers) {
               const handlerResult = await handler(event, ctx);   // <-- no try/catch
               ...
           }
       }
       return result;
   }
   ```
   A throw here propagates out of `emitToolCall` as a rejected promise.

2. The caller, `agent-session.js` `_installAgentToolHooks()` (`this.agent.beforeToolCall`), **does** wrap the call in try/catch, and on any thrown error re-throws it (wrapping non-`Error` throws in a new `Error` whose message explicitly says "blocking execution"):
   ```js
   this.agent.beforeToolCall = async ({ toolCall, args }) => {
       const runner = this._extensionRunner;
       if (!runner.hasHandlers("tool_call")) return undefined;
       try {
           return await runner.emitToolCall({ type: "tool_call", toolName: toolCall.name, toolCallId: toolCall.id, input: args });
       }
       catch (err) {
           if (err instanceof Error) { throw err; }
           throw new Error(`Extension failed, blocking execution: ${String(err)}`);
       }
   };
   ```
   The error is re-thrown up into `pi-agent-core`'s `Agent.prompt`/tool-execution loop as a `beforeToolCall` failure, which stops that tool call from executing (the tool result becomes an error / the call is not run). The literal string "blocking execution" in the fallback error message is Pi's own description of this behavior.

Evidence: `dist/core/extensions/runner.js` lines ~786–801 (`emitToolCall`, no catch) and `dist/core/agent-session.js` lines ~231–251 (`beforeToolCall`, catch-and-rethrow with "blocking execution" wording). Version: Pi 0.86.0. Not separately confirmed by experiment (source is unambiguous and reading it is cheap/safe; running one would just re-observe the same thrown error at the CLI).

**Design-fork answer for item 10: DENY.**

---

## Item 11  -  Can anything stop the agent from finishing / does `sendUserMessage` in `agent_settled` start and get waited on in `-p` mode

**Answer: no automatic follow-up run happens in print (`-p`) mode, and if it did start, the process would not wait for it. This was verified by reading source (`print-mode.js`, `agent-session.js`), not by a live experiment  -  no working provider was configured in this environment, so no live `pi -p` run was attempted (0 live sessions run; see the count at the end of this file).**

Reasoning, quoted from source:

1. `runPrintMode` (`dist/modes/print-mode.js`) drives the whole non-interactive lifecycle. For each initial/queued message it does:
   ```js
   await rebindSession();
   if (initialMessage) { await session.prompt(initialMessage, { images: initialImages }); }
   for (const message of messages) { await session.prompt(message); }
   ...
   return exitCode;
   ```
   and then falls into a `finally` that calls `await disposeRuntime()` and returns. There is no loop that re-checks for newly queued messages after `session.prompt()` resolves  -  `runPrintMode` runs each prompt exactly once and exits.

2. `session.prompt()` → `_runAgentPrompt()` in `dist/core/agent-session.js` (line ~860):
   ```js
   async _runAgentPrompt(messages) {
       ...
       await this.agent.prompt(messages);
       while (await this._handlePostAgentRun()) {
           if (this._agentRunAbortRequested) break;
           await this.agent.continue();
       }
       finally {
           ...
           await this._emitAgentSettled();   // <-- fires AFTER the while loop exits
       }
   }
   ```
   `_handlePostAgentRun()` (line ~880) returns `false` once there is nothing left to continue (no retry, no compaction, no queued messages at that point)  -  its last line is `return !this._agentRunAbortRequested && this.agent.hasQueuedMessages();`. Once it returns `false`, the while loop exits and **then** `_emitAgentSettled()` fires the `agent_settled` extension event. The comment right above this code explicitly distinguishes `agent_end` (which the loop drains queues for) from this later point.

3. `pi.sendUserMessage(...)` called from inside an `agent_settled` handler goes through `bindCore`'s wiring in `agent-session.js` (line ~2122):
   ```js
   sendUserMessage: (content, options) => {
       this.sendUserMessage(content, options).catch((err) => {
           runner.emitError({ extensionPath: "<runtime>", event: "send_user_message", error: ... });
       });
   },
   ```
   This is **fire-and-forget**  -  the extension API's `pi.sendUserMessage` is a synchronous-looking call that kicks off an async operation without returning a promise the caller (or `_runAgentPrompt`/`runPrintMode`) awaits. Since `_runAgentPrompt` has already returned (its `finally` block, including `_emitAgentSettled()`, has completed) by the time `agent_settled` handlers run and call `sendUserMessage`, and `runPrintMode` immediately proceeds to dispose the runtime and exit after `session.prompt()` resolves, nothing in the print-mode call chain waits for this follow-up.

**Design-fork answer for item 11: NO  -  no follow-up run is possible/waited-on in `-p` mode.** This matches (and is evidence for) the plan's chosen fallback: "Pi's Stop behaviour degrades to run acceptance, submit on pass, raise `input-required` on fail, without blocking."

Evidence: `dist/modes/print-mode.js` (full file, 141 lines, quoted above), `dist/core/agent-session.js` lines ~860–914 (`_runAgentPrompt`, `_handlePostAgentRun`), lines ~2112–2130 (`bindCore` wiring of `sendUserMessage`/`sendMessage` as fire-and-forget with `.catch`). Version: Pi 0.86.0. **0 live experiments run**  -  no provider had working, already-configured credentials in this environment (see item 13), so the "confirm by experiment" fallback in the brief was skipped per its own instruction ("if no provider works, skip experiments and say so").

---

## Item 12  -  Exact token fields on `event.message.usage` at `message_end`/`turn_end`

**Answer:** both `MessageEndEvent.message` and `TurnEndEvent.message` are `AgentMessage`s; for an assistant message, `.usage` is a `Usage` object (non-optional on the wire type, defined in `@earendil-works/pi-ai`'s `types.d.ts`):

```ts
export interface Usage {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
    /** Subset of `cacheWrite` written with 1h retention. Only Anthropic reports this split. */
    cacheWrite1h?: number;
    /** Reasoning/thinking tokens, when the provider reports them. Subset of `output`. */
    reasoning?: number;
    totalTokens: number;
    cost: {
        input: number;
        output: number;
        cacheRead: number;
        cacheWrite: number;
        total: number;
    };
}
```
(`node_modules/@earendil-works/pi-ai/dist/types.d.ts`, line 270; `AssistantMessage` at line 353 declares `usage: Usage;` at line 364.)

`MessageEndEvent`/`TurnEndEvent` themselves (`dist/core/extensions/types.d.ts`) just carry the full `message: AgentMessage`, so extension code reads `event.message.usage.input` / `.output` / `.cacheRead` / `.cacheWrite` / `.totalTokens` / `.cost.total` etc. In `--mode json`, the wire event for `message_update` explicitly re-exposes this: `dist/modes/json-event.js`:
```js
return {
    type: "message_update",
    usage: event.message.usage,
    assistantMessageEvent: toJsonAssistantMessageEvent(event.assistantMessageEvent),
};
```
`message_end`/`turn_end` pass through unchanged (not `message_update`), so their JSON form carries the same `message.usage` nested under `message`.

Evidence: `node_modules/@earendil-works/pi-ai/dist/types.d.ts` lines 270–289 (Usage) and 353–364 (AssistantMessage.usage); `dist/core/extensions/types.d.ts` (`MessageEndEvent`, `TurnEndEvent` definitions); `dist/modes/json-event.js` (full file, quoted above). Version: Pi 0.86.0.

---

## Item 13  -  Exit codes of `pi -p`, `--mode json` event shapes carrying usage, OpenRouter `--provider` string

**Exit codes:** `runPrintMode()` (`dist/modes/print-mode.js`) returns `0` on success, `1` on an errored/aborted final assistant message (`stopReason === "error" | "aborted"`) or on any thrown error in its `try` block (caught at the bottom, `return 1`). Its caller in `dist/main.js` (line ~794–804):
```js
const exitCode = await runPrintMode(runtime, { mode: toPrintOutputMode(appMode), messages: parsed.messages, initialMessage, initialImages });
...
if (exitCode !== 0) { process.exitCode = exitCode; }
```
propagates that 0/1 as the process exit code (Node's default exit uses `process.exitCode` once the event loop drains  -  no separate `process.exit()` call for the normal print-mode path). Separately, `dist/main.js` has many explicit `process.exit(1)` calls for CLI/arg-parsing errors, and `process.exit(0)` for package-command success paths (`grep -n process.exit dist/main.js`, not all print-mode-specific).

**`--mode json` event shapes carrying usage:** confirmed for `message_update` above (item 12). `pi --help` confirms `--mode <mode>` accepts `text` (default), `json`, or `rpc`.

**OpenRouter provider string:** `docs/providers.md` (shipped doc) states:
```
- OpenRouter (OAuth-minted API key billed from OpenRouter credits)
...
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter` |
```
i.e. `--provider openrouter` (env var `OPENROUTER_API_KEY`). **COULD NOT VERIFY by `pi --list-models`**: this environment has no OpenRouter credentials configured (no OAuth login, no `OPENROUTER_API_KEY`), and `pi --list-models openrouter` returned `No models matching "openrouter"` (the local model catalogue only has `google` and `openai-codex` entries cached offline  -  29 models total, no `openrouter/*` rows). Per the brief's constraint ("do NOT run anything that needs a key you do not already have configured"), no login was attempted. The provider string `openrouter` is documented but the exact per-model IDs under it were not independently confirmed via `--list-models`.

Evidence: `dist/modes/print-mode.js` (quoted in item 11), `dist/main.js` lines ~794–804 and grep of `process.exit`, `docs/providers.md` lines 23–85, live `pi --help` output, live `pi --list-models` / `pi --list-models openrouter` output. Version: Pi 0.86.0.

---

## Item 14  -  Codex hook registration, events, and whether a hook can DENY a tool call

**Answer: YES  -  a Codex `PreToolUse` hook can deny a tool call.**

Registration mechanism (from official docs, `https://developers.openai.com/codex/hooks`, fetched via Tavily extract):
- Hooks are discovered as `hooks.json` files or inline `[hooks]` tables inside `config.toml`, at multiple layers: `~/.codex/hooks.json`, `~/.codex/config.toml`, `<project>/.codex/hooks.json`, `<project>/.codex/config.toml`, plus plugin-bundled hooks. "If more than one hook source exists, Codex loads all matching hooks."
- Non-managed hooks require explicit trust review (`/hooks` in the CLI) before they run; `--dangerously-bypass-hook-trust` (confirmed in `codex exec --help` output: "Run enabled hooks without requiring persisted hook trust for this invocation. DANGEROUS. Intended only for automation that already vets hook sources") skips that trust check for one invocation  -  this is exactly what implies a hook system exists, per the brief's premise.
- Events, quoted from the doc's table:
  ```
  | During a turn | PreToolUse, PermissionRequest, PostToolUse, PreCompact, PostCompact, UserPromptSubmit, SubagentStop, Stop |
  | When you interrupt an active turn | Interrupt (doesn't run for subagents) |
  | When a session or subagent starts | SessionStart, SubagentStart |
  | When the main thread ends | SessionEnd (doesn't run for subagents) |
  ```
- The verifying machine's `~/.codex/hooks.json` (read for schema/key-names only, no secrets  -  content is not sensitive, it's just hook wiring) currently registers:
  ```json
  {
    "hooks": {
      "SessionStart": [{ "hooks": [{ "command": "bash '~/.codex/herdr-agent-state.sh' session", "timeout": 10, "type": "command" }] }],
      "PreToolUse": [{ "matcher": "Edit|Write|MultiEdit", "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/guard-writes.py" }] }]
    }
  }
  ```
  confirming the shape: `{event: [{matcher?, hooks: [{type: "command", command, timeout?, statusMessage?, ...}]}]}`. `~/.codex/config.toml` additionally has non-secret keys `hooks` (a value, redacted here) and `hooks.state."<hooks.json path>:<event>:<idx>:<idx>"` entries recording trust state  -  read for key names only, no values printed.

**Denial mechanism**, quoted from the doc:
> JSON on `stdout` can use `systemMessage`. To deny a supported tool call, return this hook-specific shape:
> ```json
> { "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Destructive command blocked by hook." } }
> ```
> Codex also accepts this older block shape:
> ```json
> { "decision": "block", "reason": "Destructive command blocked by hook." }
> ```
> You can also use exit code `2` and write the blocking reason to `stderr`.

This is structurally identical to Claude Code's own `PreToolUse` deny contract (`hookSpecificOutput.permissionDecision`, exit code 2 + stderr)  -  a Codex ALE shim can reuse the same response shape.

Evidence: `codex exec --help` (live, quoted above, item on `--dangerously-bypass-hook-trust`), `https://developers.openai.com/codex/hooks` (fetched via `tvly extract`, quoted directly, three separate excerpts above), `~/.codex/hooks.json` (read locally, full content quoted  -  no secrets present), `~/.codex/config.toml` (grepped for `hook` key names only, values not printed). Version: Codex CLI 0.155.1.

**Design-fork answer for item 14: YES, Codex hooks can deny a tool call.**

---

## Item 15  -  `codex exec --json` usage event shapes; AGENTS.md loading; `--add-dir`/`--sandbox workspace-write`/`-C` as coarse containment

**Token usage in `codex exec --json`:** confirmed from `https://developers.openai.com/codex/non-interactive-mode` (fetched via `tvly extract`). JSONL event types: `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.*`, `error`. Usage rides on `turn.completed`:
```json
{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122,"reasoning_output_tokens":0}}
```
Key names: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`. (No local `~/.codex/sessions` run was inspected for a live sample  -  the doc's own worked example was used as the source instead, which is a primary source per the brief.)

**AGENTS.md loading rules**, quoted from `https://developers.openai.com/codex/agent-configuration/agents-md`:
> Codex builds an instruction chain when it starts (once per run; in the TUI this usually means once per launched session). Discovery follows this precedence order:
> 1. **Global scope:** In your Codex home directory (`~/.codex` unless `CODEX_HOME` is set), Codex reads `AGENTS.override.md` if it exists, otherwise `AGENTS.md`. Only the first non-empty file at this level is used.
> 2. **Project scope:** Starting at the project root (typically the Git root), Codex walks down to the current working directory. In each directory it checks `AGENTS.override.md`, then `AGENTS.md`, then any fallback names in `project_doc_fallback_filenames`. At most one file per directory.
> 3. **Merge order:** Codex concatenates files from the root down, joined by blank lines; files closer to the cwd override earlier guidance because they appear later in the combined prompt.
> Codex skips empty files and stops adding files once the combined size reaches `project_doc_max_bytes` (32 KiB default).

**`--add-dir` / `--sandbox workspace-write` / `-C` as coarse containment:**
- `codex exec --help` (live): `-C, --cd <DIR>`  -  "Tell the agent to use the specified directory as its working root"; `--add-dir <DIR>`  -  "Additional directories that should be writable alongside the primary workspace"; `-s, --sandbox <SANDBOX_MODE>`  -  `[possible values: read-only, workspace-write, danger-full-access]`.
- `https://developers.openai.com/codex/config-reference` documents the underlying config keys: `sandbox_mode` (`read-only | workspace-write | danger-full-access`), `sandbox_workspace_write.writable_roots` ("Additional writable roots when `sandbox_mode = "workspace-write"`"), plus `sandbox_workspace_write.exclude_slash_tmp`, `sandbox_workspace_write.exclude_tmpdir_env_var`, `sandbox_workspace_write.network_access`.
- This is a **coarse, path-root-level** containment: `-C`/`--cd` sets the working root, `--sandbox workspace-write` (`sandbox_mode`) restricts writes to that root (plus `/tmp`/`$TMPDIR` by default), and `--add-dir` (`sandbox_workspace_write.writable_roots`) adds extra writable directories. There is no evidence of a finer-grained, per-file or per-glob allowlist at the sandbox level (that granularity would have to come from a `PreToolUse` hook denying/rewriting individual `apply_patch`/shell calls, per item 14)  -  so for the ALE plan, sandbox scoping is "confine writes to N whole directories," not "confine writes to an arbitrary allowed-paths set," and a hook is still needed for exact-path enforcement.

Evidence: `codex exec --help` (live, quoted above and in item 14), `https://developers.openai.com/codex/non-interactive-mode` (fetched via `tvly extract`, quoted), `https://developers.openai.com/codex/agent-configuration/agents-md` (fetched via `tvly extract`, quoted), `https://developers.openai.com/codex/config-reference` (fetched via `tvly extract`, quoted). Version: Codex CLI 0.155.1.

---

## Design-fork rulings (for the lead to record in the SDD ledger)

- **Item 10  -  throw means:** **DENY.** A thrown `tool_call` handler propagates through `emitToolCall` (uncaught there) into `beforeToolCall`'s try/catch, which re-throws and blocks that tool call's execution (Pi's own fallback error text says "blocking execution"). The Pi shim does not need to add its own catch-and-block wrapper to get "throw = deny"  -  that is already Pi's native behavior  -  but the shim should still `try/catch` internally so it can produce a clean, explicit block reason rather than relying on Pi's generic wrapped-error message.
- **Item 11  -  follow-up possible in print mode:** **NO.** Confirmed by reading `print-mode.js` and `agent-session.js`: `agent_settled` fires only after the message-queue-draining loop in `_runAgentPrompt` has already exited, and `pi.sendUserMessage()` is fire-and-forget (not awaited by the runner), while `runPrintMode` disposes the runtime and exits right after `session.prompt()` resolves. **0 live `pi -p` experiments were run** to double-confirm this at runtime, because no provider had working, already-configured credentials in this environment; the brief explicitly permits skipping experiments in that case. The source evidence is unambiguous enough to not require it.
- **Item 14  -  Codex hooks can deny a tool call:** **YES.** `PreToolUse` hooks can return `hookSpecificOutput.permissionDecision: "deny"` (or the legacy `{decision:"block"}`, or exit code 2 + stderr) to block a tool call before it runs  -  same contract shape as Claude Code's `PreToolUse`.

## Items marked COULD NOT VERIFY

- Item 13 (partial): the exact list of OpenRouter model IDs under `--provider openrouter` via `pi --list-models`  -  the provider string itself (`openrouter`) is documented in the shipped `docs/providers.md`, but no OpenRouter credentials were configured in this environment, so `pi --list-models openrouter` returned no rows and the live model catalogue could not be inspected. Nothing riskier was attempted, per the brief's "skip if no provider works" instruction.
- Item 15 (minor): no live `~/.codex/sessions/*.jsonl` transcript was inspected for a `turn.completed` usage line from an actual run in this environment (none was readily at hand); the field names were instead taken directly from the official docs' own worked JSONL example, which is a primary source.

## Live sessions run

**0.** No `pi -p` or `codex exec` live sessions were run. Pi: no working provider was configured (see item 13), so per the brief's constraint ("An experiment is allowed ONLY if Pi already has a working provider configured... if no provider works, skip experiments and say so") no experiment was attempted; the throw-handling (item 10) and follow-up (item 11) questions were fully resolved by reading source. Codex: `--dangerously-bypass-hook-trust` and hook-denial behavior were confirmed from official docs and the `--help` output plus the existing local `~/.codex/hooks.json`/`config.toml` (read-only, no modification, no secrets printed); no `codex exec` run was needed or attempted, and none was required by the brief.

