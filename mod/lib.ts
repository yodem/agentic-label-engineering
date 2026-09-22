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
  assignees?: unknown
  integrated?: unknown
  last_verdict?: unknown
  worktree?: unknown
  [key: string]: unknown
}

export type RawRun = {
  tokens?: unknown
  cost_usd?: unknown
  started_ts?: unknown
  finished?: unknown
  breaches_seen?: unknown
}

export type LabelData = Record<string, any>
export type AleEvent = Record<string, any> & { type: string; ts?: number; task_id?: string | null; agent_id?: string | null }

export type RunResolution = { runDir: string; source: 'env' | 'cwd' | 'launch' | 'arg' }

export function resolveRunDirectory(input: {
  envDir?: string
  cwdRunDir?: string
  launchRunDir?: string
  arg?: string
  cwdRunsDir?: string
  launchRunsDir?: string
}): RunResolution | undefined {
  const arg = input.arg?.trim()
  if (arg) {
    if (arg.startsWith('/')) return { runDir: arg, source: 'arg' }
    if (arg.includes('/')) return undefined
    const runsDir = input.cwdRunsDir ?? input.launchRunsDir
    if (runsDir) return { runDir: `${runsDir}/${arg}`, source: 'arg' }
    return undefined
  }
  if (input.envDir) return { runDir: input.envDir, source: 'env' }
  if (input.cwdRunDir) return { runDir: input.cwdRunDir, source: 'cwd' }
  if (input.launchRunDir) return { runDir: input.launchRunDir, source: 'launch' }
  return undefined
}

export function unknownRunArgumentMessage(arg: string): string {
  return `unknown argument "${arg}" · /ale-board [close | refresh | <run_id> | <absolute_run_dir>]`
}

const AUTHORITY_EVENTS = new Set(['verified', 'accepted', 'rejected', 'failed', 'canceled', 'lease_expired', 'released', 'input_answered', 'task_added', 'label_changed', 'label_removed', 'spawned', 'integrated', 'monitor_verdict'])
const OPEN_STATES = new Set(['planned', 'released', 'rejected'])
const LIVE_STATES = new Set(['claimed', 'working', 'input-required'])
const TERMINAL_STATES = new Set(['accepted', 'failed', 'canceled'])

function freshTask(): Record<string, any> {
  return { state: 'planned', claimable: false, attempt: 1, owner: null, started_ts: null, last_heartbeat_ts: null, submitted_ts: null, last_step: null, step_changed_ts: null, steps: [], files_modified: [], pending: [], next_steps: [], waiting_on: null, summary: null, notes: [], tokens: 0, cost_usd: 0, rejections: 0, last_reject_reason: null, evidence: null, breaches_seen: [], assignees: [], integrated: false, last_verdict: null, blocked_by: [], worktree: null }
}

