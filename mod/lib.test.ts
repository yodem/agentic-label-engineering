import { describe, expect, test } from 'bun:test'
import {
  GROUP_ORDER,
  bandLine,
  budgetRows,
  formatTaskRow,
  groupTaskIds,
  reduceEvents,
  resolveRunDirectory,
  runDirFromCurrent,
  unknownRunArgumentMessage,
  noRunMessage,
  parseStatusOutput,
  pickLatestRunDir,
  truncateTo,
  type RawTask,
} from './lib.ts'

const fixtureDir = `${import.meta.dir}/fixtures/protocol/basic`

describe('resolveRunDirectory', () => {
  test('resolves ALE_RUN_DIR first', () => {
    expect(resolveRunDirectory({ envDir: '/env/run', cwdRunDir: '/cwd/run', launchRunDir: '/launch/run' }))
      .toEqual({ runDir: '/env/run', source: 'env' })
  })
  test('resolves the live cwd before the launch directory', () => {
    expect(resolveRunDirectory({ cwdRunDir: '/cwd/run', launchRunDir: '/launch/run' }))
      .toEqual({ runDir: '/cwd/run', source: 'cwd' })
  })
  test('falls back to the launch directory', () => {
    expect(resolveRunDirectory({ launchRunDir: '/launch/run' }))
      .toEqual({ runDir: '/launch/run', source: 'launch' })
  })
  test('accepts an absolute run directory argument', () => {
    expect(resolveRunDirectory({ arg: '/chosen/run', envDir: '/env/run' }))
      .toEqual({ runDir: '/chosen/run', source: 'arg' })
  })
  test('resolves a run id under the nearest runs directory', () => {
    expect(resolveRunDirectory({ arg: 'run-7', cwdRunsDir: '/repo/.ale/runs', launchRunsDir: '/old/.ale/runs' }))
      .toEqual({ runDir: '/repo/.ale/runs/run-7', source: 'arg' })
  })
  test('formats an unknown argument with what was tried', () => {
    expect(unknownRunArgumentMessage('missing')).toContain('"missing"')
    expect(unknownRunArgumentMessage('missing')).toContain('unknown argument')
  })
})

describe('runDirFromCurrent', () => {
  test('resolves a text file containing a run id', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'file', text: 'run-7' })).toBe('/repo/.ale/runs/run-7')
  })
  test('trims a trailing newline from a run id', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'file', text: 'run-7\n' })).toBe('/repo/.ale/runs/run-7')
  })
  test('accepts an absolute path from a text file', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'file', text: '/tmp/run-7\n' })).toBe('/tmp/run-7')
  })
  test('rejects an empty file', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'file', text: ' \n' })).toBeUndefined()
  })
  test('rejects relative file text containing a slash', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'file', text: 'nested/run-7' })).toBeUndefined()
  })
  test('uses a directory symlink target when realPath is available', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'dir', realPath: '/repo/.ale/runs/run-7' })).toBe('/repo/.ale/runs/run-7')
  })
  test('uses current when a directory has no realPath', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'dir' })).toBe('/repo/.ale/runs/current')
  })
  test('rejects other filesystem entries', () => {
    expect(runDirFromCurrent({ runsDir: '/repo/.ale/runs', kind: 'other' })).toBeUndefined()
  })
})

describe('register source shape', () => {
  test('imports called lib helpers and avoids Node process globals', async () => {
    const source = await Bun.file(`${import.meta.dir}/register.tsx`).text()
    expect(source).not.toContain('process.')
    const importBlock = source.match(/import\s*\{([\s\S]*?)\}\s*from\s*'\.\/lib\.ts'/)?.[1] ?? ''
    for (const identifier of ['resolveRunDirectory', 'unknownRunArgumentMessage', 'runDirFromCurrent']) {
      expect(importBlock).toContain(identifier)
    }
    expect(source).toContain("on('command.run', { command: COMMAND }")
    expect(source).toContain("const COMMAND = 'ale-board'")
  })
})

