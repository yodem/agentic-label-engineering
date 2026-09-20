import { describe, expect, test } from 'bun:test'
import {
  GROUP_ORDER,
  bandLine,
  budgetRows,
  formatTaskRow,
  groupTaskIds,
  noRunMessage,
  parseStatusOutput,
  pickLatestRunDir,
  truncateTo,
  type RawTask,
} from './lib.ts'

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
    expect(line).toBe('ale example-run · 2 working · 1 input-required · 3 accepted · 0 breaches')
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
