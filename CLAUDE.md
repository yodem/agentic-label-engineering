@AGENTS.md

## Claude Code specifics

- Load this checkout as a plugin while developing: `claude --plugin-dir .`
  The `/ale-board` Mod additionally needs `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`; see `mod/README.md`.
- `claude plugin validate .` checks the manifest and lists the hooked events.
- The hooks in `hooks/hooks.json` run `bin/ale-hook`, which exits at once when no ALE task is bound,
  so they are safe to leave installed while you work on the repository itself.
- Hook contracts ALE depends on are recorded, with the version checked, in `docs/harness-facts.md`.
  Re-verify there before relying on a hook field that the file does not list.