const basicEvents = (await Bun.file(`${fixtureDir}/events.jsonl`).text()).trim().split('\n').map(line => JSON.parse(line))
const basicStatus = await Bun.file(`${fixtureDir}/status.json`).json() as { run: Record<string, unknown>; tasks: Record<string, Record<string, unknown>> }
const basicLabels: Record<string, Record<string, any>> = {}
for (const file of new Bun.Glob('*.json').scanSync(`${fixtureDir}/labels`)) {
  const label = await Bun.file(`${fixtureDir}/labels/${file}`).json() as Record<string, any>
  basicLabels[label.task_id] = label
}

describe('parseStatusOutput', () => {
  test('a non-zero exit yields an error, not a throw', () => {
    const result = parseStatusOutput(1, '', 'Traceback...\nusage: ale status [--json]\n')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('exit 1')
  })

  test('malformed JSON on a zero exit yields an error, not a throw', () => {
    const result = parseStatusOutput(0, '{not json', '')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('malformed JSON')
  })

  test('a JSON value that is not an object yields an error', () => {
    const result = parseStatusOutput(0, '[1,2,3]', '')
    expect(result.ok).toBe(false)
  })

  test('missing "tasks" yields an error', () => {
    const result = parseStatusOutput(0, JSON.stringify({ run: {} }), '')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('tasks')
  })

  test('a well-formed status parses to tasks and run', () => {
    const stdout = JSON.stringify({
      run: { tokens: 10, cost_usd: 0.5, breaches_seen: [] },
      tasks: { T1: { state: 'working' } },
    })
    const result = parseStatusOutput(0, stdout, '')
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.status.tasks.T1?.state).toBe('working')
      expect(result.status.run.tokens).toBe(10)
    }
  })
})

describe('groupTaskIds', () => {
  test('groups in board column order: input-required, working+claimed, submitted, ready, attention, planned, accepted, failed, canceled', () => {
    const tasks: Record<string, RawTask> = {
      A: { state: 'accepted' },
      B: { state: 'working' },
      C: { state: 'input-required' },
      D: { state: 'claimed' },
      E: { state: 'submitted' },
      F: { state: 'ready' },
      G: { state: 'rejected' },
      H: { state: 'stale' },
      I: { state: 'released' },
      J: { state: 'planned' },
      K: { state: 'failed' },
      L: { state: 'canceled' },
    }
    const groups = groupTaskIds(tasks)
    expect(groups.map(g => g.name)).toEqual([...GROUP_ORDER])
    const byName = Object.fromEntries(groups.map(g => [g.name, g.ids]))
    expect(byName['input-required']).toEqual(['C'])
    expect(byName.working).toEqual(['B', 'D'])
    expect(byName.submitted).toEqual(['E'])
    expect(byName.ready).toEqual(['F'])
    expect(byName.attention).toEqual(['G', 'H', 'I'])
    expect(byName.planned).toEqual(['J'])
    expect(byName.accepted).toEqual(['A'])
    expect(byName.failed).toEqual(['K'])
    expect(byName.canceled).toEqual(['L'])
  })

  test('an unknown state is dropped rather than thrown on', () => {
    const groups = groupTaskIds({ X: { state: 'not-a-real-state' } })
    expect(groups.every(g => g.ids.length === 0)).toBe(true)
  })

  test('ids within a bucket are sorted', () => {
    const groups = groupTaskIds({ T9: { state: 'working' }, T2: { state: 'working' } })
    const working = groups.find(g => g.name === 'working')
    expect(working?.ids).toEqual(['T2', 'T9'])
  })
})

