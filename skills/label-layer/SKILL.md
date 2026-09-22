---
name: label-layer
description: Bake a plan into ALE labels, fill planning gaps, and run the deterministic label-layer workflow.
---

# Label layer

Given a plan path:

1. If `.ale/roster.json` is missing, run `ale setup`.
2. Bake labels and write provenance:

   ```sh
   ale plan bake PLAN.md --write
   ```

   Judging is opt-in. Add `--judge` only when desired; it sends task text to the configured external API. `--no-judge` remains available for compatibility.

3. Fill every reported gap inside the plan's label blocks. Every task needs a written `lane_reason`, 2 to 5 acceptance commands, and all other required label fields. Add a monitor assignment only when risk is high or the run is unattended.
4. Run the workflow:

   ```sh
   ale run PLAN.md
   ```

5. Report the final status table, the timeline tail, and meta totals. If the command exits 6, identify the task and the reason it stopped.

Never edit a label after `init-run` except through `ale relabel`. Never mark a task done by hand. Verification and integration are determined by ALE's event-backed state.
