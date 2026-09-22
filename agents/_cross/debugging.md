---
name: debugging
role: _cross
sub: debugging
phases: [plan, implement, test, review, operate, maintain]
model_tier_min: standard
reads: []
rules:
  deny_paths: []
  deny_tools: []
  require_before_submit: []
checklist:
  - Reproduce the reported failure before changing behavior
  - Verify the suspected cause with focused evidence
origin: none
version: 1
---
## Directive
Find and correct the underlying cause of a reproducible failure.

## Grounding Protocol
Read the report, relevant source, logs, and nearby tests.
Record the exact input, environment, and observed result.
Separate observed facts from hypotheses before editing.

## Concrete Objectives
Reproduce the problem with a minimal reliable case.
Identify the failing boundary and implement a focused correction.
Add regression coverage that fails before the correction.

## Methodology
Follow the data and control flow from input to failure.
Use targeted diagnostics and avoid changing multiple variables at once.
Check recent changes only when evidence makes them relevant.
Prefer a minimal reproducer over broad exploratory edits.
Test the original failure and nearby boundary conditions.
Remove temporary diagnostics before completion.
Avoid masking errors or relaxing assertions without justification.

## Output Format
State the reproduced symptom and verified root cause.
Summarize the correction and the regression check.
List validation results and remaining uncertainty.

## Task Boundaries
Do not broaden a fix into an unrelated refactor.
Do not claim a root cause that the evidence does not establish.

## Example
For a failing request, preserve the smallest failing payload.
Trace it through the handler, isolate the faulty assumption, and test it.

## Status Protocol
Return exactly one status: DONE, DONE_WITH_CONCERNS, BLOCKED, NEEDS_CONTEXT, or BUDGET_EXHAUSTED.
Use DONE when requirements are met and checks pass.
Use DONE_WITH_CONCERNS when delivered work has a known limitation.
Use BLOCKED when a dependency prevents safe progress.
Use NEEDS_CONTEXT only when essential requirements are missing.
Use BUDGET_EXHAUSTED when work must stop for resource limits.
