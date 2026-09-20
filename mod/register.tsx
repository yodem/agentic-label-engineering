// ale-board: never name a local `h` in this file — every JSX tag compiles to
// a call of `h`, the JSX factory global. This is the first rule of
// writing function-hook plugins: a local `h` breaks the first draw.
//
// This module wires Claude Code's function-hook events to the pure helpers
// in ./lib.ts. It draws two sites: the AbovePrompt band (one line, while a
// run exists and the board is open) and a docked/inline Pane (the full task
// board, grouped into columns). It is a read-only observation plane: it
// only ever runs `python3 -m ale status --json` (or the configured
// equivalent) and reads label files under the run directory with `$.fs`. It
// never writes into the run directory and never submits a prompt.
import type { EngineInterface, On, Register } from 'claude-code'
import {
  type LabelInfo,
  type RawStatus,
  type Section,
  bandLine,
  budgetRows,
  formatTaskRow,
  groupTaskIds,
  noRunMessage,
  parseStatusOutput,
  pickLatestRunDir,
  truncateTo,
} from './lib.ts'

const PLUGIN = 'ale'
const COMMAND = 'ale-board'
const PANE_ID = 'ale-board'
const ARGUMENT_HINT = '[close | refresh | run <run_id>]'
const DEFAULT_ALE_COMMAND = 'python3 -m ale'

const STORE_OPEN_KEY = 'ale-board:isOpen'
const STORE_RUN_KEY = 'ale-board:selectedRunDir'

const REFRESH_DEBOUNCE_MS = 1500
const POLL_INTERVAL_MS = 10_000
const STATUS_TIMEOUT_MS = 15_000
const FIXED_HEADER_ROWS = 2 // title row + counts/refresh-time row

/** What we display: a resolved run's tasks, or an explanation of why not. */
type BoardModel =
  | { kind: 'no-run' }
  | { kind: 'error'; runLabel: string; message: string }
  | { kind: 'ok'; runLabel: string; status: RawStatus; labels: Record<string, LabelInfo>; refreshedAtS: number }

type Timer = { cancel: () => void }

/** Everything a hook needs, bound once from `$` at `session.start`. Every
 * function below takes this `Host`, never `$` directly, so it runs against
 * a fake in a future test. */
type Host = {
  cwd: string
  aleCommand: string[]
  run: (argv: readonly string[], init?: { cwd?: string; env?: Record<string, string>; timeoutMs?: number }) => Promise<{ exitCode: number; stdout: string; stderr: string }>
  envGetAleRunDir: () => Promise<string | undefined>
  envGetAleRoster: () => Promise<string | undefined>
  fsList: (path?: string) => Promise<readonly { name: string; kind: string }[]>
  fsStat: (path: string) => Promise<{ mtimeMs: number }>
  fsRead: (path: string) => Promise<string>
  fsExists: (path: string) => Promise<boolean>
  storeGet: (key: string) => Promise<unknown>
  storeSet: (key: string, value: unknown) => Promise<void>
  invalidate: () => void
  uiLog: (text: string) => void
  uiOpen: (title: string) => Promise<void>
  uiClose: () => Promise<void>
  clockAfter: (ms: number, fn: () => void) => Timer
  clockEvery: (ms: number, fn: () => void) => Timer
}

let host: Host | undefined
let isOpen = false
let selectedRunDir: string | undefined
let model: BoardModel = { kind: 'no-run' }
let refreshing: Promise<BoardModel> | undefined
let refreshTimer: Timer | undefined
let pollTimer: Timer | undefined

