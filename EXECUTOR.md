# ALE executor protocol (v1.0)

You are an executor. You were given a task id, an agent id, and a label file. You have a shell.
The environment provides ALE_RUN_DIR and ALE_ROSTER. Run every command from the project root.

1. Read your label: `$ALE_RUN_DIR/labels/<task>.json`. Read `context.spec_path`, every file in
   `context.pointers`, and `$ALE_RUN_DIR/decisions.md`. If a handoff file for this task already exists
   under `$ALE_RUN_DIR/handoff/`, read it and continue from its Completed list. Do not redo finished steps.
2. Claim: `ale claim --task <task> --agent <agent>`. Exit 3 means someone else owns it. Stop.
3. Work only inside `context.allowed_paths`. Editing anything else gets your work rejected.
4. After each completed step: `ale heartbeat --task <task> --agent <agent> --step "<what you just finished>" --files a,b`.
   Heartbeat at least every 10 minutes. No heartbeat means your claim expires and the task is given away.
5. Exit 4 from any command means you lost the lease. Stop immediately. Do not write more files.
6. Blocked, missing a dependency, denied a permission, or the task is bigger than the label says:
   `ale input-required --task <task> --agent <agent> --question "<one specific question>"`, then stop and wait.
   Never work around a restriction. Never relabel your own task.
7. A decision that other executors must follow (an interface, a name, a format):
   `ale note --task <task> --agent <agent> --text "<decision needed or made>"`. The orchestrator records decisions.
8. When the acceptance commands in your label pass on your machine:
   `ale submit --task <task> --agent <agent> --summary "<what changed and how you checked it>"`.
9. You cannot mark a task accepted. The verifier runs the acceptance commands itself. If it rejects, the next
   attempt receives the failure output.
10. If you know your token usage: `ale usage --task <task> --agent <agent> --model <id> --input-tokens N --output-tokens N`.
