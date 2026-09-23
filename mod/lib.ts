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
  last_event_ts?: unknown
}

export type LabelData = Record<string, any>
export type AleEvent = Record<string, any> & { type: string; ts?: number; task_id?: string | null; agent_id?: string | null }

export type RunResolution = { runDir: string; source: 'env' | 'cwd' | 'launch' | 'arg' }
export type ListedRun = { dir: string; path?: string; run_id?: string; last_event_ts: number }

/** Resolve an explicit run, the configured run, or the first row returned by ale runs. */
export function chooseRun(input: { arg?: string; argRunsDir?: string; envDir?: string; runs?: readonly ListedRun[]; runsError?: string }): { runDir: string; source: 'arg' | 'env' | 'latest' } | undefined {
  const arg = input.arg?.trim()
  if (arg) return { runDir: arg.startsWith('/') ? arg : input.argRunsDir ? `${input.argRunsDir}/${arg}` : arg, source: 'arg' }
  if (input.envDir) return { runDir: input.envDir, source: 'env' }
  if (input.runsError) return undefined
  const first = input.runs?.[0]
  return first ? { runDir: first.path ?? first.dir, source: 'latest' } : undefined
}

/** Format recent alternatives as a single terminal-safe line. */
export function formatOtherRunsLine(selectedRunDir: string, runs: readonly ListedRun[], nowS: number, columns: number): string | undefined {
  const recent = runs.filter(run => run.path !== selectedRunDir && run.dir !== selectedRunDir && nowS - run.last_event_ts <= 86400 && nowS >= run.last_event_ts)
    .slice(0, 3)
  if (!recent.length) return undefined
  const parts = recent.map(run => `${run.run_id ?? run.dir} ${ageOf(run.last_event_ts, nowS)}`)
  return truncateTo(`Other runs: ${parts.join(' · ')}`, columns)
}

export function runDirFromCurrent(input: {
  runsDir: string
  kind: 'file' | 'dir' | 'other'
  realPath?: string
  text?: string
}): string | undefined {
  if (input.kind === 'dir') return input.realPath ?? `${input.runsDir}/current`
  if (input.kind !== 'file') return undefined
  const text = input.text?.trim() ?? ''
  if (!text) return undefined
  if (text.startsWith('/')) return text
  if (text.includes('/')) return undefined
  return `${input.runsDir}/${text}`
}

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
  sub?: string
  phase?: string
  risk?: string
  effort?: string
  lane?: string
}

export type BoardMetadata = { pid?: unknown; url?: unknown; instance_id?: unknown; updated_ts?: unknown }

export const STATE_DISPLAY = [
  { raw: 'input-required', label: 'Needs your answer', glyph: '?', tone: 'needs', section: 'Needs you' },
  { raw: 'released-fail', label: 'Failed to start', glyph: '✗', tone: 'fail', section: 'Needs you' },
  { raw: 'out-of-attempts', label: 'Out of attempts', glyph: '■', tone: 'fail', section: 'Needs you' },
  { raw: 'rejected', label: 'Rejected', glyph: '✕', tone: 'fail', section: 'Needs you' },
  { raw: 'failed', label: 'Failed', glyph: '✗', tone: 'fail', section: 'Needs you' },
  { raw: 'stale', label: 'No heartbeat', glyph: '~', tone: 'needs', section: 'Needs you' },
  { raw: 'working', label: 'Running', glyph: '●', tone: 'run', section: 'Running' },
  { raw: 'claimed', label: 'Running', glyph: '●', tone: 'run', section: 'Running' },
  { raw: 'submitted', label: 'Verifying', glyph: '»', tone: 'verify', section: 'Running' },
  { raw: 'fixing', label: 'Waiting on fix', glyph: '↻', tone: 'wait', section: 'Waiting' },
  { raw: 'ready', label: 'Ready', glyph: '○', tone: 'ready', section: 'Waiting' },
  { raw: 'released', label: 'Retrying', glyph: '↺', tone: 'ready', section: 'Waiting' },
  { raw: 'planned', label: 'Waiting', glyph: '·', tone: 'wait', section: 'Waiting' },
  { raw: 'accepted', label: 'Done', glyph: '✓', tone: 'done', section: 'Done' },
  { raw: 'canceled', label: 'Canceled', glyph: '⦸', tone: 'cancel', section: 'Done' },
] as const

