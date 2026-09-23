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
