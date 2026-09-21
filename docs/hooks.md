# ALE hooks

ALE hooks resolve a binding, check the current lease, and apply deterministic
path and task rules. They do not call a model.

## Enforced where

| Rule | Claude Code hooks | Codex hooks | Pi extension | ale-exec wrapper |
| --- | --- | --- | --- | --- |
| Path guard | Enforced: PreToolUse edit denial | Enforced: PreToolUse denial | Enforced: edit and write events | Not possible: verify only |
| Auto heartbeat | Enforced: PostToolUse, 60 s throttle | Not possible: current adapter has no PostToolUse entry | Enforced: post-tool hook | Enforced: timer heartbeat |
| Stop/submit gate | Enforced: Stop acceptance gate | Not possible: current adapter has no Stop entry | Advisory: settlement runs the gate but print mode cannot block | Enforced: exit-time `check` then submit or input-required |
| Usage capture | Enforced: transcript IDs are deduplicated | Advisory: wrapper parses Codex JSON when configured | Enforced: assistant message usage | Enforced: printed JSON usage when configured |
| Session context | Enforced: SessionStart stdout | Not possible: current adapter has no SessionStart entry | Enforced: session start injection | Not possible: wrapper has no context injection |

## What hooks cannot do

A Bash tool can write anywhere. File-edit hooks cover edit tools only; shell
write containment is checked at `ale verify --base`.

Hooks also cannot recover a lost lease or replace human sign-off for manual
acceptance items. The executor must stop when the hook reports a lost lease.

The `bin/ale-hook` launcher checks the binding environment and
`$ALE_HOME/.ale/bindings` (or `$HOME/.ale/bindings`) before starting Python;
unbound calls take the shell fast path, measured below 25 ms per call in the
steady-state launcher test, while possibly bound calls continue to Python.

The detailed harness facts and versions are recorded in
`docs/harness-facts.md`. The shipped Codex file contains only its PreToolUse
entry; the wrapper and Pi extension provide the additional capabilities shown
above through their own execution paths.
