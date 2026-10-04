# The eval loop: `ale analyze` and `ale eval cases`

ALE scores its own runs. `ale analyze` grades every indexed run against bars that were
committed before the first measurement (the online half). `ale eval cases` replays a
regression suite grown from real run failures (the offline half). Both append rows to one
eval ledger. `ale analyze` exits 1 when a check is breached or an active case regressed, and
`ale eval cases --ci` exits 1 when a case fails or regressed (without `--ci` it only reports),
so either one can gate CI. No model is
called anywhere in scoring.

```text
 real runs ──► ale analyze ───┐                          ┌──► reports/<date>.md, <date>.json
                              ├──► eval-ledger.jsonl ────┤
 cases.jsonl ─► ale eval cases┘     (append-only)        └──► findings.json ──► a fix ──► fixes.jsonl
      ▲                                                                            │
      └─────────────────── the failing input becomes a case ───────────────────────┘
```

Every file below lives under `$ALE_HOME/.ale/` (default `~/.ale/`). The test suite points
`ALE_HOME` at a temp directory, so no test writes your real index or ledger.

## What is indexed

`ale init-run` appends one line to `$ALE_HOME/.ale/index/runs.jsonl`:
`{ts, run_id, run_dir, repo_root, ale_version, roster_hash}`. `run_dir` and `repo_root` are
stored resolved (absolute, symlinks followed), so a run is never indexed twice under two
spellings; when a directory appears twice, the later row wins. An index that cannot be written
prints a warning and never fails `init-run`.