describe('reduceEvents', () => {
  test('matches Python status for every task in the real CLI fixture', () => {
    const reduced = reduceEvents(basicEvents, basicLabels)
    for (const [id, expected] of Object.entries(basicStatus.tasks)) {
      const actual = reduced.tasks[id]
      expect(actual).toBeDefined()
      expect(actual?.state).toBe(expected.state)
      expect(actual?.attempt).toBe(expected.attempt)
      expect(actual?.owner).toBe(expected.owner)
      expect(actual?.integrated).toBe(expected.integrated)
      expect((actual?.last_verdict as Record<string, unknown> | null)?.verdict ?? null).toBe((expected.last_verdict as Record<string, unknown> | null)?.verdict ?? null)
      const latestSpawn = basicEvents.filter(event => event.type === 'spawned' && event.task_id === id).at(-1)
      const expectedWorktree = typeof latestSpawn?.worktree === 'string' ? latestSpawn.worktree.split('/').filter(Boolean).at(-1) : null
      const actualWorktree = typeof actual?.worktree === 'string' ? actual.worktree.split('/').filter(Boolean).at(-1) : null
      expect(actualWorktree).toBe(expectedWorktree)
    }
  })

  test('starts labeled tasks as planned and derives ready when claimable', () => {
    const result = reduceEvents([], { T1: { context: { depends_on: [] } } })
    expect(result.tasks.T1?.state).toBe('ready')
    expect(result.tasks.T1?.claimable).toBe(true)
  })

  test('claims only with matching attempt and records owner', () => {
    const result = reduceEvents([{ type: 'claimed', task_id: 'T1', agent_id: 'a', attempt: 1, ts: 1 }], { T1: { context: { depends_on: [] } } })
    expect(result.tasks.T1?.state).toBe('claimed')
    expect(result.tasks.T1?.owner).toBe('a')
  })

  test('ignores executor heartbeats from a non-owner', () => {
    const events = [
      { type: 'claimed', task_id: 'T1', agent_id: 'a', attempt: 1, ts: 1 },
      { type: 'heartbeat', task_id: 'T1', agent_id: 'b', step: 'wrong', ts: 2 },
    ]
    const result = reduceEvents(events, { T1: { context: { depends_on: [] } } })
    expect(result.tasks.T1?.last_step).toBe(null)
    expect(result.tasks.T1?.owner).toBe('a')
  })

  test('ignores authority events carrying an agent id', () => {
    const events = [{ type: 'integrated', task_id: 'T1', agent_id: 'a', ts: 1 }]
    expect(reduceEvents(events, { T1: { context: { depends_on: [] } } }).tasks.T1?.integrated).toBe(false)
  })

  test('adds spawned assignees and monitor verdicts only from authority', () => {
    const events = [
      { type: 'spawned', task_id: 'T1', agent_id: null, agent_id_minted: 'worker', ts: 1 },
      { type: 'monitor_verdict', task_id: 'T1', agent_id: null, agent_id_minted: 'monitor', verdict: 'nudge', text: 'continue', ts: 2 },
    ]
    const task = reduceEvents(events, { T1: { context: { depends_on: [] } } }).tasks.T1
    expect(task?.assignees).toEqual(['worker'])
    expect((task?.last_verdict as Record<string, unknown>).verdict).toBe('nudge')
  })

  test('sums usage for task and run totals', () => {
    const events = [{ type: 'usage', task_id: 'T1', agent_id: 'worker', 'gen_ai.usage.input_tokens': 15, 'gen_ai.usage.output_tokens': 6, ts: 1 }]
    const result = reduceEvents(events, { T1: { context: { depends_on: [] } } })
    expect(result.tasks.T1?.tokens).toBe(21)
    expect(result.run.tokens).toBe(21)
  })

  test('removes an open task and retains added task labels', () => {
    const events = [
      { type: 'task_added', task_id: 'T2', agent_id: null, label_file: 'T2.json', ts: 1 },
      { type: 'label_removed', task_id: 'T1', agent_id: null, ts: 2 },
    ]
    const result = reduceEvents(events, { T1: { context: { depends_on: [] } }, T2: { context: { depends_on: [] } } })
    expect(result.tasks.T1).toBeUndefined()
    expect(result.tasks.T2?.state).toBe('ready')
  })

  test('records integration, breach and latest worktree', () => {
    const events = [
      { type: 'spawned', task_id: 'T1', agent_id: null, worktree: '/runs/r/wt/T1', ts: 1 },
      { type: 'integrated', task_id: 'T1', agent_id: null, ts: 2 },
      { type: 'breach', task_id: 'T1', agent_id: null, breach: 'stuck', ts: 3 },
    ]
    const task = reduceEvents(events, { T1: { context: { depends_on: [] } } }).tasks.T1
    expect(task?.integrated).toBe(true)
    expect(task?.worktree).toBe('/runs/r/wt/T1')
    expect(task?.breaches_seen).toEqual([['stuck', 1]])
  })
})

