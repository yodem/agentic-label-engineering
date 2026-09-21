# ALE hooks

ALE hooks resolve a binding, check the current lease, and apply deterministic
path and task rules. They do not call a model.

## Enforced where

| Rule | Claude Code | Codex | Pi | ale-exec |
| --- | --- | --- | --- | --- |
| Lease ownership | PreToolUse | PreToolUse | adapter | command guard |
| Edit path containment | PreToolUse | PreToolUse | adapter | `ale guard-path` |
| Automatic heartbeat | PostToolUse | adapter | adapter | heartbeat command |
| Acceptance before stop | Stop | adapter | adapter | `ale verify` |

## What hooks cannot do

A Bash tool can write anywhere. File-edit hooks cover edit tools only; shell
write containment is checked at `ale verify --base`.

Hooks also cannot recover a lost lease or replace human sign-off for manual
acceptance items. The executor must stop when the hook reports a lost lease.

The `bin/ale-hook` launcher checks the binding environment and
`$ALE_HOME/.ale/bindings` (or `$HOME/.ale/bindings`) before starting Python;
unbound calls take the shell fast path, measured below 25 ms per call in the
steady-state launcher test, while possibly bound calls continue to Python.
