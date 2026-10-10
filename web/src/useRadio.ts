import { useCallback, useEffect, useRef, useState } from 'react'
import { mergeCalls, parseCall, parseStatus, reconcileCalls } from './data'
import type { RadioCall, RadioStatus } from './data'
import { acceptsCallUpdates, latestStatus } from './monitorState'
import type { StreamState } from './monitorState'

async function getJson(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(path, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`服务暂不可用（${response.status}），正在等待重新连接。`)
  return response.json()
}

export function useRadio() {
  const [calls, setCalls] = useState<RadioCall[]>([])
  const [status, setStatus] = useState<RadioStatus | null>(null)
  const [stream, setStream] = useState<StreamState>('connecting')
  const [refreshing, setRefreshing] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const currentStatus = useRef<RadioStatus | null>(null)
  const applyStatus = useCallback((next: RadioStatus) => {
    const accepted = latestStatus(currentStatus.current, next)
    if (accepted !== currentStatus.current) {
      currentStatus.current = accepted
      setStatus(accepted)
    }
  }, [])
  const refreshRef = useRef<() => void>(() => undefined)
  const refresh = useCallback(() => refreshRef.current(), [])

  useEffect(() => {
    let active = true
    let controller: AbortController | null = null
    let pendingCalls = new Map<string, RadioCall>()
    let snapshotInFlight = false

    const reload = async () => {
      controller?.abort()
      const request = new AbortController()
      controller = request
      pendingCalls = new Map()
      snapshotInFlight = true
      setRefreshing(true)
      try {
        const [rawStatus, rawCalls] = await Promise.all([
          getJson('/api/status', request.signal),
          getJson('/api/calls?limit=200', request.signal),
        ])
        const nextStatus = parseStatus(rawStatus)
        if (!Array.isArray(rawCalls)) throw new Error('通话列表格式异常，请刷新重试。')
        const nextCalls = rawCalls.map(parseCall)
        if (!active || request.signal.aborted) return
        applyStatus(nextStatus)
        // Stopped receivers synchronize history from the snapshot, not late stream events.
        const duringRequest = acceptsCallUpdates(currentStatus.current) ? [...pendingCalls.values()] : []
        setCalls((previous) => reconcileCalls(previous, nextCalls, duringRequest))
        setReady(true)
        setError(null)
      } catch (reason) {
        if (!active || request.signal.aborted) return
        setError(reason instanceof TypeError ? '服务连接中断，正在自动恢复。' : reason instanceof SyntaxError ? '服务返回了无效数据，请刷新状态。' : reason instanceof Error ? reason.message : '连接暂不可用，正在等待重新连接。')
      } finally {
        if (active && !request.signal.aborted) {
          snapshotInFlight = false
          pendingCalls.clear()
          setRefreshing(false)
        }
      }
    }

    refreshRef.current = () => { void reload() }
    void reload()
    const events = new EventSource('/api/events')
    events.onopen = () => {
      if (!active) return
      setStream('connected')
      // Includes first connection: calls can change between GET and stream setup.
      void reload()
    }
    events.onerror = () => { if (active) setStream('reconnecting') }
    events.addEventListener('status', (event) => {
      if (!active) return
      try {
        const next = parseStatus(JSON.parse((event as MessageEvent<string>).data))
        applyStatus(next)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '无法读取电台状态。')
      }
    })
    events.addEventListener('call', (event) => {
      if (!active || !acceptsCallUpdates(currentStatus.current)) return
      try {
        const next = parseCall(JSON.parse((event as MessageEvent<string>).data))
        if (snapshotInFlight) pendingCalls.set(next.id, next)
        setCalls((previous) => mergeCalls(previous, [next]))
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '无法读取通话记录。')
      }
    })
    return () => {
      active = false
      controller?.abort()
      events.close()
      refreshRef.current = () => undefined
    }
  }, [applyStatus])

  return { calls, status, stream, refreshing, error, refresh, ready, applyStatus }
}