describe('truncateTo', () => {
  test('leaves a short string untouched', () => {
    expect(truncateTo('hello', 10)).toBe('hello')
  })

  test('cuts to width with a trailing ellipsis', () => {
    expect(truncateTo('a much longer title than fits', 10)).toBe('a much lo…')
    expect(truncateTo('a much longer title than fits', 10).length).toBe(10)
  })

  test('width 0 is empty, width 1 has no room for an ellipsis', () => {
    expect(truncateTo('hello', 0)).toBe('')
    expect(truncateTo('hello', 1)).toBe('h')
  })
})

describe('budgetRows', () => {
  test('fits as-is when under the cap', () => {
    const sections = [{ name: 'working', rows: ['r1', 'r2'] }]
    expect(budgetRows(sections, 10)).toEqual(sections)
  })

  test('collapses accepted before planned when both are large', () => {
    const sections = [
      { name: 'planned', rows: Array.from({ length: 5 }, (_, i) => `p${i}`) },
      { name: 'accepted', rows: Array.from({ length: 5 }, (_, i) => `a${i}`) },
    ]
    // total = (1+5) + (1+5) = 12; cap 8 forces at least one collapse
    const out = budgetRows(sections, 8)
    const accepted = out.find(s => s.name === 'accepted')
    const planned = out.find(s => s.name === 'planned')
    expect(accepted?.rows).toEqual(['5 more'])
    expect(planned?.rows).toEqual(sections[0]?.rows)
  })

  test('collapses planned too when accepted alone is not enough', () => {
    const sections = [
      { name: 'planned', rows: Array.from({ length: 20 }, (_, i) => `p${i}`) },
      { name: 'accepted', rows: Array.from({ length: 20 }, (_, i) => `a${i}`) },
    ]
    const out = budgetRows(sections, 5)
    const accepted = out.find(s => s.name === 'accepted')
    const planned = out.find(s => s.name === 'planned')
    expect(accepted?.rows).toEqual(['20 more'])
    expect(planned?.rows).toEqual(['20 more'])
  })
})

describe('formatTaskRow', () => {
  test('truncates the whole row to the column budget', () => {
    const row = formatTaskRow(
      'T8',
      { state: 'working', owner: 'sonnet-t8', attempt: 1, tokens: 1234, last_heartbeat_ts: 1000, last_step: 'a very long step description that will not fit' },
      { title: 'ale-board Claude Mod: task board pane and band', role: 'frontend', tier: 'standard' },
      1005,
      24,
    )
    expect(row.text.length).toBeLessThanOrEqual(24)
    expect(row.id).toBe('T8')
  })

  test('marks a task with breaches', () => {
    const row = formatTaskRow('T1', { state: 'working', breaches_seen: [['stuck', 1]] }, undefined, 100, 80)
    expect(row.breached).toBe(true)
    expect(row.text).toContain('!')
  })
})

describe('noRunMessage', () => {
  test('names the two commands that create a run', () => {
    expect(noRunMessage()).toContain('init-run')
    expect(noRunMessage()).toContain('status --json')
  })
})

describe('bandLine', () => {
  test('matches the fixture text', () => {
    const tasks: Record<string, RawTask> = {
      T1: { state: 'working', breaches_seen: [] },
      T2: { state: 'claimed', breaches_seen: [] },
      T3: { state: 'input-required', breaches_seen: [] },
      T4: { state: 'accepted', breaches_seen: [] },
      T5: { state: 'accepted', breaches_seen: [] },
      T6: { state: 'accepted', breaches_seen: [] },
    }
    const line = bandLine('example-run', tasks, { breaches_seen: [] })
    expect(line).toBe('ale example-run · 1 working · 1 claimed · 1 input-required · 3 accepted · 0 tokens · 0 breaches')
  })

  test('counts breaches from both the run and its tasks', () => {
    const tasks: Record<string, RawTask> = { T1: { state: 'working', breaches_seen: ['x'] } }
    const line = bandLine('r', tasks, { breaches_seen: ['y', 'z'] })
    expect(line).toContain('3 breaches')
  })
})

describe('pickLatestRunDir', () => {
  test('picks the most recently modified entry', () => {
    const picked = pickLatestRunDir([
      { name: 'old-run', mtimeMs: 100 },
      { name: 'new-run', mtimeMs: 300 },
      { name: 'mid-run', mtimeMs: 200 },
    ])
    expect(picked).toBe('new-run')
  })

  test('no entries yields undefined', () => {
    expect(pickLatestRunDir([])).toBeUndefined()
  })
})
