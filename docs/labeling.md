# Labeling

ALE labels tasks with a fixed roster. The typed fields are `role`, `model_tier`, `risk`, and `effort`. `lane` is not voted on by rules, judges, or evaluation.

## Cascade

The planner writes a draft label first. Rules can add votes for matching fields. The command judge can add one vote per field when the roster enables that field.

Vote precedence is handled by the merge step. Planner, rule, and judge votes are combined according to the roster mode and threshold. Judge failures never block labeling. A failed judge call becomes an abstain with an error reason.

Judge modes are shadow by default. A shadow judge records evidence and disagreements, but the planner label remains authoritative unless the roster explicitly changes the mode.

## Jev shadow decisions

Jev records additive shadow votes on ALE decisions. A vote is never applied: labels, lane, `lane_reason`, routing, the monitor requirement, the rejection action and the monitor verdict stay exactly what the planner, dispatcher, run loop or monitor decided. Collection depends on `judge.default` in the roster:

| `judge.default` | Behavior |
| --- | --- |
| `shadow` (and `judge.bar` present) | Every judged decision below collects votes. `--no-judge` disables collection for one command. |
| `off` | No judge call at all, whatever the command-line flags say. The shipped roster uses `off`. |
| absent | A roster written before these decisions existed. The old label-cascade judge keeps its old opt-in (`ale label` without `--no-judge`, `ale plan bake --judge`); no decision votes are collected. |

The absent case deliberately differs from `off`: it keeps older rosters and scripts behaving as they did. Use `ale setup --judge shadow` or `ale setup --judge off` to write the key.

### Registry

Twelve decision IDs are registered in `ale/decisions.py`. `executor` is deterministic (routing from role and tier) and is never judged, so eleven decisions are judged. Classification decisions are one Choice with an escape option. Verdict-like decisions are never asked as one question: Jev answers two to four narrow yes/no evidence questions (Noul) and code computes the decision with a fixed rule table (Jev book Rule 9.9). Facts that events or labels already hold, such as fix counts, breach kinds and plan dependencies, are computed in code and never asked. Every question text comes from `judge.questions` in the roster; a missing question makes the decision abstain, never a generic question.

| Decision | Asked as | Rule computed in code | Options |
| --- | --- | --- | --- |
| `role`, `model_tier`, `risk`, `effort` | Choice (`judge.questions.<field>`) | none | roster vocab |
| `sub` | Choice (`judge.questions.sub`) | none | the role's subs plus cross subs |
| `phase` | Choice (`judge.questions.phase`) | none | `vocab.phase` |
| `locality` | Noul `locality` | yes -> `local`, else `any` | `any`, `local` |
| `lane` | Nouls `over_an_hour`, `unattended`; facts `role`, `independent_tasks` | over an hour -> `pane`; unattended -> `pane`; role test or review -> `workflow`; two or more independent tasks -> `workflow`; else `inline` (the `flow lane` table) | `inline`, `workflow`, `pane` |
| `needs_monitor` | Nouls `over_an_hour`, `unattended`, `external_side_effects`; fact `risk` | risk high -> `yes`; over an hour and unattended -> `yes`; external side effects -> `yes`; else `no` | `yes`, `no` |
| `rejection_action` | Nouls `rejection_environment`, `rejection_spec_conflict`, `rejection_needs_human`; facts `fix_count`, `is_fix_task` | fix of a fix or two fixes -> `escalate`; needs a human -> `escalate`; environment problem -> `reopen`; check contradicts the task -> `reopen`; else `fix` | `fix`, `reopen`, `escalate` |
| `monitor_verdict` | Nouls `monitor_reports_defect`, `monitor_agent_blocked`, `monitor_unsafe`; facts `monitor_wrote_files`, `attempts_exhausted`, `breach` | monitor wrote files -> `escalate`; attempts exhausted -> `escalate`; unsafe -> `escalate`; defect -> `fix`; agent blocked -> `nudge`; liveness breach -> `nudge`; else `continue` | `continue`, `nudge`, `fix`, `escalate` |
| `executor` | not judged | deterministic routing | roster routing |

