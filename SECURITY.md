# Security

## Reporting a vulnerability

Please report security issues privately through GitHub:
**Security → Report a vulnerability** on this repository
([private advisory form](https://github.com/yodem/agentic-label-engineering/security/advisories/new)).
Do not open a public issue for a vulnerability.

Include the ALE version (`.claude-plugin/plugin.json`), the harness and its version if relevant,
and the smallest reproduction you can. You should get an acknowledgement within a week.

## Trust model

ALE coordinates cooperating agents on your own machine. Know what it does and does not protect.

**Labels are code.** Acceptance commands in a label run through the shell (`shell=True`) with your
user's permissions, in the task's working tree. Only run labels that you or your orchestrator wrote.
Treat a plan or label from someone else like a script from someone else.

**Agent identity is self-asserted.** The event log refuses events from an agent that does not own
the task, which catches accidents and honest mistakes. It does not stop a malicious local process
that writes forged events directly to `events.jsonl`.

**Path guards cover edit tools, not shells.** Hooks deny file edits outside `allowed_paths`, but an
agent's shell tool can write anywhere. `ale verify --base` checks the final changed paths against the
label and is the containment backstop. Run executors in a worktree per task.

**The judge is off by default.** When you turn it on, task text is sent to whatever your judge command
calls, which may be a hosted model. See [docs/judge.md](docs/judge.md).

**Local filesystems only.** The append-only guarantee of the event log depends on POSIX append
semantics and does not hold on network mounts.

## In scope

- A way for an executor to get a task marked `accepted` without `ale verify` running its acceptance.
- A path that escapes `allowed_paths` and still passes `ale verify --base`.
- Hook behaviour that lets an unbound session be treated as bound, or the reverse.
- Anything that sends data off the machine when the judge is off.

Running a label you did not write, or a local process forging events, is outside the trust model
described above.

## Headless Claude executor permissions

`claude -p` cannot show a permission prompt, so `bin/ale-spawn` gives a headless Claude executor a
prompt-free grant: `--permission-mode acceptEdits` and one `--allowedTools` list. The list holds
`git add`, `git commit`, `git status`, `git diff` and `git log`. It also holds the executor's ALE
subcommands (`claim`, `heartbeat`, `status`, `submit`, `usage`, `note`, `input-required`,
`refs-ack`) under `python3 -m ale`, `<ALE_PYTHON> -m ale`, `bin/ale-py` and `$ALE_BIN`. Rules
match the unexpanded text, so the `$ALE_BIN` rules are granted only when the `ALE_BIN` that
`ale-spawn` exports is exactly the `bin/ale-py` shim path; an inherited `ALE_BIN` naming another
program gets no `$ALE_BIN` rule. Lead-side
commands such as `rescope` and `accept` are not on it. The last entries are the task's acceptance
commands, verbatim. ALE never passes `bypassPermissions` or `--dangerously-skip-permissions`. A
read-only monitor (`ALE_READ_ONLY=1`) gets no grant. A roster opts out with
`harnesses.claude.permission_mode: null`.

What bounds the grant:
- `acceptEdits` approves edits only inside the working directory, which is the task worktree.
  Reads and writes outside it, including the run directory, are refused.
- ALE's PreToolUse hook denies an edit-tool write outside the label's `allowed_paths`, with
  `edit path outside allowed_paths`.
- Any other Bash command needs a prompt that `-p` cannot show, so it is refused.

What is not guarded:
- An allowed acceptance command (a test runner, `make`, a script) runs arbitrary code. It can write
  anywhere the user's account can.
- The hook checks Bash only in the `cat > path <<` form. `acceptEdits` also auto-approves
  `mkdir`, `touch`, `mv` and `cp` inside the worktree. Those writes can land outside
  `allowed_paths` without a hook denial.

This is a bounded grant, not a filesystem sandbox. Details and evidence:
[docs/harness-facts.md](docs/harness-facts.md), item 16.
