import { describe, expect, test } from 'bun:test'
import { attentionOrder, boardUrl, createRefreshErrorLogger, displayState, formatTokens, bundledAleArgv, STATE_DISPLAY } from './lib.ts'

describe('board v2 display contract', () => {
  test('uses only literal hook API access and never passes the hook context as a value', async () => {
    const source = await Bun.file(`${import.meta.dir}/register.tsx`).text()
    expect(source).not.toMatch(/\$\[|\$\?\./)
    const withoutHookParameters = source.replace(
      /\bon\(\s*['"][^'"]+['"]\s*,(?:\s*\{[^}]*\}\s*,)?\s*(?:async\s*)?\(\s*\$[^)]*\)\s*=>/g,
      'on("event", hook =>',
    )
    expect(withoutHookParameters).not.toMatch(/[,(]\s*\$(?:\s*[,)]|\s*\])/)
  })

  test('status command runs the bundled ale', () => {
    const argv = bundledAleArgv('/plugin root', ['status', '--json', '--run-dir', '/run dir'])
    expect(argv[0]).toBe('python3')
    expect(argv[1]).toBe('-c')
    expect(argv[2]).toContain('sys.path.insert(0, sys.argv[1])')
    expect(argv[2]).toContain('from ale.cli import main')
    expect(argv[2]).toContain('main(sys.argv[2:])')
    expect(argv[3]).toBe('/plugin root')
    expect(argv.slice(4)).toEqual(['status', '--json', '--run-dir', '/run dir'])
    expect(argv).not.toContain('-m')
  })

  test('refresh error is logged once', () => {
    const logOnce = createRefreshErrorLogger()
    const logged: string[] = []
    const uiLog = (message: string) => logged.push(message)
    const refreshError = 'ale status failed (exit 1): No module named ale'

    logOnce(uiLog, refreshError)
    logOnce(uiLog, refreshError)

    expect(logged).toEqual([refreshError])
  })

  test('carries every design-authority state label', () => {
    const labels = STATE_DISPLAY.map(row => row.label)
    expect(labels).toEqual(expect.arrayContaining([
      'Needs your answer', 'Failed to start', 'Out of attempts', 'Rejected', 'Failed',
      'No heartbeat', 'Running', 'Verifying', 'Waiting on fix', 'Ready', 'Retrying',
      'Waiting', 'Done', 'Canceled',
    ]))
  })

  test('uses text-plus-glyph attention ordering without hiding ordinary work', () => {
    expect(attentionOrder({ normal: { state: 'ready' }, failed: { state: 'rejected' }, stale: { state: 'stale' } }))
      .toEqual(['failed', 'stale'])
    expect(displayState({ state: 'working', lease_expires_ts: 20 }, 21).label).toBe('No heartbeat')
    expect(displayState({ state: 'accepted' }).label).toBe('Done')
  })

  test('board URL requires a live loopback URL and valid metadata', () => {
    expect(boardUrl({ pid: 12, url: 'http://127.0.0.1:43127/a9f/', instance_id: 'i1', updated_ts: 100 }, 12, 100))
      .toBe('http://127.0.0.1:43127/a9f/')
    expect(boardUrl({ pid: 12, url: 'http://192.168.1.2:43127/a9f/', instance_id: 'i1', updated_ts: 100 }, 12, 100)).toBeUndefined()
    expect(boardUrl({ pid: 12, url: 'http://127.0.0.1:43127/a9f/', instance_id: 'i1', updated_ts: 1 }, 12, 40)).toBeUndefined()
  })

  test('uses the design number format', () => {
    expect(formatTokens(2402922)).toBe('2.4M')
    expect(formatTokens(540044)).toBe('540k')
  })
})
