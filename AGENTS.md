# AGENTS.md

Guidance for coding agents working **on** this repository. (Agents working **under** ALE, as
executors of a task, follow [EXECUTOR.md](EXECUTOR.md) instead.)

## What this is

ALE (Agentic Label Engineering) coordinates multi-agent coding work with typed task labels, an
append-only event log, code-run acceptance and a zero-LLM watchdog. Start with [README.md](README.md);
[docs/label-layer.md](docs/label-layer.md) is the reference for the run loop and label fields.

## Layout

| Path | Contents |
| --- | --- |
| `ale/` | The Python package and `ale` CLI (`ale/cli.py` is the entry point). No runtime dependencies. |
| `ale/labeling/` | Label cascade: rules, the optional command judge, shadow statistics. |
| `ale/schema/` | JSON schemas for labels, events, rosters and agents. |
| `agents/` | Agent definitions (Markdown with frontmatter), resolved by role, sub and phase. |
| `catalog/refs/` | Reference material the agent definitions cite. |
| `bin/` | POSIX shell launchers: `ale-hook`, `ale-spawn`, `ale-exec`. |
| `hooks/`, `skills/`, `.claude-plugin/` | The Claude Code plugin. |
| `mod/` | The `/ale-board` Claude Code Mod (TypeScript, tested with Bun). |
| `adapters/` | Pi extension and Codex hook config. |
| `docs/` | Reference docs and the `index.html` explainer. |
| `tests/` | Pytest suite, including end-to-end runs with fake executors in `tests/e2e/fakes/`. |

`setup.py` copies the plugin tree (`agents/`, `catalog/`, `bin/`, `hooks/`, `skills/`,
`.claude-plugin/`, `mod/`) into the wheel as `ale/_bundle`. `ale.paths.plugin_root()` is the plugin
directory (checkout or bundle); `ale.paths.import_root()` is what goes on `PYTHONPATH`. Dispatch
passes both, plus `ALE_PYTHON`, to workers; never use the plugin root as an import path. A new
runtime directory must be added to `BUNDLED` in `setup.py`, `MANIFEST.in` and
`scripts/release-check.sh` (tests check all three).

## Commands

```sh
pip install -e .                 # development install
uvx --python 3.9 pytest -q       # full Python suite (about 2 minutes)
uvx --python 3.9 pytest -q tests/test_cli.py -k verify   # a focused run
cd mod && bun test               # Mod tests
sh scripts/release-check.sh      # fails if plugin files changed without a version bump
```

Run the full suite before you report a change as done. Report failures with their output.

## Rules

- **Deterministic first.** Gates, routing, acceptance, liveness and lane choice are decided by code.
  Do not add a model call where a rule, a check or an exit code can decide. The judge only ever
  records shadow votes; it never changes a decision.
- **Python 3.9.** The package must import and run on 3.9: no `match`, no `X | Y` type unions at
  runtime, keep `from __future__ import annotations` in modules that use newer annotation syntax.
- **No runtime dependencies.** Standard library only in `ale/`.
- **The event log is append-only.** Never rewrite or truncate `events.jsonl`; state is computed from
  it. New event types go in `ale/schema/event_types.json`.
- **Exit codes are an interface**: 0 ok, 1 check failed, 2 usage, 3 claim lost, 4 lease lost,
  5 needs sign-off, 6 breaches. Do not repurpose them.
- **Tests never call a real judge.** The root `conftest.py` blocks `jev-ask` on `PATH`; use
  `tests/judge_fakes.py`. Tests that need git create a repository in `tmp_path`.
- **Portable content.** No absolute home paths, personal emails or machine-specific details in
  `agents/`, `catalog/`, docs or fixtures. `tests/test_agent_bundle.py` checks part of this.
- **Docs follow behaviour.** A user-visible change updates the matching file in `docs/` or the
  README, and adds a line to the top of `CHANGELOG.md`.
- **Versions move together.** A release bumps `.claude-plugin/plugin.json`, `pyproject.toml`,
  `ale/__init__.py` and `EXPECTED_VERSION` in `tests/test_release_metadata.py` to the same version.
