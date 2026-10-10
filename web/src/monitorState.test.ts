import { describe, expect, it } from 'vitest'
import { acceptsCallUpdates, captureLabel, controlView, currentCall, emptyFeedView, initialTracking, latestStatus, updateTracking } from './monitorState'
import type { RadioCall, RadioStatus } from './data'

const call = (id: string, started_at: number) => ({ id, started_at } as RadioCall)

describe('monitor tracking', () => {
  it('uses the initial snapshot as a baseline and counts only new IDs while paused', () => {
    const first = call('first', 10)
    const baseline = updateTracking(initialTracking, [first], false, true)
    expect(baseline.unreadIds).toEqual([])
    const paused = updateTracking(baseline, [first, call('new', 11)], false, true)
    expect(paused.unreadIds).toEqual(['new'])
    expect(updateTracking(paused, [{ ...first }, call('new', 11)], false, true).unreadIds).toEqual(['new'])
    expect(updateTracking(paused, [call('older-history', 1), first, call('new', 11)], false, true).unreadIds).toEqual(['new'])
  })
  it('clears notifications on resume and removes discarded IDs from the count', () => {
    const baseline = updateTracking(initialTracking, [call('first', 10)], true, true)
    const paused = updateTracking(baseline, [call('first', 10), call('new', 11)], false, true)
    expect(updateTracking(paused, [call('first', 10)], false, true).unreadIds).toEqual([])
    expect(updateTracking(paused, [call('first', 10), call('new', 11)], true, true).unreadIds).toEqual([])
  })
  it('uses the current slot source rather than an older unfinished recording for active identity', () => {
    const older = { ...call('old', 10), status: 'recording', source_id: 1 } as RadioCall
    const active = { ...call('current', 11), status: 'recording', source_id: 2 } as RadioCall
    expect(currentCall([older, active], { slot: 1, tg: 91, active: true, source_id: 2 })).toBe(active)
    expect(currentCall([older], { slot: 1, tg: 91, active: true, source_id: 2 })).toBeUndefined()
  })
})

describe('operator control states', () => {
  it('keeps the stopped control response when older GET or SSE status arrives afterward', () => {
    const stopped = { listening_state: 'stopped', status_version: 200 } as RadioStatus
    const delayedRunning = { listening_state: 'running', status_version: 199 } as RadioStatus
    expect(latestStatus(stopped, delayedRunning)).toBe(stopped)
    expect(latestStatus(stopped, { ...delayedRunning, status_version: 200 })).toBe(stopped)
    expect(latestStatus(stopped, { listening_state: 'running' } as RadioStatus)).toBe(stopped)
    const newer = { ...delayedRunning, status_version: 201 }
    expect(latestStatus(stopped, newer)).toBe(newer)
  })
  it('ignores buffered call events after stopping while retaining legacy status compatibility', () => {
    const legacyRunning = { capture_state: 'online' } as RadioStatus
    const stopped = { listening_state: 'stopped', status_version: 300 } as RadioStatus
    expect(latestStatus(null, legacyRunning)).toBe(legacyRunning)
    expect(latestStatus(legacyRunning, stopped)).toBe(stopped)
    expect(acceptsCallUpdates(legacyRunning)).toBe(true)
    expect(acceptsCallUpdates(stopped)).toBe(false)
    expect(acceptsCallUpdates({ ...stopped, listening_state: 'stopping' })).toBe(false)
    expect(acceptsCallUpdates({ ...stopped, listening_state: 'starting' })).toBe(true)
    expect(acceptsCallUpdates(null)).toBe(false)
  })
  it('distinguishes delayed history loading from a stopped receiver and a failed synchronization', () => {
    const stopped = { ready: true, loading: false, stream: 'connected' as const, listening: 'stopped' as const, capture: 'offline' as const, filtered: false }
    expect(emptyFeedView({ ...stopped, ready: false, loading: true }).title).toBe('正在同步日志')
    expect(emptyFeedView({ ...stopped, ready: false }).title).toBe('日志同步失败')
    expect(emptyFeedView(stopped).title).toBe('监听已停止')
    expect(emptyFeedView({ ...stopped, stream: 'reconnecting' }).title).toBe('链路断开')
  })
  it('requires explicit start from stopped and disables both transitions', () => {
    expect(controlView('stopped')).toEqual({ action: 'start', disabled: false, label: '开始监听' })
    expect(controlView('running')).toEqual({ action: 'stop', disabled: false, label: '停止监听' })
    expect(controlView('starting').disabled).toBe(true)
    expect(controlView('stopping').disabled).toBe(true)
    expect(controlView('error')).toEqual({ action: 'stop', disabled: false, label: '停止监听' })
    expect(controlView('running', true).disabled).toBe(true)
  })
  it('never reports an active receiver as online while live updates are disconnected', () => {
    const status = { capture_state: 'online', listening_state: 'running' } as RadioStatus
    expect(captureLabel(status, 'connected')).toBe('接收在线')
    expect(captureLabel(status, 'reconnecting')).toBe('更新中断')
    expect(captureLabel({ ...status, listening_state: 'stopped' }, 'connected')).toBe('监听已停止')
  })
})