export function reduceEvents(events: AleEvent[], initialLabels: Record<string, LabelData>): { run: RawRun; tasks: Record<string, RawTask>; labels: Record<string, LabelData> } {
  const labels = structuredClone(initialLabels)
  const tasks: Record<string, Record<string, any>> = Object.fromEntries(Object.keys(labels).filter(id => id !== '__roster__').map(id => [id, freshTask()]))
  const run: Record<string, any> = { tokens: 0, cost_usd: 0, started_ts: null, finished: false, breaches_seen: [] }
  const depsOk = (id: string) => (labels[id]?.context?.depends_on ?? []).every((dep: string) => tasks[dep]?.state === 'accepted')
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index]
    const kind = event.type
    const id = event.task_id
    if (kind === 'run_started') { run.started_ts = event.ts; continue }
    if (kind === 'run_finished') { run.finished = true; continue }
    if (kind === 'usage') {
      const tokens = Number(event['gen_ai.usage.input_tokens'] || 0) + Number(event['gen_ai.usage.output_tokens'] || 0)
      run.tokens += tokens
      run.cost_usd += Number(event.cost_usd || 0)
    }
    if (kind === 'breach' && id == null) { run.breaches_seen.push(event.breach); continue }
    if (AUTHORITY_EVENTS.has(kind) && event.agent_id != null) continue
    if (kind === 'task_added' && id && isRecord(event.label)) { labels[id] = event.label; tasks[id] = freshTask(); continue }
    if (kind === 'label_removed' && id && OPEN_STATES.has(tasks[id]?.state)) { delete labels[id]; delete tasks[id]; continue }
    if (kind === 'label_changed' && id && labels[id] && typeof event.field === 'string') {
      const field = event.field === 'lane' ? 'labels.lane' : event.field
      const allowed = ['labels.', 'assignments', 'watch.', 'context.allowed_paths', 'context.depends_on']
      if (!allowed.some(prefix => field === prefix || field.startsWith(prefix))) continue
      const parts = field.split('.')
      if (parts.length === 1) labels[id][field] = event.new
      else if (parts.length === 2) { labels[id][parts[0]] ??= {}; labels[id][parts[0]][parts[1]] = event.new }
    }
    const task = id ? tasks[id] : undefined
    if (!task) continue
    if (kind === 'accepted' && task.state === 'rejected') {
      const acceptedFix = Object.entries(labels).some(([fixId, label]) => label.fixes === id && events.slice(0, index).some(earlier => earlier.type === 'accepted' && earlier.task_id === fixId))
      if (acceptedFix) task.state = 'submitted'
    }
    const agent = event.agent_id
    const owner = task.owner != null && agent === task.owner
    const ts = event.ts ?? 0
    if (kind === 'claimed' && task.owner == null && OPEN_STATES.has(task.state) && event.attempt === task.attempt && depsOk(id!)) {
      Object.assign(task, { state: 'claimed', owner: agent, started_ts: ts, last_heartbeat_ts: ts, step_changed_ts: ts, submitted_ts: null })
      if (agent && !task.assignees.includes(agent)) task.assignees.push(agent)
    } else if (kind === 'heartbeat' && owner && LIVE_STATES.has(task.state)) {
      if (task.state !== 'input-required') task.state = 'working'
      task.last_heartbeat_ts = ts
      if (event.step !== task.last_step) { task.last_step = event.step; task.step_changed_ts = ts; task.steps.push(event.step) }
      for (const path of event.files_modified ?? []) if (!task.files_modified.includes(path)) task.files_modified.push(path)
      for (const key of ['pending', 'next_steps']) if (event[key] != null) task[key] = [...event[key]]
    } else if (kind === 'input_required' && owner && LIVE_STATES.has(task.state)) Object.assign(task, { state: 'input-required', waiting_on: event.question })
    else if (kind === 'input_answered' && task.state === 'input-required') Object.assign(task, { state: 'working', waiting_on: null, last_heartbeat_ts: ts, step_changed_ts: ts })
    else if (kind === 'submitted' && owner && LIVE_STATES.has(task.state)) Object.assign(task, { state: 'submitted', summary: event.summary, submitted_ts: ts })
    else if (kind === 'verified') task.evidence = event.evidence
    else if (kind === 'accepted' && task.state === 'submitted') Object.assign(task, { state: 'accepted', owner: null, evidence: event.evidence })
    else if (kind === 'rejected' && task.state === 'submitted') { Object.assign(task, { state: 'rejected', owner: null, evidence: event.evidence, last_reject_reason: event.reason }); task.rejections += 1; task.attempt += 1 }
    else if ((kind === 'failed' || kind === 'canceled') && !TERMINAL_STATES.has(task.state)) Object.assign(task, { state: kind, owner: null })
    else if (kind === 'lease_expired' && LIVE_STATES.has(task.state)) Object.assign(task, { state: 'stale', owner: null })
    else if (kind === 'released' && task.state === 'stale') task.state = 'released'
    else if (kind === 'usage') { task.tokens += Number(event['gen_ai.usage.input_tokens'] || 0) + Number(event['gen_ai.usage.output_tokens'] || 0); task.cost_usd += Number(event.cost_usd || 0) }
    else if (kind === 'breach') task.breaches_seen.push([event.breach, task.attempt])
    else if (kind === 'spawned') {
      if (event.agent_id_minted && !task.assignees.includes(event.agent_id_minted)) task.assignees.push(event.agent_id_minted)
      task.worktree = typeof event.worktree === 'string' ? event.worktree : null
    } else if (kind === 'integrated') task.integrated = true
    else if (kind === 'monitor_verdict') task.last_verdict = { agent_id_minted: event.agent_id_minted, verdict: event.verdict, text: event.text }
  }
  for (const [id, task] of Object.entries(tasks)) {
    const missing = (labels[id]?.context?.depends_on ?? []).filter((dep: string) => !(dep in tasks))
    task.blocked_by = missing
    task.claimable = !task.owner && OPEN_STATES.has(task.state) && depsOk(id) && missing.length === 0
    if (missing.length) task.breaches_seen.push(['orphaned_dependency', task.attempt])
    if (task.state === 'planned' && task.claimable) task.state = 'ready'
  }
  for (const [fixId, label] of Object.entries(labels)) {
    const parent = label.fixes
    if (!parent || !tasks[parent] || !tasks[fixId]) continue
    if (tasks[fixId].state !== 'accepted') tasks[parent].state = 'fixing'
    else if (tasks[parent].state !== 'accepted') {
      const rejects = events.map((e, i) => [e, i] as const).filter(([e]) => e.task_id === parent && e.type === 'rejected').map(([, i]) => i)
      const accepts = events.map((e, i) => [e, i] as const).filter(([e]) => e.task_id === fixId && e.type === 'accepted').map(([, i]) => i)
      if (accepts.length && (!rejects.length || Math.max(...accepts) > Math.max(...rejects))) tasks[parent].state = 'submitted'
    }
  }
  return { run, tasks, labels }
}

