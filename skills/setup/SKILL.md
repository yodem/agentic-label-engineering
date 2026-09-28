---
name: setup
description: Set up or reconfigure ALE for this repository. Detects the harnesses (claude, codex, pi), CandleKeep and remote hosts, asks the user each setup question, then writes the roster and checks it. Triggers on "set up ALE", "configure ALE", "ale setup", "/ale:setup".
---

# Set up ALE

`ale setup` configures six layers: the default harness, the model per harness and tier,
CandleKeep handbook refs, the remote host, the label judge, and free-form project notes
that every executor prompt carries. Code detects what the machine has and fills the
defaults; you ask the user, and code applies the answers.

## Safety

- Never ask for, repeat or write an API key, token or password. If the user offers one,
  tell them to export it in their own shell. `ale setup` refuses secret-looking answers.
- Setup writes only `.ale/roster.json` (excluded from git) and files under
  `${XDG_CONFIG_HOME:-$HOME/.config}/ale/`. Do not write anything else.
- Provider sign-in is the user's to do. Pass on the login commands that `--check` prints;
  do not run them.

## Procedure

1. **Questions.** Run `ale setup --questions --json`. Each item has `id`, `layer`,
   `prompt`, `kind` (`choice`, `text` or `bool`), `options` for a choice, and the
   `default` detected for this machine and repository.
2. **Ask.** Ask the user every question through your question tool, grouped by `layer`
   and at most four per call. Mark each default as Recommended. A `text` question takes
   free text; offer the default as the first option. Keep the question ids.
3. **Open question.** Finish with one open question:
   "Anything else executors should know about this project?"
   Its answer is `notes.project` (at most 2000 characters).
4. **Apply.** Write the answers as a JSON object of question id to answer in a temporary
   file outside the repository, then run `ale setup --answers <file>` (add `--force` only
   when `.ale/roster.json` does not exist yet and the user wants a fresh roster). Paste
   the report lines. Delete the temporary file.
5. **Handbook.** If `refs.book` is `build`, run the `agent-handbook` skill now, with the
   refs file path from `refs.file`.
6. **Check.** Run `ale setup --check` and paste its report. Exit 1 means a configured
   layer is broken (the refs file is missing, or the remote host is unreachable); say
   which, and what fixes it. Rerun `--check` after a fix.

## Answers file example

```json
{
  "harness.default": "claude",
  "models.codex.standard": "<model id>",
  "refs.book": "build",
  "refs.file": "<refs file path>",
  "remote.host": "none",
  "project.private": true,
  "judge.mode": "off",
  "notes.project": "Run the linter before submit."
}
```

Leave out any id the user skipped; setup keeps the roster value for it.