export type DisplayState = typeof STATE_DISPLAY[number] | { raw: string; label: string; glyph: string; tone: 'cancel'; section: 'Waiting' }

export function displayState(task: RawTask, nowS = Date.now() / 1000): DisplayState {
  const raw = asString(task.state, 'unknown')
  if (raw === 'released' && asString((task.outcome as Record<string, unknown> | undefined)?.reason).toLowerCase().startsWith('spawn failed')) return STATE_DISPLAY[1]
  if (raw === 'rejected' && asNumber(task.attempt) > asNumber(task.max_attempts, Number.POSITIVE_INFINITY) && !(Array.isArray(task.fixed_by) && task.fixed_by.some(id => String(id)))) return STATE_DISPLAY[2]
  if ((raw === 'working' || raw === 'claimed') && asNumber(task.lease_expires_ts) < nowS) return STATE_DISPLAY[5]
  return STATE_DISPLAY.find(row => row.raw === raw) ?? { raw, label: `Unknown: ${raw}`, glyph: '◇', tone: 'cancel', section: 'Waiting' }
}

function boardState(id: string, task: RawTask, tasks: Record<string, RawTask>, nowS: number): DisplayState {
  if (task.state === 'rejected') {
    const parent = Object.entries(tasks).find(([, candidate]) =>
      candidate.state === 'accepted' && Array.isArray(candidate.fixes) && candidate.fixes.includes(id))
    if (parent) return { raw: 'superseded', label: 'Superseded', glyph: '→', tone: 'done', section: 'Done' }
  }
  return displayState(task, nowS)
}

export function formatTokens(value: unknown): string {
  const n = asNumber(value)
  if (n < 1000) return Math.round(n).toLocaleString('en')
  if (n < 10000) return `${(n / 1000).toFixed(1)}k`
  if (n < 1000000) return `${Math.round(n / 1000)}k`
  if (n < 10000000) return `${(n / 1000000).toFixed(1)}M`
  return `${Math.round(n / 1000000)}M`
}

