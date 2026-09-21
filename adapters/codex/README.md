# Codex hook adapter

Copy `hooks.json` into a Codex hook configuration and point `ALE_PLUGIN_ROOT`
at the ALE plugin directory. The adapter sends PreToolUse events to the same
deterministic `ale hook pre-tool` entry point used by Claude Code.

Shell writes are not intercepted by file-edit hooks. Use `ale verify --base`
to check the final changed paths.
