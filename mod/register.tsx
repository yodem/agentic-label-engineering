// ale-board: never name a local `h` in this file — every JSX tag compiles to
// a call of `h`, the JSX factory global. This is the first rule of
// writing function-hook plugins: a local `h` breaks the first draw.
//
// This module wires Claude Code's function-hook events to the pure helpers
// in ./lib.ts. It draws two sites: the AbovePrompt band (one line, while a
// run exists and the board is open) and a docked/inline Pane (the full task
// board, grouped into columns). It is a read-only observation plane: it
// only ever runs the plugin's bundled ALE status command and reads label files under
// the run directory with `$.fs`. It
// never writes into the run directory and never submits a prompt.
import type { On, Register } from 'claude-code'
import {
  type LabelInfo,
  type RawStatus,
  type Section,
  bandLine,
  budgetRows,
  bundledAleArgv,
  createRefreshErrorLogger,
  displayState,
  formatTokens,
  noRunMessage,
  parseStatusOutput,
  resolveRunDirectory,
  runDirFromCurrent,
  truncateTo,
  renderBoard,
  unknownRunArgumentMessage,
} from './lib.ts'

const PLUGIN = 'ale'
const COMMAND = 'ale-board'
const PANE_ID = 'ale-board'
const ARGUMENT_HINT = '[close | refresh | <run_id>]'

const STORE_OPEN_KEY = 'ale-board:isOpen'

const REFRESH_DEBOUNCE_MS = 1500
const POLL_INTERVAL_MS = 10_000
const STATUS_TIMEOUT_MS = 15_000
const FIXED_HEADER_ROWS = 2 // title row + counts/refresh-time row
const logRefreshErrorOnce = createRefreshErrorLogger()

/** What we display: a resolved run's tasks, or an explanation of why not. */
type BoardModel =
  | { kind: 'no-run' }
  | { kind: 'error'; runLabel: string; source: string; message: string }
  | { kind: 'ok'; runLabel: string; source: string; status: RawStatus; labels: Record<string, LabelInfo>; refreshedAtS: number }

type Timer = { cancel: () => void }

/** The host adapter for async helpers. Its engine calls are bound inline in
 * `session.start` so every `$` API access remains literal at the call site. */
type Host = {
  cwd: string
  pluginRoot: string
  liveCwd: () => Promise<string>
  envGetAleRunDir: () => Promise<string | undefined>
  envGetAleRoster: () => Promise<string | undefined>
  fsStat: (path: string, options: { resolve: boolean }) => Promise<{ kind: 'file' | 'dir' | 'other'; isLink?: boolean; realPath?: string; mtimeMs?: number }>
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
  processRun: (argv: readonly string[], options?: { cwd?: string; timeoutMs?: number }) => Promise<{ exitCode: number; stdout: string; stderr: string }>
}

let host: Host | undefined
let isOpen = false
let selectedRun: { runDir: string; source: 'arg' } | undefined
let model: BoardModel = { kind: 'no-run' }
let refreshing: Promise<BoardModel> | undefined
let refreshTimer: Timer | undefined
let pollTimer: Timer | undefined

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

async function nearestRun(engine: Host, start: string): Promise<{ runDir?: string; runsDir?: string }> {
  let dir = start
  let nearestRunsDir: string | undefined
  while (dir) {
    const runsDir = `${dir}/.ale/runs`
    const current = `${runsDir}/current`
    const hasRunsDir = await engine.fsExists(runsDir).catch(() => false)
    if (hasRunsDir && nearestRunsDir === undefined) nearestRunsDir = runsDir
    const currentStat = await engine.fsStat(current, { resolve: true }).catch(() => undefined)
    if (currentStat) {
      const text = currentStat.kind === 'file' ? await engine.fsRead(current).catch(() => '') : undefined
      return { runDir: runDirFromCurrent({ runsDir, kind: currentStat.kind, realPath: currentStat.realPath, text }), runsDir: nearestRunsDir }
    }
    const parent = dir.slice(0, dir.lastIndexOf('/'))
    if (!parent || parent === dir) break
    dir = parent
  }
  return { runsDir: nearestRunsDir }
}