A Noul is yes at 0.5 or above. A consulted Noul in 0.35 to 0.65, or a Choice confidence below 0.5, is inside the uncertain band. A missing or invalid answer that the rule table needs makes the vote abstain (`rule: missing_evidence`); the table never fails open. `lane` stays planner-decided with a written `lane_reason` (WRK-46); Jev's lane vote is observation only.

### Firing sites and outcomes

| Site | Votes | Outcome recorded |
| --- | --- | --- |
| `ale plan bake` | role, model_tier, risk, effort, locality, sub, phase, lane, needs_monitor, written to the git-ignored `.ale/shadow/<plan>-<hash>.jsonl` with a `bake_id` | at `init-run` |
| `ale init-run --plan` | imports the latest bake's votes into the run's `events.jsonl` | each bake decision's value from the compiled label |
| `ale label` | the same bake decisions, straight into the run | from the written label |
| `ale verify` (rejection) | rejection_action | `ale fix` -> `fix`; fix of a fix or two fixes -> `escalate`; `ale reopen` -> `reopen`. The `ale run` exhausted branch also records `escalate` (wired, no test yet). |
| `ale dispatch` (monitor result) | monitor_verdict | the monitor's verdict |

An outcome is written only when it is known and only after a vote for the same task and decision. Each vote records `decision`, `options`, `choice`, `confidence`, `model`, `latency_ms`, `uncertain`, and for evidence decisions `answers` (each Noul probability), `rule` (the table row that fired) and `calls_latency_ms` (one entry per Jev call the vote made; an answer shared with an earlier decision on the same state is not counted again). The monitor's liveness facts come from events: the latest watchdog breach kind (itself computed from heartbeat age) and whether attempts are exhausted. The model comes from the judge answer; set the optional `judge.model` to pass `JEV_MODEL` to `jev-ask`.

### Statistics

`ale judge-stats --run-dir RUN --roster ROSTER` reads only the run's `events.jsonl`; bake votes are there because `init-run` imported them. It returns `{"decisions": {DECISION: {"vote_count", "adjudicated_count", "uncertain_band": {"inside", "outside", "missing"}, "agreement", "agreement_inside_band", "agreement_outside_band", "latency_ms_median", "grey_zone", "progress", "instability", "bar_met"}}, "bar": {"min_cases", "min_agreement", "max_instability"}, "band": {"noul_uncertain", "choice_uncertain_below"}, "cases": N}`. Agreement compares each vote with the recorded outcome. `latency_ms_median` is the median over individual Jev calls. `grey_zone` is true for `effort`, whose honest answer is often mid-confidence (Jev book chapter 8). Decisions without votes are omitted, not padded. The preregistered bar is 100 adjudicated cases, a lead-selected minimum agreement of 0.8, and maximum instability of 0.10. Meeting it is evidence only: statistics never promote a judge or change an authoritative decision. `ale adjudicate --decision D --task T --value V` records one lead adjudication per task and decision; a second one is refused, and `executor` cannot be adjudicated.

## LLM Labelers

An external labeler file is JSONL. Each line is one synthetic task label:

```json
{"id":"C1","role":"backend","model_tier":"standard","risk":"low","effort":"M"}
```

Use the same ids as the corpus. Missing ids are skipped when the gold set is built.

## Evaluation

Build a corpus first, then collect two labeler files, adjudicate disagreements, run the judge, and render the report.

