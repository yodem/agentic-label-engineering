---
name: label-layer
description: Turn plan mode or superpowers plans into typed ALE tasks, dispatch them safely, and verify completion.
---

# Label layer

1. Start with a plan file. Run:

   ```sh
   ale plan bake PLAN.md
   ```

   Read every reported gap before editing the plan.

2. Fill each gap in this order:

   - `acceptance`: write 2 to 5 commands that exit 0 only when the task is truly done. Use a manual entry only when no command can decide.
   - `allowed_paths`: list the narrowest files or globs the task may change.
   - `lane` and `lane_reason`: answer the three planner questions and record why the lane fits. Never ask a model or Jev to choose the lane.

3. Add a `monitor` assignment only for high-risk or unattended work. Use `on_breach` unless a different trigger is justified.

4. Write the labels and start the run:

   ```sh
   ale plan bake PLAN.md --write
   ale init-run --plan PLAN.md
   ale dispatch --json
   ale dispatch --spawn
   ```

   Worktree isolation is the default for write tasks. Let dispatch create the task worktree.

5. On return, run `ale verify`. If it rejects, run `ale fix --task TASK` and verify the fix task before re-verifying the parent.

6. Integrate an accepted worktree with `ale integrate --task TASK`.

7. Inspect `ale timeline` and `ale meta` for event history and usage totals. Never mark a task done by hand. Only `ale verify` can produce `accepted`.
