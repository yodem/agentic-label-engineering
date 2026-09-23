import { describe, expect, test } from 'bun:test'
import { parseStatusOutput, renderBoard } from './lib'

const nowS = 1_758_542_400
const tasks: Record<string, any> = {
  T20: { state: 'input-required', waiting_on: 'Acceptance failed: A1 FAIL exit=1 A2 FAIL exit=1', tokens: 96000 },
  T21: { state: 'planned', depends_on: ['T20'], tokens: 0 },
}
const labels: Record<string, any> = {
  T20: { title: 'Remove legacy session store (needs review)' },
  T21: { title: 'Version 0.4.0, CHANGELOG entry, update release notes' },
}
for (let n = 1; n <= 14; n++) {
  const id = `T${15 - n}`
  tasks[id] = { state: 'accepted', tokens: n === 1 ? 8100 : n === 2 ? 9800 : n === 3 ? 11000 : n * 1000, submitted_ts: nowS - (n === 1 ? 7080 : n === 2 ? 8400 : n === 3 ? 10260 : 3600 * n) }
  labels[id] = { title: n === 1 ? 'Add token refresh endpoint' : n === 2 ? 'Write auth integration tests' : n === 3 ? 'Document the session API' : `Task ${id} completed` }
}
const REALRUN = {
  runId: '2026-09-22-auth-refresh-demo', tasks,
  run: { tokens: 1_200_000, cost_usd: 0 }, labels, nowS,
}
const fixture = REALRUN
const width = (line: { text: string }[]) => [...line.map(s => s.text).join('')].length
const plain = (columns: number) => renderBoard({ ...fixture, nowS: fixture.nowS, columns })
const joined = (columns: number) => plain(columns).map(l => l.map(s => s.text).join(''))

describe('renderBoard', () => {
  for (const columns of [60, 80, 120]) {
    test(`no line wider than ${columns} columns`, () => {
      for (const line of plain(columns)) expect(width(line)).toBeLessThanOrEqual(columns)
    })
  }
  test('glyph, id and state word start at the same column on every task row at 80', () => {
    const rows = joined(80).filter(l => /^[✓●?·■→◆×] T\d/.test(l))
    expect(rows.length).toBeGreaterThan(3)
    for (const row of rows) expect(row.slice(11, 29).trim().length).toBeGreaterThan(0)
  })
  test('needs-you reason is never truncated', () => {
    expect(joined(80).join('\n')).toContain('Acceptance failed: A1 FAIL exit=1 A2 FAIL exit=1')
  })
  test('unknown state renders with its prefix', () => {
    const tasks = { ...fixture.tasks, T99: { state: 'paused' } }
    expect(renderBoard({ ...fixture, tasks, columns: 80 }).map(l => l.map(s => s.text).join('')).join('\n')).toContain('Unknown: paused')
  })
  test('headline grammar for one', () => {
    expect(joined(80).join('\n')).toContain('1 needs you; 1 is waiting')
  })
  test('board URL is the last line', () => {
    const lines = renderBoard({ ...fixture, boardUrl: 'http://127.0.0.1:1/t/', columns: 80 })
    expect(lines.at(-1)!.map(s => s.text).join('')).toStartWith('board: http://127.0.0.1:1/t/')
  })
  test('shows the web command in the header when no validated board URL exists', () => {
    const header = joined(80)[0]
    expect(header).toContain('web: /ale:board')
  })
  test('shows last event age and copied run directory in header', () => {
    const lines = renderBoard({ ...fixture, tasks: { T1: { state: 'ready' } }, labels: {}, run: { ...fixture.run, last_event_ts: fixture.nowS - 3 * 3600 }, runPath: '/tmp/copied-run', columns: 100 })
    const header = lines.slice(0, 3).map(l => l.map(s => s.text).join('')).join('\n')
    expect(header).toContain('Last event 3h ago')
    expect(header).toContain('/tmp/copied-run')
    expect(header).toContain('Idle, last activity 3h ago')
  })
  test('shows other runs on one truncated line at 80 and 120 columns', () => {
    const otherRuns = Array.from({ length: 3 }, (_, i) => ({ run_id: `run-${i}-${'x'.repeat(35)}`, dir: `d${i}`, path: `/runs/d${i}`, last_event_ts: nowS - i * 60 }))
    for (const columns of [80, 120]) {
      const lines = renderBoard({ ...fixture, otherRuns, columns })
      const text = lines.map(line => line.map(s => s.text).join(''))
      const other = text.find(line => line.startsWith('Other runs:'))
      expect(other).toBeDefined()
      expect([...other!].length).toBeLessThanOrEqual(columns)
    }
  })
})

