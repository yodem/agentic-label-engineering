# ALE agents

Each task label resolves to one agent definition. The definition supplies a role,
specialty, lifecycle phase, reading context, checklist, and deterministic rules.
It does not select a model; the roster continues to route model tiers to
executors and models.

## Taxonomy

`sub` and `phase` are closed vocabularies in the roster. Cross-cutting subs may
be used with any role. `infra` remains valid for compatibility and resolves as
`devops/infra`.

| Role | Subs |
| --- | --- |
| `frontend` | `css`, `ux`, `design`, `components`, `performance`, `accessibility` |
| `backend` | `architecture`, `api`, `data`, `integration`, `performance` |
| `devops` | `ci`, `deploy`, `infra`, `monitor`, `release` |
| `test` | `unit`, `integration`, `e2e`, `coverage` |
| `docs` | `reference`, `guide`, `changelog` |
| `infra` | Kept for compatibility; new work uses `devops` |
| `fixer`, `monitor`, `general` | No role-specific subs |
| Any role | Cross-cutting: `debugging`, `review`, `security` |

Phases are `plan`, `design`, `implement`, `test`, `review`, `deploy`,
`operate`, and `maintain`. `sub` is optional in the schema, but `bake` reports
it as a gap for `frontend`, `backend`, and `devops`. `phase` defaults to
`implement` only when `allowed_paths` is non-empty; otherwise it is a gap.

## Agent file format

Bundled agents live at `agents/<role>/<sub>.md`, with per-role
`agents/<role>/_default.md`, cross-cutting definitions in `agents/_cross/`,
and `agents/general.md` as the final fallback. A project can add or override
these files under `.ale/agents/`.

Files use YAML frontmatter followed by a structured body. The following is the
frontmatter and first heading of the bundled `agents/frontend/css.md` example:

```yaml
---
name: frontend-css
role: frontend
sub: css
phases: [implement, review, maintain]
model_tier_min: cheap
reads:
  - "agents/_refs/architecture-patterns/SKILL.md"
---
```

```markdown
# Directive
```

Required frontmatter includes `name`, `role`, `sub`, `phases`,
`model_tier_min`, `reads`, `rules`, `checklist`, and `version`. `origin` is
optional provenance. The body follows this order: Directive, Grounding
Protocol, Concrete Objectives, Methodology, Output Format, Task Boundaries,
Example, and Status Protocol. Portable content comes first; optional
Claude-only instructions follow `<!-- harness: claude-code -->`.

## Resolution

The effective catalog searches project `.ale/agents/` before the bundled
`agents/` catalog. A project file with the same relative path shadows the
bundled file. For a task's role, sub, and phase, ALE resolves in this order:

1. `agents/<role>/<sub>.md` when its `phases` includes the task phase.
2. The same sub file if present, with a phase-mismatch warning recorded.
3. `agents/_cross/<sub>.md` for a cross-cutting sub.
4. `agents/<role>/_default.md`, then `agents/general.md`.

The resolved reference records the path, name, SHA-256, version, and match
type in `routing.agent` at `init-run`, and is frozen for that run. Relabeling
`sub` resolves again through a `label_changed` event. `model_tier_min` raises
the label tier when needed and records `provenance.model_tier.by` as
`agent-floor`. If a catalog file changes during a run, the original SHA stays
in effect and `ale status` reports `agent: stale`.

Use `ale agents list` to inspect the effective catalog and its source. Use
`ale agents show <role>/<sub>` to inspect the resolved definition and SHA.
Project-specific additions belong in `.ale/agents/`; add a roster vocabulary
entry under `vocab.sub.<role>` as well so the new sub is recognized.

## Rule enforcement

Agent rules add to the label's restrictions. `deny_paths` and `deny_tools`
are unioned with existing guards. `require_before_submit` commands run before
submission and again during verification. Shipped harness behavior is shown
below.

| Rule | Claude Code hooks | Codex hooks | Pi extension | ale-exec wrapper |
| --- | --- | --- | --- | --- |
| `deny_paths` | Enforced: edit denied by PreToolUse | Enforced: edit denied by PreToolUse | Enforced: edit and write events | Verify only; no edit interception |
| `deny_tools` | Enforced: pre-tool deny list and Claude spawn disallowed tools | Enforced: pre-tool deny list | Enforced: pre-tool deny list | Not available as a wrapper tool guard |
| `require_before_submit` | Enforced: Stop gate; failure requires input | Not available in current adapter | Enforced: settlement gate; print mode is advisory | Enforced: exit-time check before submit |
| Verify backstop | Enforced: `ale verify` reruns required commands | Enforced: `ale verify` reruns required commands | Enforced: `ale verify` reruns required commands | Enforced: `ale verify` reruns required commands |

## Analytics and variants

`ale meta` includes an `agents` table keyed by `name@version`, with tasks run,
accepted on first verify (count and rate), fix tasks needed, breaches, billable
tokens, and median wall time. `ale meta --csv` includes the same agent
analytics. `rewrite_candidate` is true when the first-verify rate is under 0.7
across at least five tasks, which is the weekly review's rewrite signal.

The first-verify rate counts, in the numerator, tasks accepted on attempt 1 that
neither are a fix task nor spawned one. The denominator is every task routed to
that agent identity, which is the `tasks` column. A task that needed a fix
therefore scores 0 out of 2 rather than an undefined 0 out of 0, and the table
stays readable as a single ratio over its own task count.

The per-executor-instance rows that `ale meta` used to publish under `agents`
now appear under `agent_instances`. The `agents` key belongs to the frozen agent
identities above.

The A/B option is `ale run --agent-variant <role>/<sub>=<path>`, repeatable.
It overlays one catalog key with the given file for that run. `routing.agent`
then records `variant: true` alongside the file's resolved path and SHA-256, and
the run id gains a `-v<sha8>` suffix unless `--run-id` is given, so two runs of
the same plan land in two run directories and produce two comparable rows.

## Catalog roots and symlinks

`.ale/agents/` is followed even when it is a symlink pointing outside the
project. That is deliberate: a team that keeps its agent definitions in a shared
checkout should be able to link them in. It is safe because following the link
buys an attacker nothing on its own. Every file the catalog loads must carry
valid frontmatter and validate against the agent schema, so an arbitrary file
reached through a hostile link is refused rather than executed, and the resolved
path and SHA-256 of whatever was loaded are recorded in `routing.agent` and
visible in `ale agents show`. Path restrictions are a separate matter: they are
enforced at the hook edge, which resolves a tool's target to its real path
before the `deny_paths` check, so a symlink inside an allowed directory cannot
be used to reach a denied one.

## Attribution

Parts of the bundled agent catalog and references derive from OrchestKit 9.8.0
under the MIT License. See [NOTICE](../NOTICE) for attribution and license
text.