function bind($: EngineInterface, options: Readonly<Record<string, string | number | boolean | readonly string[]>>): Host {
  const configured = options.aleCommand
  const aleCommand = (typeof configured === 'string' && configured.trim() ? configured.trim() : DEFAULT_ALE_COMMAND).split(/\s+/).filter(Boolean)
  return {
    cwd: '',
    aleCommand,
    run: (argv, init) => $.process.run(argv, init),
    // $.env.get names must be string literals (book rule 4), so one method per variable.
    envGetAleRunDir: () => $.env.get('ALE_RUN_DIR'),
    envGetAleRoster: () => $.env.get('ALE_ROSTER'),
    fsList: path => $.fs.list(path),
    fsStat: path => $.fs.stat(path),
    fsRead: path => $.fs.read(path),
    fsExists: path => $.fs.exists(path),
    storeGet: key => $.store.get(key),
    storeSet: (key, value) => $.store.set(key, value),
    invalidate: () => $.ui.invalidate('ui.render'),
    uiLog: text => $.ui.log(text),
    uiOpen: title =>
      $.ui.open({ id: PANE_ID, title, closeOnEscape: true, holdToasts: true, rows: 18 }),
    uiClose: () => $.ui.close({ id: PANE_ID }),
    clockAfter: (ms, fn) => $.clock.after(ms, fn),
    clockEvery: (ms, fn) => $.clock.every(ms, fn),
  }
}

function setOpen(engine: Host, open: boolean): void {
  isOpen = open
  void engine.storeSet(STORE_OPEN_KEY, open).catch(() => undefined)
  if (open) {
    startPolling(engine)
  } else {
    pollTimer?.cancel()
    pollTimer = undefined
  }
  engine.invalidate()
}

function startPolling(engine: Host): void {
  if (pollTimer) return
  pollTimer = engine.clockEvery(POLL_INTERVAL_MS, () => {
    if (!isOpen) return
    void refresh(engine)
  })
}

/** `ALE_RUN_DIR`, else the most recently modified directory under
 * `<cwd>/.ale/runs/`. Never guesses a path outside the session's cwd. */
async function discoverRunDir(engine: Host): Promise<string | undefined> {
  const envDir = await engine.envGetAleRunDir().catch(() => undefined)
  if (envDir) return envDir
  const runsDir = `${engine.cwd}/.ale/runs`
  const exists = await engine.fsExists(runsDir).catch(() => false)
  if (!exists) return undefined
  const entries = await engine.fsList(runsDir).catch(() => [])
  const dirEntries = entries.filter(entry => entry.kind === 'directory')
  const stats = await Promise.all(
    dirEntries.map(async entry => {
      const stat = await engine.fsStat(`${runsDir}/${entry.name}`).catch(() => undefined)
      return stat === undefined ? undefined : { name: entry.name, mtimeMs: stat.mtimeMs }
    }),
  )
  const picked = pickLatestRunDir(stats.filter((s): s is { name: string; mtimeMs: number } => s !== undefined))
  return picked === undefined ? undefined : `${runsDir}/${picked}`
}

async function resolveRosterPath(engine: Host): Promise<string> {
  const envRoster = await engine.envGetAleRoster().catch(() => undefined)
  if (envRoster) return envRoster
  const atCwd = `${engine.cwd}/roster.json`
  if (await engine.fsExists(atCwd).catch(() => false)) return atCwd
  return `${engine.cwd}/.ale/roster.json`
}

function runLabelOf(runDir: string): string {
  const parts = runDir.split('/').filter(Boolean)
  return parts[parts.length - 1] ?? runDir
}

async function loadLabels(engine: Host, runDir: string, taskIds: readonly string[]): Promise<Record<string, LabelInfo>> {
  const out: Record<string, LabelInfo> = {}
  await Promise.all(
    taskIds.map(async id => {
      const path = `${runDir}/labels/${id}.json`
      const text = await engine.fsRead(path).catch(() => undefined)
      if (text === undefined) return
      try {
        const parsed = JSON.parse(text) as Record<string, unknown>
        const title = typeof parsed.title === 'string' ? parsed.title : undefined
        const labels = parsed.labels
        const role = labels && typeof labels === 'object' && labels !== null && typeof (labels as Record<string, unknown>).role === 'string' ? ((labels as Record<string, unknown>).role as string) : undefined
        const tier = labels && typeof labels === 'object' && labels !== null && typeof (labels as Record<string, unknown>).model_tier === 'string' ? ((labels as Record<string, unknown>).model_tier as string) : undefined
        out[id] = { title, role, tier }
      } catch {
        // a malformed label file just yields no title/role/tier for this task
      }
    }),
  )
  return out
}

/** Runs `<aleCommand> status --json --run-dir <dir> --roster <path>`,
 * single-flight, and returns the model it settled on (also stored in the
 * module-level `model` for the render hooks to read). */
