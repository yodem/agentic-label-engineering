# Labeling

ALE labels tasks with a fixed roster. The typed fields are `role`, `model_tier`, `risk`, and `effort`. `lane` is not voted on by rules, judges, or evaluation.

## Cascade

The planner writes a draft label first. Rules can add votes for matching fields. The command judge can add one vote per field when the roster enables that field.

Vote precedence is handled by the merge step. Planner, rule, and judge votes are combined according to the roster mode and threshold. Judge failures never block labeling. A failed judge call becomes an abstain with an error reason.

Judge modes are shadow by default. A shadow judge records evidence and disagreements, but the planner label remains authoritative unless the roster explicitly changes the mode.

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