export type RawStatus = {
  run: RawRun
  tasks: Record<string, RawTask>
}

export type LabelInfo = {
  title?: string
  role?: string
  tier?: string
  fixes?: string
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
  attention: ['rejected', 'stale', 'released', 'fixing'],
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
  const assignees = Array.isArray(task.assignees) ? task.assignees.filter((x): x is string => typeof x === 'string').join(',') : ''
  const owner = asString(task.owner, '') || assignees || '—'
  const attempt = asNumber(task.attempt, 1)
  const tokens = asNumber(task.tokens, 0)
  const heartbeatTs = task.last_heartbeat_ts ?? task.started_ts
  const age = ageOf(heartbeatTs, nowS)
  const lastStep = asString(task.last_step, '')
  const breached = asBreachCount(task.breaches_seen) > 0
  const breachText = Array.isArray(task.breaches_seen) ? task.breaches_seen.map(value => Array.isArray(value) ? String(value[0]) : String(value)).join(',') : ''
  const worktree = asString(task.worktree, '')
  const worktreeLabel = worktree ? `wt/${worktree.split('/').filter(Boolean).pop()}` : ''
  const integrated = task.integrated === true ? 'integrated' : ''
  const verdict = isRecord(task.last_verdict) ? asString(task.last_verdict.verdict) : ''

  const parts = [
    `${glyph}${breached ? '!' : ' '}`,
    id,
    title,
    roleTier,
    owner,
    `#${attempt}`,
    `${tokens}tok`,
    worktreeLabel,
    integrated,
    verdict ? `monitor:${verdict}` : '',
    breachText ? `breach:${breachText}` : '',
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
  const counts: Record<string, number> = {}
  let breaches = asBreachCount(run.breaches_seen)
  for (const task of Object.values(tasks)) {
    const state = asString(task.state)
    counts[state] = (counts[state] ?? 0) + 1
    breaches += asBreachCount(task.breaches_seen)
  }
  const summary = ['working', 'claimed', 'input-required', 'submitted', 'ready', 'planned', 'fixing', 'rejected', 'stale', 'released', 'accepted', 'failed', 'canceled']
    .filter(state => counts[state])
    .map(state => `${counts[state]} ${state}`).join(' · ')
  return `ale ${runId}${summary ? ` · ${summary}` : ''} · ${asNumber(run.tokens)} tokens · ${breaches} breaches`
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
