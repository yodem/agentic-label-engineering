# ALE Pi extension

This extension is active only when `ALE_TASK` is set. It translates Pi lifecycle and tool events into calls to `ale hook <event>` and delegates all executor decisions to ALE.

Install it for one run with `pi -e adapters/pi/ale.ts`, copy it to `~/.pi/agent/extensions/` for a user installation, or copy it to a project's `.pi/extensions/` for a project installation.

Headless use with Pi's default provider and model:

```sh
ALE_TASK=task-id ALE_AGENT=agent-id ALE_RUN_DIR=.ale/run ALE_ROSTER=.ale/roster pi -p "<prompt>" -e adapters/pi/ale.ts --no-session
```

To override the defaults, add `--provider openrouter --model <id>`.

The `bash` tool can still write anywhere because shell containment is outside file-edit path checks; run `ale verify --base` for the containment backstop. In print mode there is no follow-up run at the end: `agent_settled` reports usage and calls `ale hook stop`, then Pi exits.
