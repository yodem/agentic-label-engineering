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
ale eval corpus --ledger events.jsonl --plans "plans/*.md" --out .ale/eval/run1
ale eval gold --a a.jsonl --b b.jsonl --rulings rulings.jsonl --out .ale/eval/run1
ale eval judge --corpus .ale/eval/run1/corpus.jsonl --roster roster.json --out .ale/eval/run1
ale eval report --corpus .ale/eval/run1/corpus.jsonl --gold .ale/eval/run1/gold.json --judge .ale/eval/run1/judge.jsonl --a a.jsonl --roster roster.json --out .ale/eval/run1
```

`eval judge` appends to `judge.jsonl` as each call finishes. It can resume because existing `(id, field, perm)` triples are skipped. `--max-calls` stops after that many new calls and exits successfully.

## Privacy

Use synthetic invented text in fixtures, docs, and committed corpora. Real corpora, label files, rulings, judge rows, and reports should live under git ignored evaluation directories. Pass a deny regex when building a corpus if source data can contain private strings.

## Reading The Report

The report states the tolerance and promotion bar before results. Each field is split by `short` and `full` corpus rows. For each split it shows sample count, accuracy, coverage at confidence thresholds, kappa against labeler A, common confusion pairs, abstain and error reasons, latency, calls, and position sensitivity.

Comparison arms passed with `--arm NAME=PATH` are shown beside the judge with accuracy and kappa rows.

`MEETS BAR` means the split has enough cases and coverage for the roster promotion rule. `BELOW BAR` names the number that failed the bar.

## Known Limits

Roster rules are trusted author input. A catastrophically backtracking regex can hang `ale label`.

Agreement-based gold is biased toward the two labelers, so they are scored only on the human-adjudicated subset.

Option strings carry guideline text. Editing a guideline changes what the judge sees.

Spec text is read only from inside the project or run directory.
