---
name: board
description: Open the ALE web board for a run in the browser.
---

# Open the ALE web board

Use `/ale:board [run-dir]` to start or reuse the web board for an ALE run.

1. Resolve the run directory in this order:
   - Use the supplied `run-dir` argument. Resolve a relative run ID under the repo's `.ale/runs` directory.
   - Otherwise, find the git root from the current directory and use its `.ale/runs/current` target (or the run ID stored in that file).
   - Otherwise, choose the newest run directory under the git root's `.ale/runs`, based on modification time.
   If no run can be found, explain that and stop.
2. Resolve the run's repository root from its path under `.ale/runs`. Use `<repo>/.ale/roster.json` when it exists; otherwise use `${CLAUDE_PLUGIN_ROOT}/ale/example_roster.json`.
3. Read `<run-dir>/board.json`. If its URL is present and its recorded process is still serving this run, reuse that URL and do not start another server.
4. Otherwise start the bundled server in the background, capture its output, and wait for its URL (up to 10 seconds):

   ```bash
   board_log=$(mktemp)
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m ale board --run-dir <run-dir> --roster <roster> --open >"$board_log" 2>&1 &
   board_pid=$!
   for attempt in $(seq 1 50); do
     board_url=$(sed -n 's/.*\(http:\/\/127\.0\.0\.1:[^[:space:]]*\).*/\1/p' "$board_log" | head -n 1)
     if [ -n "$board_url" ]; then break; fi
     kill -0 "$board_pid" 2>/dev/null || break
     sleep 0.2
   done
   cat "$board_log"
   ```

   Wait for the command to print its URL. Tell the user that URL. If the server fails to start or does not print a URL, report the error instead of claiming success.

Do not stop a reused or newly started server; the user can keep using the board after this command returns.
