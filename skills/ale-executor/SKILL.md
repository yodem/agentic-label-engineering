---
name: ale-executor
description: Execute an ALE task while respecting its lease, path boundary, acceptance gate, and event protocol.
---

# ALE executor

Use this skill when `ALE_TASK`, `ALE_AGENT`, `ALE_RUN_DIR`, and `ALE_ROSTER`
identify an ALE executor session. Hooks enforce the protocol; this skill
explains how to cooperate with them.

## Start

Read the task label, its pointers, the decisions file, and any existing
handoff. Then claim the task:

```sh
python3 -m ale claim --task "$ALE_TASK" --agent "$ALE_AGENT"
```

Exit 3 means another executor owns the task. Stop. A SessionStart hook may
already have claimed it and injected the task context.

## While working

Work only in `context.allowed_paths`. Edit-tool hooks check the lease and the
path boundary before the edit. A deny means stop immediately. Do not retry by
switching tools, using a shell write, or changing the path: shell writes are
checked later by `ale verify --base`, and routing around a deny can invalidate
the work.

Automatic heartbeats prove liveness only. After a meaningful completed step,
write a real progress heartbeat:

```sh
python3 -m ale heartbeat --task "$ALE_TASK" --agent "$ALE_AGENT" --step "finished one meaningful step" --files path/to/file
```

Exit 4 means the lease is lost. Stop writing files. If blocked or a required
dependency is missing, ask one specific question:

```sh
python3 -m ale input-required --task "$ALE_TASK" --agent "$ALE_AGENT" --question "Which interface should this use?"
```

Record decisions that other executors need to follow:

```sh
python3 -m ale note --task "$ALE_TASK" --agent "$ALE_AGENT" --text "The shared format is ..."
```

## Finish

Run the label's acceptance commands. Submit only after they pass:

```sh
python3 -m ale submit --task "$ALE_TASK" --agent "$ALE_AGENT" --summary "Implemented the task and verified acceptance."
```

The stop gate runs acceptance independently. It may block a stop with failing
output, then asks for input after its bounded retry count. Manual acceptance
items remain for sign-off. Never mark a task accepted yourself; verification
does that:

```sh
python3 -m ale verify --task "$ALE_TASK" --base main
```

For protocol details and recovery guidance, read [EXECUTOR.md](../../EXECUTOR.md)
and [the hook support matrix](../../docs/hooks.md).