// Captured real `ale status --json` output for the run that exposed the bug.
const REAL_STATUS = {
  "run": {
    "breaches_seen": [],
    "cost_usd": 0.0,
    "finished": false,
    "started_ts": 1790073243.3072078,
    "tokens": 1355909
  },
  "tasks": {
    "T10": {
      "agent": "agent: unknown",
      "assignees": [
        "T10-executor-backend-1"
      ],
      "attempt": 3,
      "blocked_by": [],
      "breaches": [
        "lease_expired",
        "overrun",
        "lease_expired"
      ],
      "breaches_seen": [
        [
          "lease_expired",
          1
        ],
        [
          "overrun",
          2
        ],
        [
          "lease_expired",
          2
        ]
      ],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790097004.184146,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790097604.184146,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790096883.7782888,
      "state": "accepted",
      "step_changed_ts": 1790096883.7782888,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790097025.993244,
      "summary": "ale-exec completed",
      "tokens": 57054,
      "waiting_on": null
    },
    "T11": {
      "agent": "agent: unknown",
      "assignees": [
        "T11-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [
          "A3"
        ],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          }
        ],
        "signoff": "lead: verified"
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790097273.08273,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790097873.08273,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790097032.602946,
      "state": "accepted",
      "step_changed_ts": 1790097032.760268,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790097362.411932,
      "summary": "ale-exec completed",
      "tokens": 93727,
      "waiting_on": null
    },
    "T12": {
      "agent": "agent: unknown",
      "assignees": [
        "T12-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790098379.5484312,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790098979.5484312,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790098259.2170901,
      "state": "accepted",
      "step_changed_ts": 1790098259.4051352,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790098499.324834,
      "summary": "ale-exec completed",
      "tokens": 54024,
      "waiting_on": null
    },
    "T13": {
      "agent": "agent: unknown",
      "assignees": [
        "T13-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790098638.145529,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790099238.145529,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790098517.9378839,
      "state": "accepted",
      "step_changed_ts": 1790098518.008838,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790098709.647713,
      "summary": "ale-exec completed",
      "tokens": 62037,
      "waiting_on": null
    },
    "T16": {
      "agent": "agent: unknown",
      "assignees": [
        "T16-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [
          "A2"
        ],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ],
        "signoff": "lead: verified"
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790099325.504384,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790099925.504384,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790098724.852413,
      "state": "accepted",
      "step_changed_ts": 1790098724.924452,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790099428.456724,
      "summary": "ale-exec completed",
      "tokens": 60466,
      "waiting_on": null
    },
    "T20": {
      "agent": "agent: unknown",
      "assignees": [
        "T20-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [
        "over_budget"
      ],
      "breaches_seen": [
        [
          "over_budget",
          1
        ]
      ],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ],
        "signoff": "lead: verified"
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790102152.275569,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790102752.275569,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790102032.072375,
      "state": "accepted",
      "step_changed_ts": 1790102032.072375,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790102224.762655,
      "summary": "ale-exec completed",
      "tokens": 174675,
      "waiting_on": null
    },
    "T21": {
      "agent": "agent: unknown",
      "assignees": [
        "T21-executor-backend-1",
        "lead-orchestrator"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [
          "A2"
        ],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ],
        "signoff": "lead: verified"
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790102553.785577,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790103153.785577,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790102553.785577,
      "state": "accepted",
      "step_changed_ts": 1790102553.785577,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790102553.963341,
      "summary": "lead submitted after review",
      "tokens": 74125,
      "waiting_on": null
    },
    "T3": {
      "agent": "agent: unknown",
      "assignees": [
        "T3-executor-test-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790073363.9157681,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790073963.9157681,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790073243.659279,
      "state": "accepted",
      "step_changed_ts": 1790073243.792429,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790073444.700028,
      "summary": "ale-exec completed",
      "tokens": 65627,
      "waiting_on": null
    },
    "T4": {
      "agent": "agent: unknown",
      "assignees": [
        "T4-executor-backend-1"
      ],
      "attempt": 5,
      "blocked_by": [],
      "breaches": [
        "over_budget",
        "over_budget"
      ],
      "breaches_seen": [
        [
          "over_budget",
          1
        ],
        [
          "over_budget",
          2
        ]
      ],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [
        "T4.fix1",
        "T4.fix2"
      ],
      "integrated": true,
      "last_heartbeat_ts": 1790074179.431175,
      "last_reject_reason": "acceptance failed: A2",
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790074779.431175,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 2,
      "resumable": false,
      "started_ts": 1790073458.349152,
      "state": "accepted",
      "step_changed_ts": 1790073458.488565,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790075978.748873,
      "summary": "lead submitted after review",
      "tokens": 155498,
      "waiting_on": null
    },
    "T4.fix1": {
      "agent": "agent: unknown",
      "assignees": [
        "T4.fix1-executor-fixer-1"
      ],
      "attempt": 2,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": true,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": false,
        "results": [
          {
            "exit": 1,
            "id": "A2",
            "ok": false,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790074541.247823,
      "last_reject_reason": "acceptance failed: A2",
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790075141.247823,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 1,
      "resumable": false,
      "started_ts": 1790074300.72814,
      "state": "rejected",
      "step_changed_ts": 1790074300.927857,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790074549.507978,
      "summary": "ale-exec completed",
      "tokens": 39052,
      "waiting_on": null
    },
    "T4.fix2": {
      "agent": "agent: unknown",
      "assignees": [
        "T4.fix2-executor-fixer-1"
      ],
      "attempt": 2,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": true,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": false,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 1,
            "id": "A2",
            "ok": false,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790074670.9785368,
      "last_reject_reason": "acceptance failed: A2",
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790075270.9785368,
      "next_steps": [],
      "notes": [
        "lead hint: rerun the failing check with the roster set"
      ],
      "owner": null,
      "pending": [],
      "rejections": 1,
      "resumable": false,
      "started_ts": 1790074550.683962,
      "state": "rejected",
      "step_changed_ts": 1790074550.8173382,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790074721.754168,
      "summary": "ale-exec completed",
      "tokens": 68892,
      "waiting_on": null
    },
    "T5": {
      "agent": "agent: unknown",
      "assignees": [
        "T5-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A4",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790082168.430602,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790082768.430602,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790081927.9898088,
      "state": "accepted",
      "step_changed_ts": 1790081927.9898088,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790082258.842156,
      "summary": "ale-exec completed",
      "tokens": 149297,
      "waiting_on": null
    },
    "T6": {
      "agent": "agent: unknown",
      "assignees": [
        "T6-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790082398.296038,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790082998.296038,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790082277.9469628,
      "state": "accepted",
      "step_changed_ts": 1790082278.126019,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790082455.8132071,
      "summary": "ale-exec completed",
      "tokens": 75364,
      "waiting_on": null
    },
    "T7": {
      "agent": "agent: unknown",
      "assignees": [
        "T7-executor-backend-1"
      ],
      "attempt": 2,
      "blocked_by": [],
      "breaches": [
        "stuck",
        "lease_expired"
      ],
      "breaches_seen": [
        [
          "stuck",
          1
        ],
        [
          "lease_expired",
          1
        ]
      ],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790091824.073741,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790092424.073741,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790091823.8552802,
      "state": "accepted",
      "step_changed_ts": 1790091823.8552802,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790091844.6801121,
      "summary": "ale-exec completed",
      "tokens": 0,
      "waiting_on": null
    },
    "T8": {
      "agent": "agent: unknown",
      "assignees": [
        "T8-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790092117.888402,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790092717.888402,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790091877.436676,
      "state": "accepted",
      "step_changed_ts": 1790091877.576756,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790092188.1257012,
      "summary": "ale-exec completed",
      "tokens": 107257,
      "waiting_on": null
    },
    "T9": {
      "agent": "agent: unknown",
      "assignees": [
        "T9-executor-backend-1"
      ],
      "attempt": 1,
      "blocked_by": [],
      "breaches": [],
      "breaches_seen": [],
      "claimable": false,
      "cost_usd": 0.0,
      "evidence": {
        "files": [],
        "manual": [],
        "passed": true,
        "required": [],
        "required_failures": [],
        "results": [
          {
            "exit": 0,
            "id": "A1",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A2",
            "ok": true,
            "tail": ""
          },
          {
            "exit": 0,
            "id": "A3",
            "ok": true,
            "tail": ""
          }
        ]
      },
      "files_modified": [],
      "fixed_by": [],
      "fixes": [],
      "integrated": true,
      "last_heartbeat_ts": 1790092445.8648808,
      "last_reject_reason": null,
      "last_step": "process alive",
      "last_verdict": null,
      "lease_expires_ts": 1790093045.8648808,
      "next_steps": [],
      "notes": [],
      "owner": null,
      "pending": [],
      "rejections": 0,
      "resumable": false,
      "started_ts": 1790092205.384094,
      "state": "accepted",
      "step_changed_ts": 1790092205.524905,
      "steps": [
        "process alive"
      ],
      "submitted_ts": 1790092541.332588,
      "summary": "ale-exec completed",
      "tokens": 118814,
      "waiting_on": null
    }
  }
} as const

describe('real ALE status regression', () => {
  test('renders accepted totals and superseded rejected fixes from status', () => {
    const parsed = parseStatusOutput(0, JSON.stringify(REAL_STATUS), '')
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) throw new Error(parsed.error)
    const lines = renderBoard({
      runId: '2026-09-22-board-redesign',
      tasks: parsed.status.tasks,
      run: parsed.status.run,
      labels: {},
      nowS: 1_790_103_000,
      columns: 180,
    }).map(line => line.map(segment => segment.text).join('')).join('\n')

    expect(lines).toContain('14 of 16 done')
    expect(lines).toContain('Needs you 0')
    expect(lines).toContain('T4.fix1')
    expect(lines).toContain('T4.fix2')
    expect(lines.match(/→ T4\.fix[12].*Superseded/g)).toHaveLength(2)
    expect(lines).toContain('Superseded: parent T4 accepted')
    expect(lines).not.toContain('16M')
  })
})