async function discoverRun(engine: Host): Promise<{ runDir: string; source: 'env' | 'cwd' | 'launch' } | undefined> {
  const envDir = await engine.envGetAleRunDir().catch(() => undefined)
  const liveCwd = await engine.liveCwd().catch(() => engine.cwd)
  const cwd = await nearestRun(engine, liveCwd)
  const launch = await nearestRun(engine, engine.cwd)
  const resolved = resolveRunDirectory({ envDir, cwdRunDir: cwd.runDir, launchRunDir: launch.runDir })
  if (!resolved || resolved.source === 'arg') return undefined
  return { runDir: resolved.runDir, source: resolved.source }
}

async function resolveRosterPath(engine: Host, repoRoot = engine.cwd): Promise<string> {
  const envRoster = await engine.envGetAleRoster().catch(() => undefined)
  if (envRoster) return envRoster
  const atCwd = `${repoRoot}/roster.json`
  if (await engine.fsExists(atCwd).catch(() => false)) return atCwd
  return `${repoRoot}/.ale/roster.json`
}

function repositoryRootForRun(runDir: string): string | undefined {
  const marker = '/.ale/runs/'
  const index = runDir.lastIndexOf(marker)
  return index > 0 ? runDir.slice(0, index) : undefined
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
        out[id] = { title, role }
      } catch {
        // a malformed label file just yields no title/role/tier for this task
      }
    }),
  )
  return out
}

/** Reads ALE's authoritative status and the labels used for display names. */
async function refresh(engine: Host): Promise<BoardModel> {
  if (refreshing) return refreshing
  refreshing = (async (): Promise<BoardModel> => {
    const resolved = selectedRun ?? (await discoverRun(engine))
    if (resolved === undefined) {
      const next: BoardModel = { kind: 'no-run' }
      model = next
      engine.invalidate()
      return next
    }
    const runDir = resolved.runDir
    const runLabel = runLabelOf(runDir)
    const repoRoot = repositoryRootForRun(runDir)
    const cwd = repoRoot ?? engine.cwd
    const repoRosterExists = await engine.fsExists(`${cwd}/.ale/roster.json`).catch(() => false)
    const args = ['status', '--json', '--run-dir', runDir]
    if (!repoRosterExists) args.push('--roster', await resolveRosterPath(engine, cwd))
    const result = await engine.processRun(bundledAleArgv(engine.pluginRoot, args), { cwd, timeoutMs: STATUS_TIMEOUT_MS })
    const parsed = parseStatusOutput(result.exitCode, result.stdout, result.stderr)
    if (!parsed.ok) {
      const next: BoardModel = { kind: 'error', runLabel, source: resolved.source, message: parsed.error }
      model = next
      logRefreshErrorOnce(message => engine.uiLog(`${PLUGIN}: ${message}`), next.message)
      engine.invalidate()
      return next
    }
    const labels = await loadLabels(engine, runDir, Object.keys(parsed.status.tasks))
    const next: BoardModel = { kind: 'ok', runLabel, source: resolved.source, status: parsed.status, labels, refreshedAtS: Date.now() / 1000 }
    model = next
    engine.invalidate()
    return next
  })().catch(err => {
    const next: BoardModel = {
      kind: 'error',
      runLabel: selectedRun ? runLabelOf(selectedRun.runDir) : 'unknown run',
      source: selectedRun?.source ?? 'launch',
      message: err instanceof Error ? err.message : String(err),
    }
    model = next
    logRefreshErrorOnce(message => engine.uiLog(`${PLUGIN}: ${message}`), next.message)
    engine.invalidate()
    return next
  })
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
  return `${bandLine(model.runLabel, model.status.tasks, model.status.run)} · source:${model.source}`
}

function buildSections(status: RawStatus, labels: Record<string, LabelInfo>, columns: number): Section[] {
  const nowS = Date.now() / 1000
  const order = ['Needs you', 'Running', 'Waiting', 'Done']
  const rows = Object.entries(status.tasks).map(([id, task]) => {
    const info = displayState(task, nowS)
    const reason = taskReason(task, status.tasks, nowS)
    const title = labels[id]?.title ?? id
    const parent = labels[id]?.fixes ? `↳ ${labels[id]?.fixes} ` : ''
    return { id, section: info.section, text: truncateTo(`${info.glyph} ${id.padEnd(8)} ${info.label} ${parent}${title} ${reason}`, columns) }
  })
  return order.map(name => ({ name, rows: rows.filter(row => row.section === name).sort((a, b) => a.id.localeCompare(b.id)).map(row => row.text) }))
}