async function refresh(engine: Host): Promise<BoardModel> {
  if (refreshing) return refreshing
  refreshing = (async (): Promise<BoardModel> => {
    const runDir = selectedRunDir ?? (await discoverRunDir(engine))
    if (runDir === undefined) {
      const next: BoardModel = { kind: 'no-run' }
      model = next
      engine.invalidate()
      return next
    }
    const runLabel = runLabelOf(runDir)
    const rosterPath = await resolveRosterPath(engine)
    const argv = [...engine.aleCommand, 'status', '--json', '--run-dir', runDir, '--roster', rosterPath]
    const result = await engine
      .run(argv, { cwd: engine.cwd, timeoutMs: STATUS_TIMEOUT_MS })
      .catch(err => ({ exitCode: -1, stdout: '', stderr: err instanceof Error ? err.message : String(err) }))
    const parsed = parseStatusOutput(result.exitCode, result.stdout, result.stderr)
    if (!parsed.ok) {
      const next: BoardModel = { kind: 'error', runLabel, message: parsed.error }
      model = next
      engine.uiLog(`${PLUGIN}: ${parsed.error}`)
      engine.invalidate()
      return next
    }
    const labels = await loadLabels(engine, runDir, Object.keys(parsed.status.tasks))
    const next: BoardModel = { kind: 'ok', runLabel, status: parsed.status, labels, refreshedAtS: Date.now() / 1000 }
    model = next
    engine.invalidate()
    return next
  })()
  try {
    return await refreshing
  } finally {
    refreshing = undefined
  }
}

function scheduleRefresh(engine: Host): void {
  refreshTimer?.cancel()
  refreshTimer = engine.clockAfter(REFRESH_DEBOUNCE_MS, () => {
    refreshTimer = undefined
    void refresh(engine)
  })
}

// ---- View: pure-ish string layout, called from the render hooks only ----

function bandText(): string | undefined {
  if (model.kind !== 'ok') return undefined
  if (model.status.run.finished === true) return undefined
  return bandLine(model.runLabel, model.status.tasks, model.status.run)
}

function buildSections(status: RawStatus, labels: Record<string, LabelInfo>, columns: number): Section[] {
  const nowS = Date.now() / 1000
  const grouped = groupTaskIds(status.tasks)
  return grouped.map(({ name, ids }) => ({
    name,
    rows: ids.map(id => {
      const task = status.tasks[id]
      if (task === undefined) return `${id}`
      return formatTaskRow(id, task, labels[id], nowS, columns).text
    }),
  }))
}

const SECTION_TITLE: Record<string, string> = {
  'input-required': 'input-required',
  working: 'working / claimed',
  submitted: 'submitted',
  ready: 'ready',
  attention: 'rejected / stale / released',
  planned: 'planned',
  accepted: 'accepted',
  failed: 'failed',
  canceled: 'canceled',
}

// ---- register ----

