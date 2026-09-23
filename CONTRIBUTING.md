# Contributing

Thanks for helping. ALE is small and opinionated. The most useful contributions are bug reports with a
reproduction, harness facts checked against a real CLI version, and focused fixes with tests.

## Set up

```sh
git clone https://github.com/yodem/agentic-label-engineering.git
cd agentic-label-engineering
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

The Python package has no runtime dependencies. `setup.py` copies the plugin tree
into the wheel as `ale/_bundle`; `ale.paths.plugin_root()` prefers the checkout when there is one. The test suite needs `pytest` (run it through
[`uv`](https://docs.astral.sh/uv/) as below, or `pip install pytest`). The Claude Code Mod under
`mod/` is TypeScript and is tested with [Bun](https://bun.sh).

Working with a coding agent? [AGENTS.md](AGENTS.md) holds the layout, commands and rules it needs;
[CLAUDE.md](CLAUDE.md) imports it for Claude Code.

## Test

```sh
uvx --python 3.9 pytest -q     # Python suite; 3.9 is the oldest supported version
cd mod && bun test             # Mod logic
```

Both must pass before a pull request is merged. A few rules the suite enforces:

- **No test may call a real judge.** The root `conftest.py` puts a guard first on `PATH` that fails
  any test executing `jev-ask`. Use the fakes in `tests/judge_fakes.py`.
- **Agent and catalog files stay portable.** `tests/test_agent_bundle.py` rejects absolute home paths
  and personal email addresses in `agents/` and `catalog/refs/`.
- Tests that need a git repository create one in `tmp_path`; never point a test at this checkout.

## Make a change

1. Open an issue first for anything bigger than a bug fix, so we can agree on the shape.
2. Keep changes deterministic where possible. ALE's rule is that anything code can decide (gates,
   routing, acceptance, liveness) is decided by code, not by a model.
3. Add or update tests with the change. Update the matching doc in `docs/` when behaviour changes.
4. Add a line to the top section of `CHANGELOG.md` describing the user-visible change.

## Releases

A release bumps the version in `.claude-plugin/plugin.json`, `pyproject.toml`, `ale/__init__.py` and
`tests/test_release_metadata.py` together, adds a
`CHANGELOG.md` section, and is tagged `vX.Y.Z`. `scripts/release-check.sh` fails when plugin files
changed since the last tag without a version bump.

## Harness facts

`docs/harness-facts.md` records what Claude Code, Pi and Codex hooks actually do, with the version
checked. If you find a fact that has changed in a newer release, update it with the version you
tested, the command you ran, and the observed output.

## Conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
