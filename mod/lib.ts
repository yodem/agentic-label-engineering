// ale-board: the pure helpers behind the board. No JSX and no `claude-code`
// import, so `bun test` runs them without the early-access types.
//
// All parsing, grouping, truncation and row budgeting live here as pure
// functions with no `$` access, so they are unit-testable in isolation.

export type RawTask = {
  state?: unknown
  claimable?: unknown
  attempt?: unknown
  owner?: unknown
  started_ts?: unknown
  last_heartbeat_ts?: unknown
  submitted_ts?: unknown
  last_step?: unknown
  step_changed_ts?: unknown
  tokens?: unknown
  cost_usd?: unknown
  rejections?: unknown
  waiting_on?: unknown
  breaches_seen?: unknown
  [key: string]: unknown
}

export type RawRun = {
  tokens?: unknown
  cost_usd?: unknown
  started_ts?: unknown
  finished?: unknown
  breaches_seen?: unknown
}

export type RawStatus = {
  run: RawRun
  tasks: Record<string, RawTask>
}

export type LabelInfo = {
  title?: string
  role?: string
  tier?: string
}

export type ParsedStatus = { ok: true; status: RawStatus } | { ok: false; error: string }

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

export function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export function asBool(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

export function asBreachCount(value: unknown): number {
  return Array.isArray(value) ? value.length : 0
}

/**
 * `python3 -m ale status --json`'s stdout to a `RawStatus`, or an error a
 * board row can show without throwing. Never throws.
 */
export function parseStatusOutput(exitCode: number, stdout: string, stderr: string): ParsedStatus {
  if (exitCode !== 0) {
    const line = stderr.trim().split('\n').pop() ?? ''
    return { ok: false, error: line ? `ale status failed (exit ${exitCode}): ${line}` : `ale status failed (exit ${exitCode})` }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(stdout)
  } catch (err) {
    return { ok: false, error: `ale status: malformed JSON (${err instanceof Error ? err.message : String(err)})` }
  }
  if (!isRecord(parsed)) return { ok: false, error: 'ale status: expected a JSON object' }
  const tasksRaw = parsed.tasks
  if (!isRecord(tasksRaw)) return { ok: false, error: 'ale status: missing "tasks"' }
  const runRaw = isRecord(parsed.run) ? parsed.run : {}
  const tasks: Record<string, RawTask> = {}
  for (const [id, value] of Object.entries(tasksRaw)) {
    if (isRecord(value)) tasks[id] = value
  }
  return { ok: true, status: { run: runRaw, tasks } }
}

/** The board's section order, and which task states fall in each. */
export const GROUP_ORDER: readonly string[] = [
  'input-required',
  'working',
  'submitted',
  'ready',
  'attention',
  'planned',
  'accepted',
  'failed',
  'canceled',
]

const GROUP_STATES: Record<string, readonly string[]> = {
  'input-required': ['input-required'],
  working: ['working', 'claimed'],
  submitted: ['submitted'],
  ready: ['ready'],
  attention: ['rejected', 'stale', 'released'],
  planned: ['planned'],
  accepted: ['accepted'],
  failed: ['failed'],
  canceled: ['canceled'],
}

const STATE_TO_GROUP: Record<string, string> = (() => {
  const out: Record<string, string> = {}
  for (const group of GROUP_ORDER) {
    for (const state of GROUP_STATES[group] ?? []) out[state] = group
  }
  return out
})()

export function groupNameFor(state: string): string | undefined {
  return STATE_TO_GROUP[state]
}

/**
 * Task ids bucketed into board sections, in `GROUP_ORDER`, each bucket's ids
 * sorted for determinism. A task whose state is not in the vocabulary is
 * dropped from every bucket (it cannot be drawn safely).
 */
export function groupTaskIds(tasks: Record<string, RawTask>): { name: string; ids: string[] }[] {
  const buckets: Record<string, string[]> = {}
  for (const name of GROUP_ORDER) buckets[name] = []
  for (const [id, task] of Object.entries(tasks)) {
    const state = asString(task.state)
    const group = STATE_TO_GROUP[state]
    if (group === undefined) continue
    buckets[group]?.push(id)
  }
  for (const name of GROUP_ORDER) buckets[name]?.sort()
  return GROUP_ORDER.map(name => ({ name, ids: buckets[name] ?? [] }))
}

/** Truncates to `width` columns, with a trailing ellipsis when it must cut. */
export function truncateTo(text: string, width: number): string {
  if (width <= 0) return ''
  if (text.length <= width) return text
  if (width === 1) return text.slice(0, 1)
  return `${text.slice(0, width - 1)}…`
}

const GLYPH_OF: Record<string, string> = {
  'input-required': '?',
  working: '●',
  claimed: '●',
  submitted: '»',
  ready: '○',
  planned: '·',
  rejected: '✕',
  stale: '~',
  released: '↺',
  accepted: '✓',
  failed: '✗',
  canceled: '⦸',
}

/** One glyph per state (rule 27: glyphs, not only colour — survives `tmux capture-pane -p`). */
export function glyphFor(state: string): string {
  return GLYPH_OF[state] ?? '·'
}

/** A short human age, from a `ts` (epoch seconds) to `nowS` (epoch seconds). */
export function ageOf(ts: unknown, nowS: number): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return '—'
  const deltaS = Math.max(0, Math.round(nowS - ts))
  if (deltaS < 60) return `${deltaS}s`
  const minutes = Math.floor(deltaS / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const restMinutes = minutes % 60
  if (hours < 24) return `${hours}h${restMinutes}m`
  const days = Math.floor(hours / 24)
  const restHours = hours % 24
  return `${days}d${restHours}h`
}

export type TaskRow = {
  id: string
  state: string
  text: string
  breached: boolean
}

/**
 * One board row, already truncated to `columns`. Format:
 * `<glyph> <id> <title> <role>/<tier> <owner> #<attempt> <tokens>tok <age> <lastStep>`
 */
export function formatTaskRow(id: string, task: RawTask, label: LabelInfo | undefined, nowS: number, columns: number): TaskRow {
  const state = asString(task.state)
  const glyph = glyphFor(state)
  const title = truncateTo(asString(label?.title, ''), 28)
  const role = asString(label?.role, '')
  const tier = asString(label?.tier, '')
  const roleTier = role || tier ? `${role}${role && tier ? '/' : ''}${tier}` : ''
  const owner = asString(task.owner, '') || '—'
  const attempt = asNumber(task.attempt, 1)
  const tokens = asNumber(task.tokens, 0)
  const heartbeatTs = task.last_heartbeat_ts ?? task.started_ts
  const age = ageOf(heartbeatTs, nowS)
  const lastStep = asString(task.last_step, '')
  const breached = asBreachCount(task.breaches_seen) > 0

  const parts = [
    `${glyph}${breached ? '!' : ' '}`,
    id,
    title,
    roleTier,
    owner,
    `#${attempt}`,
    `${tokens}tok`,
    age,
    lastStep,
  ].filter(part => part !== '')

  return { id, state, text: truncateTo(parts.join(' '), columns), breached }
}

export type Section = { name: string; rows: string[] }

/**
 * Budgets sections against `maxRows` (each section costs one header row plus
 * one row per task). When the total would scroll, collapse `accepted`
 * first, then `planned`, to a single count line — the priority order the
 * brief specifies. Sections with no rows are left as-is (they cost only
 * their header line, which the caller may choose to skip when empty).
 */
export function budgetRows(sections: readonly Section[], maxRows: number): Section[] {
  const totalRows = (secs: readonly Section[]): number => secs.reduce((n, s) => n + 1 + s.rows.length, 0)

  const collapse = (name: string, secs: readonly Section[]): Section[] =>
    secs.map(s => (s.name === name && s.rows.length > 1 ? { name: s.name, rows: [`${s.rows.length} more`] } : { ...s }))

  let out: Section[] = sections.map(s => ({ ...s }))
  if (totalRows(out) > maxRows) out = collapse('accepted', out)
  if (totalRows(out) > maxRows) out = collapse('planned', out)
  return out
}

/** The message a board with no discovered run shows. */
export function noRunMessage(): string {
  return 'no ALE run found · `ale init-run` to create one · `ale status --json` once a run exists'
}

/**
 * The one-line AbovePrompt band: `ale <run> · N working · N input-required ·
 * N accepted · N breaches`.
 */
export function bandLine(runId: string, tasks: Record<string, RawTask>, run: RawRun): string {
  let working = 0
  let inputRequired = 0
  let accepted = 0
  let breaches = asBreachCount(run.breaches_seen)
  for (const task of Object.values(tasks)) {
    const state = asString(task.state)
    if (state === 'working' || state === 'claimed') working += 1
    else if (state === 'input-required') inputRequired += 1
    else if (state === 'accepted') accepted += 1
    breaches += asBreachCount(task.breaches_seen)
  }
  return `ale ${runId} · ${working} working · ${inputRequired} input-required · ${accepted} accepted · ${breaches} breaches`
}

/** Picks the most recently modified directory entry, or undefined for none. */
export function pickLatestRunDir(entries: readonly { name: string; mtimeMs: number }[]): string | undefined {
  if (entries.length === 0) return undefined
  let best = entries[0]
  for (const entry of entries) {
    if (best === undefined || entry.mtimeMs > best.mtimeMs) best = entry
  }
  return best?.name
}