export const register: Register = (on: On) => {
  on('session.start', async ($, e, next) => {
    host = bind($, {})
    host.cwd = e.cwd
    const engine = host
    isOpen = (await engine.storeGet(STORE_OPEN_KEY).catch(() => false)) === true
    const storedRun = await engine.storeGet(STORE_RUN_KEY).catch(() => undefined)
    selectedRunDir = typeof storedRun === 'string' ? storedRun : undefined
    await $.command
      .register({
        name: COMMAND,
        description: 'ALE task board: tasks and their status, grouped into columns (ale-board)',
        argumentHint: ARGUMENT_HINT,
        immediate: true,
      })
      .catch(err => $.ui.log(`${PLUGIN}: /${COMMAND} not registered: ${err}`))
    if (isOpen) {
      startPolling(engine)
      await $.ui.open({ id: PANE_ID, title: 'ALE task board', closeOnEscape: true, holdToasts: true, rows: 18 }).catch(() => undefined)
      void refresh(engine)
    }
    return next(e)
  })

  on('command.run', { command: COMMAND }, async ($, e) => {
    if (!host) return { text: 'not ready yet, try again in a moment' }
    const engine = host
    const arg = e.args.trim()
    const lower = arg.toLowerCase()

    if (lower === 'close' || lower === 'stop') {
      setOpen(engine, false)
      await engine.uiClose().catch(() => undefined)
      return { text: 'closed' }
    }
    if (lower === 'refresh') {
      setOpen(engine, true)
      await $.ui.open({ id: PANE_ID, title: 'ALE task board', closeOnEscape: true, holdToasts: true, rows: 18 }).catch(() => undefined)
      const after = await refresh(engine)
      return { text: after.kind === 'error' ? `refresh failed · ${after.message}` : 'refreshed' }
    }
    if (lower.startsWith('run ')) {
      const runArg = arg.slice('run '.length).trim()
      if (!runArg) return { text: `unknown argument "${arg}" · /${COMMAND} ${ARGUMENT_HINT}` }
      selectedRunDir = runArg
      void engine.storeSet(STORE_RUN_KEY, selectedRunDir).catch(() => undefined)
      model = { kind: 'no-run' }
      setOpen(engine, true)
      await $.ui.open({ id: PANE_ID, title: 'ALE task board', closeOnEscape: true, holdToasts: true, rows: 18 }).catch(() => undefined)
      const after = await refresh(engine)
      return { text: after.kind === 'error' ? `run ${runArg}: ${after.message}` : `board on ${runArg}` }
    }
    if (arg) return { text: `unknown argument "${arg}" · /${COMMAND} ${ARGUMENT_HINT}` }

    setOpen(engine, true)
    await $.ui.open({ id: PANE_ID, title: 'ALE task board', closeOnEscape: true, holdToasts: true, rows: 18 }).catch(() => undefined)
    let current = model
    if (current.kind === 'no-run' || current.kind === 'error') {
      current = await refresh(engine)
    } else {
      engine.invalidate()
      scheduleRefresh(engine)
    }
    return { text: current.kind === 'error' ? `board above the prompt (last refresh failed) · /${COMMAND} close closes` : `board above the prompt · /${COMMAND} close closes` }
  })

  on('ui.close', { id: PANE_ID }, async ($, e, next) => {
    if (host && e.origin.kind === 'person') setOpen(host, false)
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined && host && isOpen) scheduleRefresh(host)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!isOpen || e.props.hasSurvey || e.surface !== 'terminal') return next(e)
    const line = bandText()
    if (line === undefined) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        <Text>{truncateTo(line, e.props.bodyColumns)}</Text>
        {await next(e)}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE_ID) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const columns = e.props.bodyColumns
    const maxRows = e.props.scroll.bodyRows

    if (model.kind === 'no-run') {
      return (
        <Box flexDirection="column">
          <Text bold>ale-board</Text>
          <Text>{truncateTo(noRunMessage(), columns)}</Text>
        </Box>
      )
    }
    if (model.kind === 'error') {
      return (
        <Box flexDirection="column">
          <Text bold>{truncateTo(`ale-board · ${model.runLabel}`, columns)}</Text>
          <Text>{truncateTo(`error: ${model.message}`, columns)}</Text>
        </Box>
      )
    }

    const sections = buildSections(model.status, model.labels, columns)
    const budgeted = budgetRows(sections, Math.max(1, maxRows - FIXED_HEADER_ROWS))
    const counts = sections.map(s => `${s.name}:${s.rows.length}`).join(' ')
    const tokens = typeof model.status.run.tokens === 'number' ? model.status.run.tokens : 0
    const cost = typeof model.status.run.cost_usd === 'number' ? model.status.run.cost_usd : 0
    const refreshedAgo = Math.max(0, Math.round(Date.now() / 1000 - model.refreshedAtS))

    return (
      <Box flexDirection="column">
        <Text bold>{truncateTo(`ale-board · ${model.runLabel} · ${counts}`, columns)}</Text>
        <Text dimColor>{truncateTo(`${tokens} tok · $${cost.toFixed(2)} · refreshed ${refreshedAgo}s ago`, columns)}</Text>
        {budgeted
          .filter(section => section.rows.length > 0)
          .map(section => (
            <Box key={section.name} flexDirection="column">
              <Text dimColor>{truncateTo(SECTION_TITLE[section.name] ?? section.name, columns)}</Text>
              {section.rows.map((row, index) => (
                <Text key={`${section.name}-${index}`}>{row}</Text>
              ))}
            </Box>
          ))}
      </Box>
    )
  })
}