```sh
ale eval corpus --ledger events.jsonl --plans "plans/*.md" --exclude-task-id-regex "scratch-" --redact --english-only --out .ale/eval/run1
ale eval gold --a a.jsonl --b b.jsonl --rulings rulings.jsonl --out .ale/eval/run1
ale eval judge --corpus .ale/eval/run1/corpus.jsonl --roster roster.json --out .ale/eval/run1
ale eval report --corpus .ale/eval/run1/corpus.jsonl --gold .ale/eval/run1/gold.json --judge .ale/eval/run1/judge.jsonl --a a.jsonl --b b.jsonl --arm incumbent=incumbent.jsonl --incumbent-arm incumbent --roster roster.json --out .ale/eval/run1
```

`eval corpus` accepts repeatable `--plans` globs and de-duplicates matched files by real path. `--full-share FLOAT` controls how much of the sampled corpus is reserved for full plan rows, with the default preserving the historical one-third share. Pass `--full-share 1` to take full rows first up to `--n`, then fill with ledger rows.

Use `--exclude-task-id-regex RE` to drop ledger starts whose `task_id` or `task` matches a pattern. Use `--english-only` to drop rows whose text is not mostly English.

`eval judge` appends to `judge.jsonl` as each call finishes. It can resume because existing `(id, field, perm)` triples are skipped. `--max-calls` stops after that many new calls and exits successfully.

`eval gold` records a gold value for every field and id where labelers agree or where a human ruling exists. It also records the basis for each value: `agree` for labeler agreement and `human` for adjudicated disagreements.

## Privacy

Use synthetic invented text in fixtures, docs, and committed corpora. Real corpora, label files, rulings, judge rows, and reports should live under git ignored evaluation directories. Pass a deny regex when building a corpus if source data can contain private strings.

`ale eval corpus --redact` applies built-in redaction to emitted row `text` and `source`: home directories become `~`, email and ssh-style addresses become `<addr>`, IPv4 addresses become `<ip>`, and key-like values for `api_key`, `apikey`, `token`, `password`, and `secret` become `<redacted>`. Repeat `--redact-regex 'RE=>REPLACEMENT'` to add caller-provided redactions after the built-ins; this flag implies `--redact`. Replacement counts are written to `corpus-stats.json` under `redactions`.

## Reading The Report

The report states the tolerance and promotion bar before results. The promotion bar requires every field to pass all criteria: judge full-gold sample count is at least `promotion.min_cases`; an incumbent arm is selected and judge full-gold accuracy is at least incumbent full-gold accuracy minus `promotion.tolerance`; coverage at `roster["judge"]["threshold"]` is at least `promotion.min_coverage`; accuracy among covered items at that threshold is at least the same incumbent-minus-tolerance value; and option-order instability is at most `--max-instability`, which defaults to `0.10`. Pass the independent comparison arm with `--arm NAME=PATH` and select it with `--incumbent-arm NAME`. When no incumbent arm is selected, every field is below bar with the reason `no incumbent arm`.

The judge and every comparison arm are scored on the full gold set and on the human-basis subset. The human-basis subset is the adjudicated A/B disagreement set.

Labeler A and optional labeler B are scored only on the human-basis subset, and the report labels this as informational because that subset is where A and B originally disagreed. A and B cannot be used as the incumbent promotion arm for that bar because agreement-derived gold already contains their shared answers, and the human subset is selected from their disagreement set. Use an independent comparison arm for promotion.

Each field is also split by `short` and `full` corpus rows for diagnostics. For each split the report shows sample count, accuracy, coverage at confidence thresholds, kappa against labeler A, common confusion pairs, abstain and error reasons, latency, calls, and position sensitivity.

The inter-labeler table prints the gold set kappa and agreement for each field. Metrics that cannot be computed render as `n/a`.

Teams can optionally blind-audit a sample of agreement-based gold items. This checks whether easy agreement cases still match the roster guidance without mixing that audit into the promotion bar.

## Known Limits

Roster rules are trusted author input. A catastrophically backtracking regex can hang `ale label`.

Agreement-based gold is biased toward the two labelers, so they are scored only on the human-adjudicated subset.

Option strings carry guideline text. Editing a guideline changes what the judge sees.

Spec text is read only from inside the project or run directory.