`ale analyze --backfill DIR` (repeatable) indexes runs that predate the index: every
`.ale/runs/<dir>/` under `DIR` holding both `labels/` and `events.jsonl`, where the `.ale/runs`
directory is at most 7 levels below `DIR`. It never descends into `.git`, `node_modules`, `__pycache__`,
`.venv`, `venv`, `.tox`, `.mypy_cache` or `.pytest_cache`, nor below a `.ale/runs/` entry (so a
run's own `wt/` worktrees are never walked). Backfilled entries take `run_id` and
`roster_hash` from the event log and leave `ale_version` null. Backfill is idempotent.

A case id names the run as `<repo>:<run dir name>` (for `<repo>/.ale/runs/<dir>`), not by its
run id: every run compiled from a `plan.md` gets run id `plan`, and run dir names repeat across
repositories. A task case is `<repo>:<run dir>/<task>`.

Runs whose run id or dir name matches `exclude_run_ids` in the thresholds file (test runs such
as `roundtrip` and `jev-bakeoff`) are skipped. A missing or unreadable run is skipped with a
warning on stderr; unparseable lines of `events.jsonl` or label files are skipped and counted.

## `ale analyze`

```sh
ale analyze [--since 7d|30d|<N>d|all] [--index P] [--backfill DIR]... [--json]
            [--if-due] [--no-write] [--thresholds P]
```

| Flag | Effect |
| --- | --- |
| `--since` | Window: runs whose last event falls in the last `<N>` days; `all` scores every run. Default: `window_days` (7). The previous window of the same length is scored too, for the trend. |
| `--index` | Run index path (default `$ALE_HOME/.ale/index/runs.jsonl`). |
| `--backfill DIR` | Index existing run dirs under `DIR` first. |
| `--json` | Print the report as JSON instead of markdown. |
| `--if-due` | Exit 0 with `ale analyze: not due (last report <date>)` unless the newest dated report is at least `due_after_days` (7) old, or there is none. |
| `--no-write` | Print the report; write no report, findings or ledger rows. `--backfill` still updates the index. |
| `--thresholds` | A thresholds file other than `ale/schema/analyze_thresholds.json`. |

`ALE_NOW` (epoch seconds) overrides the clock, for reproducible reports.

A run is `done` when no task is open (accepted, failed, canceled, removed, or a superseded fix
task: a rejected fix whose parent was accepted), `active` when it has an event in the last
`stale_after_hours` (72), and `stale` otherwise.

### Checks

Each check returns a score of 0 or 1 per case, or no result when its premise does not hold:
an n/a check writes no row and is never counted as a pass. A check is **breached** when its
pass rate over the window is below its bar with at least `min_n` (3) results. The bars live in
`ale/schema/analyze_thresholds.json`; changing one is a reviewed commit with a reason.

| Check | Premise (else n/a) | Passes when | Bar |
| --- | --- | --- | --- |
| `ale.task.first_pass` | the lead verified the task | its first lead verdict was an accept | 0.70 |
| `ale.task.path_scope_checked` | accepted, `worktree.mode` not `none`, the accept recorded a file list | that file list is not empty | 0.90 |
| `ale.task.write_has_worktree` | the task changed files (a verified file list, an integrate, or a `register-worktree` spawn) | its `worktree.mode` is not `none` | 0.90 |
| `ale.task.dispatch_worktree` | the task was spawned or claimed | its worktree came from a dispatch `spawned`, not `register-worktree` or no spawn | 0.75 |
| `ale.task.resolved` | the run is stale or done | the task is accepted, removed or superseded | 0.90 |
| `ale.task.input_required_bounded` | always | at most 3 `input_required` events in any one attempt | 0.95 |
| `ale.task.usage_recorded` | the first spawn was headless and the lead did not claim the task | it has a `usage` event | 0.50 |
| `ale.task.acceptance_held` | the run has `run_started` | acceptance was not relabeled after `init-run` | 0.85 |
| `ale.run.absolute_paths` | the run has spawned worktrees | every `spawned.worktree` is absolute | 1.00 |
| `ale.run.no_stale_open` | always | the run is not stale with open tasks | 0.90 |

Task cases carry `case_category` `<role>/<effort>`; run cases carry `run`. `config_hash` on an
online row is the run's own `roster_hash` (from its `labeled` events, else the index entry),
recorded when the run started, not when it is scored.

### Report sections

The markdown report has, in order: the window and run counts (active, stale, done, skipped),
the checks table (n, passed, rate, bar, status, up to five failing case ids), **What
regressed** (online checks that met their bar last window and are below it now, plus
regressions of active offline cases from the ledger), **Weakest category** and **Weakest evaluator** (lowest mean
score, top five), **Trend** (per check versus the previous window: improving, degrading or
stable at ±0.01; and mean score per `config_hash`), **Saturated checks** (at 1.00 in this and
the previous `saturation_windows - 1` dated reports: tighten or retire), **Fix status**,
**Metrics** (per role, tier, harness and model: acceptance rate, first-pass rate, attempts per
accepted task, claim-to-accept p50 and p90, tokens per accepted task, unknown-usage share),
**Calibration** (effort buckets with n >= 5 must be monotonic in duration and files changed;
role/effort/risk groups with n >= 3 whose reject rate or median duration is at least 2x the
median group), and **Promotions** (judge promotion progress per field from the roster;
proposals only, nothing is applied).

### Files written

Unless `--no-write`:

| File | Contents |
| --- | --- |
| `reports/<date>.md` | The markdown report. |
| `reports/<date>.json` | The full report, including every case result. |
| `reports/findings.json` | Open findings keyed by check id: `value`, `bar`, `n`, `first_seen`, `last_seen`, `examples` (case ids), and the latest `fix` record for that check. A finding keeps its `first_seen` across reports. |
| `reports/promotions.json` | The promotion proposals. |
| `eval-ledger.jsonl` | One row per case result: `tool: "ale"`, `case_kind: "online"`, `run_id` `analyze-<generated>`, `metadata.run_dir`, `metadata.scored_run_id`. |

A report, index or ledger file that cannot be written (say `$ALE_HOME/.ale` is not a
directory) prints a warning on stderr; the report is still printed and the exit code still
follows the checks.

### Exit codes

0 when nothing is wrong, 1 when any check is breached or an active offline case regressed, 2
for a usage error (a bad `--since`, `ALE_NOW` or thresholds file). Offline regressions count
only the active cases of the default cases file (the bundled `evalcases/cases.jsonl`), in the
exit code and in **What regressed** alike, so a retired case never gates the report or lingers
in it. When the default cases file is missing or unreadable, `ale analyze` warns on stderr
(`offline cases not read: …`), ignores offline regressions and exits by the checks alone.

## The eval ledger

`$ALE_HOME/.ale/eval-ledger.jsonl` is append-only and never rewritten. A row is
`{run_id, timestamp, tool, tool_version, config_hash, case_id, case_kind, case_category,
evaluator, score, passed, reason, metadata}`. Readers keep the latest row per
`(case_id, evaluator)`, so re-running a report is safe. A case **regressed** when its latest
score is below the best score recorded for it before.

## Fix records

`$ALE_HOME/.ale/fixes.jsonl` holds one record per fix:
`{evaluator, commit, ts or date, note, cases_added}`. `ts` is ISO 8601 (`Z` or a `+HH:MM`
offset, millisecond or microsecond fractions); `date` (`YYYY-MM-DD`) is the fallback, used when
`ts` is absent or does not parse. The report measures the check's pass rate over the window's
cases from runs that started after the fix: `holding` (n >= `min_n` and rate >= bar),
`regressed` (n >= `min_n` and rate < bar), `pending` (fewer results), or `invalid` when neither
`ts` nor `date` parses.

## `ale eval cases`

```sh
ale eval cases [--cases P] [--ci] [--no-record]
```

Runs every active case in `evalcases/cases.jsonl` (default: the copy in the plugin root, which
the wheel bundles), prints `PASS|FAIL <id>: <reason>` per case, a `REGRESSED <id>` line per
regression, and a summary. Unless `--no-record`, it appends one row per case to the eval
ledger: `case_kind: "offline"`, `evaluator` `ale.case.<kind>`, `run_id` `cases-<ISO ts>`,
`tool_version` the ALE version, and `config_hash` the first 12 hex digits of the sha256 of the
ALE version plus the bytes of `ale/schema/analyze_thresholds.json`. With `--no-record`, or when the
ledger cannot be written (a warning on stderr), the current results still count as the latest
when looking for regressions.

Exit codes: with `--ci`, 1 when any active case fails or an active case regressed (an offline
row whose latest score is below its best before; online rows and retired cases never count);
otherwise 0. A file with no active case (empty, or every case retired) checks nothing: it
prints `no active cases` on stderr and exits 1 with `--ci`, 0 without. A cases file that
cannot be read, or holds an invalid line, an unknown kind, a non-boolean `active` or a
duplicate id, exits 2.

### Case format

One JSON object per line:

```json
{"id": "parse-letter-ids", "kind": "parse", "input": "### Task T1: First\n...",
 "expected": {"tasks": [{"id": "T1", "files": ["a/x.py"]}]}, "category": "label",
 "difficulty": "easy", "created_at": "2026-10-04", "active": true,
 "source_run": "claude-obsidian:2026-10-02-trove-phase4", "note": "why this case exists"}
```

`input` is inline text or JSON, or `{"dir": "cases/<id>"}` relative to the cases file. Never
delete a case; retire it with `"active": false`. `active` defaults to true and must be a JSON
boolean: a string such as `"false"` or a number is a load error (exit 2), never silently active. Each evaluator is an exact match on the
fields `expected` names; `score` is the share that match and a case passes at 1.0. An
evaluator that raises scores 0 with the error as its reason.

| Kind | Runs | `expected` |
| --- | --- | --- |
| `parse` | `planparse.parse_plan` on the text (or `<dir>/plan.md`) | `tasks`: `[{id, files}]`, plus the task count |
| `bake` | for a plan without `ale-label` blocks: parse, skeleton labels, `bake`; then `compile_plan(run_id="case")` | `labels`: `{task: {key: value}}`; a key is a dotted path into the label, else into `context`; `worktree` is the effective mode dispatch uses |
| `route` | `harness.route(label, roster, lane=lane)` on `{label, roster, lane}` | any of `harness`, `model`, `mode`, `host` |
| `refs` | `refs.allowed_command` on the command (shlex split) | `allowed` |
| `analyze` | `analyze.evaluate` on the run in `<dir>` (`labels/` + `events.jsonl`), now = last event + 60 s unless `input.now` | `checks`: `{check: true\|false\|null}`; a check passes when all its case results pass, null means n/a |

`analyze` cases only read their fixture; they write nothing under `ALE_HOME`.

### Seed cases

Each seed case comes from a real failure, named in its `source_run` and `note`: bulleted
`- Create:` file lines and `### Task T1:` ids that the parser missed (55 write tasks baked
`worktree.mode: none`), a write task that must bake a per-task worktree, `trove items get`
refs, relative `spawned.worktree` paths, the 458-event `input_required` flood, accepts
path-checked against an empty file list, a `register-worktree` instead of a dispatch, and a
Claude task on an inline lane that must run in-session. Guards (`parse-numeric-ids-unchanged`,
`refs-rm-refused`) pin the behaviour around a fix, and `analyze-clean-run` is the positive
control: every check passes.

## How a finding becomes a case

1. `ale analyze` breaches a check; `findings.json` lists it with example case ids, which name
   the run dirs and tasks to look at.
2. Diagnose from the artifacts (labels, `events.jsonl`, the worktree), fix the code, and add the
   failing input as a case with the corrected `expected`: copy the smallest fixture that
   reproduces it into `evalcases/cases/<id>/`, with home paths, emails and machine names
   scrubbed (a directory named `fixtures` is not bundled). Confirm the case fails without the
   fix and passes with it.
3. Append `{"evaluator": "<check id>", "commit": "<sha>", "ts": "<ISO time>", "note": "...",
   "cases_added": ["<id>"]}` to `$ALE_HOME/.ale/fixes.jsonl`.
4. The next `ale analyze` reports the fix as `pending`, then `holding` or `regressed`, and
   `ale eval cases --ci` keeps the case from coming back.
