# ALE executor protocol (v1.0)

You are an executor. You were given a task id, an agent id, and a label file.
The environment provides ALE_TASK, ALE_AGENT, ALE_RUN_DIR, and ALE_ROSTER.
Run every command from the project root. The companion
[ale-executor skill](skills/ale-executor/SKILL.md) gives the short model-facing
version of this protocol.

1. Read your label: `$ALE_RUN_DIR/labels/<task>.json`. Read `context.spec_path`, every file in
   `context.pointers`, and `$ALE_RUN_DIR/decisions.md`. If a handoff file for this task already exists
   under `$ALE_RUN_DIR/handoff/`, read it and continue from its Completed list. Do not redo finished steps.
2. Claim: `python3 -m ale claim --task <task> --agent <agent>`. Exit 3 means someone else owns it. Stop.
3. Read the deep reference before editing. When your prompt's `Deep reference` section names a
   `prefetched:` file, read it (its first line is `ALE-REFS-TOKEN: <token>`); otherwise run its
   `read first` and `how to read` commands. Then run
   `$ALE_BIN refs-ack --token <ALE-REFS-TOKEN line> --summary "<what applied>"`. The ack is report
   only: `ale verify` records `refs_read`, and a wrong token shows as a mismatch.
4. Work only inside `context.allowed_paths`. Editing anything else gets your work rejected.
   If the task truly needs a path outside them, ask with `input-required` (step 7); the lead widens
   the scope with `ale rescope`. Never edit label files.
   A headless Claude run is pre-approved for edits inside the worktree, `git add|commit|status|diff|log`,
   the ALE commands this protocol names (`python3 -m ale …`, `$ALE_BIN …`) and your label's
   acceptance commands typed exactly as written (one command, not chained with `&&`). Anything else
   that needs approval, including reads and writes outside the worktree, is refused: treat that as step 7.
5. After each completed step: `python3 -m ale heartbeat --task <task> --agent <agent> --step "<what you just finished>" --files a,b`.
   Heartbeat at least every 10 minutes. No heartbeat means your claim expires and the task is given away.
6. Exit 4 from any command means you lost the lease. Stop immediately. Do not write more files.
7. Blocked, missing a dependency, denied a permission, or the task is bigger than the label says:
   `python3 -m ale input-required --task <task> --agent <agent> --question "<one specific question>"`, then stop and wait.
   Never work around a restriction. Never relabel your own task.
8. A decision that other executors must follow (an interface, a name, a format):
   `python3 -m ale note --task <task> --agent <agent> --text "<decision needed or made>"`. The orchestrator records decisions.
9. When the acceptance commands in your label pass on your machine:
   `python3 -m ale submit --task <task> --agent <agent> --summary "<what changed and how you checked it>"`.
10. You cannot mark a task accepted. The verifier runs the acceptance commands itself. If it rejects, the next
   attempt receives the failure output.
11. Do not record token usage yourself. The orchestrator or an adapter records usage for the task.

Lead-side verification can use `ale verify --reject "reason"` to record a manual rejection without running acceptance commands. `ale reopen` returns a rejected or accepted-but-unintegrated task to verification; `ale fix` returns a rejection to implementation work. A parent gets at most two fix tasks, and fix tasks cannot be fixed again; escalate further repair to the lead.

`ale dispatch --no-exec` creates and records the task worktree and branch without launching an executor, for work the lead will execute manually.