function formatAge(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

export function attentionOrder(tasks: Record<string, RawTask>): string[] {
  const need = Object.entries(tasks).filter(([, task]) => displayState(task).section === 'Needs you')
  return need.sort(([a, left], [b, right]) => {
    const leftSeverity = displayState(left).tone === 'fail' ? 0 : 1
    const rightSeverity = displayState(right).tone === 'fail' ? 0 : 1
    return leftSeverity - rightSeverity || a.localeCompare(b)
  }).map(([id]) => id)
}

export function boardUrl(metadata: BoardMetadata | undefined, currentPid: number, nowS = Date.now() / 1000): string | undefined {
  if (!metadata || asNumber(metadata.pid) !== currentPid || typeof metadata.instance_id !== 'string' || !metadata.instance_id) return undefined
  if (typeof metadata.updated_ts === 'number' && nowS - metadata.updated_ts > 30) return undefined
  if (typeof metadata.url !== 'string') return undefined
  try {
    const parsed = new URL(metadata.url)
    if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || !parsed.port || !parsed.pathname.endsWith('/')) return undefined
    return metadata.url
  } catch { return undefined }
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

/** Runs the plugin's bundled ALE package while passing every path as an argv item. */
export function bundledAleArgv(pluginRoot: string, args: readonly string[]): string[] {
  const bootstrap = 'import sys; sys.path.insert(0, sys.argv[1]); from ale.cli import main; sys.exit(main(sys.argv[2:]))'
  return ['python3', '-c', bootstrap, pluginRoot, ...args]
}

/** Logs each distinct refresh error once for the lifetime of this module. */
export function createRefreshErrorLogger(): (log: (message: string) => void, message: string) => void {
  const seen = new Set<string>()
  return (log, message) => {
    if (seen.has(message)) return
    seen.add(message)
    log(message)
  }
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
  const chars = [...text]
  if (chars.length <= width) return text
  if (width === 1) return chars[0] ?? ''
  return `${chars.slice(0, width - 1).join('')}…`
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
  fixing: '↻',
}

/** One glyph per state (rule 27: glyphs, not only colour — survives `tmux capture-pane -p`). */
export function glyphFor(state: string): string {
  return GLYPH_OF[state] ?? '◇'
}

export type BoardSegment = { text: string; color?: string; bold?: boolean; dimColor?: boolean }
export type RenderBoardInput = { runId: string; runPath?: string; tasks: Record<string, RawTask>; run: RawRun; labels: Record<string, LabelInfo>; otherRuns?: readonly ListedRun[]; boardUrl?: string; nowS: number; columns: number }

const toneColor: Record<string, string> = { needs: 'yellow', fail: 'red', run: 'blue', verify: 'magenta', ready: 'cyan', done: 'green' }
const sectionOrder = ['Needs you', 'Running', 'Waiting', 'Done'] as const

function plural(n: number, one: string, many = `${one}s`): string { return `${n} ${n === 1 ? one : many}` }
function rowReason(task: RawTask, tasks: Record<string, RawTask>, nowS: number, supersededBy?: string): string {
  if (supersededBy) return `Superseded: parent ${supersededBy} accepted`
  const info = displayState(task, nowS)
  if (info.raw === 'input-required') return `Needs your answer: "${String(task.waiting_on ?? '')}"`
  if (info.raw === 'rejected') return `Rejected: ${String(task.last_reject_reason ?? 'needs a fix')}`
  if (info.raw === 'failed') return `Failed: ${String(task.reason ?? 'executor failed')}`
  if (info.raw === 'stale') return 'No heartbeat. The executor may have died.'
  const unmet = (Array.isArray(task.depends_on) ? task.depends_on : []).filter(dep => tasks[String(dep)]?.state !== 'accepted')
  if (unmet.length) return `Waiting on ${unmet.join(', ')} (blocked by ${unmet[0]}, needs you)`
  if (info.raw === 'fixing') return `Waiting on fix ${(Array.isArray(task.fixed_by) ? task.fixed_by : []).join(', ')}`
  if (info.raw === 'ready') return 'Ready, not picked up yet.'
  if (info.raw === 'released') return `Retrying: attempt ${String(task.attempt ?? 1)} of ${String(task.max_attempts ?? '?')}.`
  return ''
}

function boardLine(text: string, columns: number, color?: string, bold?: boolean): BoardSegment[] {
  const safe = truncateTo(text, columns)
  return [{ text: safe, ...(color ? { color } : {}), ...(bold ? { bold: true } : {}) }]
}

export function renderBoard(input: RenderBoardInput): BoardSegment[][] {
  const { tasks, labels, nowS, columns } = input
  const entries = Object.entries(tasks).map(([id, task]) => ({ id, task, state: boardState(id, task, tasks, nowS) }))
  const inSection = (section: string) => entries.filter(row => row.state.section === section).sort((a, b) => section === 'Done'
    ? Number(b.state.raw === 'superseded') - Number(a.state.raw === 'superseded') || asNumber(b.task.submitted_ts) - asNumber(a.task.submitted_ts) || a.id.localeCompare(b.id)
    : a.id.localeCompare(b.id))
  const needs = inSection('Needs you'), running = inSection('Running'), waiting = inSection('Waiting'), done = inSection('Done')
  const completed = done.filter(row => row.state.raw !== 'superseded')
  const total = entries.length
  const runId = input.runId
  const headline = running.length === 0 && needs.length > 0
    ? `Nothing is running. ${needs.length === 1 ? '1 needs you' : `${needs.length} need you`}; ${waiting.length === 1 ? '1 is' : `${waiting.length} are`} waiting behind them.`
    : `${running.length} running, ${waiting.length} waiting, ${completed.length} of ${total} done.`
  const ribbon = total > 40
    ? `✓ ${done.length}  ● ${running.length}  ? ${needs.length}  · ${waiting.length}`
    : [...done.map(() => '✓'), ...running.map(r => r.state.glyph), ...needs.map(r => r.state.glyph), ...waiting.map(r => r.state.glyph)].join('')
  const lastEvent = asNumber(input.run.last_event_ts)
  const idleAgeHours = lastEvent > 0 ? Math.floor((input.nowS - lastEvent) / 3600) : 0
  const idleLabel = running.length === 0 && idleAgeHours > 1 ? `Idle, last activity ${idleAgeHours}h ago` : 'Idle'
  const doneLabel = input.run.finished === true ? 'Finished' : needs.length ? 'Stalled: needs you' : running.length ? 'Running' : idleLabel
  const doneColor = needs.length && !running.length ? 'red' : running.length ? 'blue' : 'green'
  const runTokens = asNumber(input.run.tokens)
  const cost = asNumber(input.run.cost_usd)
  const rows: BoardSegment[][] = [
    [{ text: 'ALE ' }, ...(!input.boardUrl ? [{ text: 'web: /ale:board  ' }] : []), { text: `${runId}  ` }, { text: `■ ${doneLabel}`, color: doneColor, ...(doneColor === 'red' ? { bold: true } : {}) }],
    ...(input.runPath ? [boardLine(`Run directory: ${input.runPath}`, columns)] : []),
    ...(lastEvent > 0 ? [boardLine(`Last event ${formatAge(Math.max(0, input.nowS - lastEvent))} ago`, columns)] : []),
    ...(formatOtherRunsLine(input.runPath ?? '', input.otherRuns ?? [], input.nowS, columns) ? [boardLine(formatOtherRunsLine(input.runPath ?? '', input.otherRuns ?? [], input.nowS, columns)!, columns)] : []),
    boardLine(headline, columns),
    boardLine(`${ribbon}  ${completed.length} of ${total} done   ${formatTokens(runTokens)} tok, run total   ${cost ? `$${cost.toFixed(2)}` : runTokens ? 'cost not reported' : '$0.00'}`, columns),
  ]
  const prefix = (id: string, state: DisplayState, boldId = false): BoardSegment[] => [
    { text: `${state.glyph} `, color: toneColor[state.tone] ?? undefined, ...(['wait', 'cancel'].includes(state.tone) ? { dimColor: true } : {}) },
    { text: id.padEnd(8), ...(boldId ? { bold: true } : {}) },
    { text: ` ${state.label.padEnd(17)} `, color: toneColor[state.tone] ?? undefined, ...(['wait', 'cancel'].includes(state.tone) ? { dimColor: true } : {}) },
  ]
  const plainRow = (id: string, task: RawTask, state: DisplayState, section: string): BoardSegment[][] => {
    const title = labels[id]?.title ?? id
    const p = prefix(id, state, section === 'Needs you')
    const base = p.map(s => s.text).join('')
    const supersededBy = state.raw === 'superseded'
      ? Object.entries(tasks).find(([, parent]) => parent.state === 'accepted' && Array.isArray(parent.fixes) && parent.fixes.includes(id))?.[0]
      : undefined
    const reason = rowReason(task, tasks, nowS, supersededBy)
    if (section === 'Needs you') {
      const first = `${base}${title}`
      const lines = [p.concat([{ text: truncateTo(`${title}`, Math.max(0, columns - [...base].length)) }])]
      if (reason) lines.push([{ text: ' '.repeat(11) }, { text: truncateTo(reason, Math.max(0, columns - 11)) }])
      lines.push([{ text: ' '.repeat(11) }, { text: `Blocks ${entries.filter(other => (Array.isArray(other.task.depends_on) && other.task.depends_on.includes(id))).length} ${entries.filter(other => Array.isArray(other.task.depends_on) && other.task.depends_on.includes(id)).length === 1 ? 'task' : 'tasks'}` }])
      if (columns >= 120 && asNumber(task.tokens)) lines[lines.length - 1].push({ text: `   ${formatTokens(task.tokens)} tok` })
      return lines
    }
    const titleWidth = columns >= 120 ? 32 : columns >= 80 ? 16 : Math.max(0, columns - 29)
    const titleText = truncateTo(title, titleWidth)
    if (columns < 80) return [p.concat([{ text: titleText }])]
    const reasonText = state.raw === 'superseded' ? reason : section === 'Done' ? `Done ${ageOf(task.submitted_ts, nowS)} ago` : reason
    const tokenMeta = section === 'Done' && columns >= 120 && asNumber(task.tokens) ? `${formatTokens(task.tokens)} tok` : ''
    const paddedTitle = titleText.padEnd(titleWidth)
    const reasonWidth = Math.max(0, columns - [...base + paddedTitle].length - 1 - (tokenMeta ? [...tokenMeta].length + 1 : 0))
    const reasonPart = truncateTo(reasonText, reasonWidth)
    const fill = tokenMeta ? Math.max(1, columns - [...base + paddedTitle + ' ' + reasonPart + tokenMeta].length) : 0
    return [p.concat([{ text: `${paddedTitle} ` }, { text: reasonPart }, ...(tokenMeta ? [{ text: `${' '.repeat(fill)}${tokenMeta}` }] : [])])]
  }
  rows.push(boardLine(`Needs you ${needs.length}`, columns))
  for (const row of needs) rows.push(...plainRow(row.id, row.task, row.state, 'Needs you'))
  rows.push(boardLine(`Running ${running.length}`, columns))
  if (!running.length) rows.push(boardLine('  Nothing is running.', columns))
  else for (const row of running) rows.push(...plainRow(row.id, row.task, row.state, 'Running'))
  rows.push(boardLine(`Waiting ${waiting.length}`, columns))
  const firstWaiting = waiting[0]
  if (firstWaiting) {
    const dependencies = Array.isArray(firstWaiting.task.depends_on) ? firstWaiting.task.depends_on : []
    const firstDep = dependencies.length ? String(dependencies[0] ?? '') : ''
    const blockerTask = firstDep ? tasks[firstDep] : undefined
    const blocker = blockerTask ? displayState(blockerTask, nowS) : firstWaiting.state
    rows.push(boardLine(`  Blocked by ${firstDep || firstWaiting.id} (${blocker.label}), ${plural(waiting.length, 'task')}`, columns))
  }
  for (const row of waiting) rows.push(...plainRow(row.id, row.task, row.state, 'Waiting'))
  rows.push(boardLine(`Done ${done.length}`, columns))
  for (const row of done.slice(0, 3)) rows.push(...plainRow(row.id, row.task, row.state, 'Done'))
  if (done.length > 3) rows.push(boardLine(`  +${done.length - 3} more done`, columns))
  if (input.boardUrl) rows.push(boardLine(`board: ${input.boardUrl}  (copy into a browser) · /ale-board <run> switches`, columns))
  else rows.push(boardLine('/ale-board <run> switches', columns))
  return rows.map(line => {
    const text = line.map(segment => segment.text).join('')
    return [...text].length > columns ? boardLine(text, columns) : line
  })
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
  if (hours < 24) return `${hours}h ${restMinutes}m`
  const days = Math.floor(hours / 24)
  const restHours = hours % 24
  return `${days}d ${restHours}h`
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