function taskReason(task: RawStatus['tasks'][string], tasks: RawStatus['tasks'], nowS: number): string {
  const info = displayState(task, nowS)
  if (info.raw === 'input-required') return `Needs your answer: ${String(task.waiting_on ?? '')}`
  if (info.raw === 'released-fail') return `Failed to start: ${String((task.outcome as Record<string, unknown> | undefined)?.reason ?? '').split('\n').at(0)?.slice(0, 120) ?? ''}`
  if (info.raw === 'rejected') return `Rejected: ${String(task.last_reject_reason ?? 'needs a fix')}`
  if (info.raw === 'failed') return `Failed: ${String((task.outcome as Record<string, unknown> | undefined)?.reason ?? 'executor failed')}`
  if (info.raw === 'stale') return 'No heartbeat. The executor may have died.'
  const unmet = (Array.isArray(task.depends_on) ? task.depends_on : []).filter(dep => tasks[String(dep)]?.state !== 'accepted')
  if (unmet.length) return `Waiting on ${unmet.join(', ')}`
  if (info.raw === 'fixing') return `Waiting on fix ${(Array.isArray(task.fixed_by) ? task.fixed_by : []).join(', ')}`
  if (info.section === 'Running') return `Running: ${String(task.last_step ?? 'executor active')}`
  if (info.raw === 'ready') return 'Ready, not picked up yet.'
  if (info.raw === 'released') return `Retrying: attempt ${String(task.attempt ?? 1)} of ${String(task.max_attempts ?? '?')}.`
  return ''
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
    host = {
      cwd: e.cwd,
      pluginRoot: $.plugin.root,
      liveCwd: () => $.session.cwd(),
      // $.env.get names must be string literals (book rule 4), so one method per variable.
      envGetAleRunDir: () => $.env.get('ALE_RUN_DIR'),
      envGetAleRoster: () => $.env.get('ALE_ROSTER'),
      fsStat: (path, statOptions) => $.fs.stat(path, statOptions),
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
      processRun: (argv, processOptions) => $.process.run(argv, processOptions),
    }
    const engine = host
    isOpen = (await engine.storeGet(STORE_OPEN_KEY).catch(() => false)) === true
    selectedRun = undefined
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
    try {
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
    if (lower.startsWith('run ') || arg) {
      const runArg = lower.startsWith('run ') ? arg.slice('run '.length).trim() : arg
      const liveCwd = await engine.liveCwd().catch(() => engine.cwd)
      const cwd = await nearestRun(engine, liveCwd)
      const launch = await nearestRun(engine, engine.cwd)
      const resolved = resolveRunDirectory({ arg: runArg, cwdRunsDir: cwd.runsDir, launchRunsDir: launch.runsDir })
      if (!resolved || !await engine.fsExists(resolved.runDir).catch(() => false)) return { text: unknownRunArgumentMessage(runArg || arg) }
      selectedRun = { runDir: resolved.runDir, source: 'arg' }
      model = { kind: 'no-run' }
      setOpen(engine, true)
      await $.ui.open({ id: PANE_ID, title: 'ALE task board', closeOnEscape: true, holdToasts: true, rows: 18 }).catch(() => undefined)
      const after = await refresh(engine)
      return { text: after.kind === 'error' ? `run ${runArg}: ${after.message}` : `board on ${runArg}` }
    }
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
    } catch (err) {
      return { text: `ale-board error: ${err instanceof Error ? err.message : String(err)}` }
    }
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
          <Text bold>{truncateTo(`ale-board · ${model.runLabel} · source:${model.source}`, columns)}</Text>
          <Text>{truncateTo(`error: ${model.message}`, columns)}</Text>
        </Box>
      )
    }

    const lines = renderBoard({ runId: model.runLabel, tasks: model.status.tasks, run: model.status.run, labels: model.labels, boardUrl: typeof (model.status.run as any).board_url === 'string' ? (model.status.run as any).board_url : undefined, nowS: Date.now() / 1000, columns })

    return (
      <Box flexDirection="column">
        {lines.map((line, index) => (
          <Text key={`board-line-${index}`}>{line.map((segment, part) => <Text key={`segment-${part}`} color={segment.color} bold={segment.bold} dimColor={segment.dimColor}>{segment.text}</Text>)}</Text>
        ))}
      </Box>
    )
  })
}
