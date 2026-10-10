import type { ListeningState, RadioCall, RadioStatus, SlotStatus } from './data'

export type StreamState = 'connecting' | 'connected' | 'reconnecting'
export type CallFilter = 'all' | 'text' | 'processing' | 'failed'

export interface FeedTracking {
  initialized: boolean
  observedIds: string[]
  latestAt: number | null
  unreadIds: string[]
}

export const initialTracking: FeedTracking = { initialized: false, observedIds: [], latestAt: null, unreadIds: [] }

export function updateTracking(previous: FeedTracking, calls: RadioCall[], following: boolean, ready: boolean): FeedTracking {
  const ids = calls.map((call) => call.id)
  const current = new Set(ids)
  const observed = new Set(previous.observedIds)
  const latestAt = calls.reduce((latest, call) => Math.max(latest, call.started_at), previous.latestAt ?? 0)
  const additions = previous.initialized ? calls.filter((call) => !observed.has(call.id) &&
    (previous.latestAt === null || call.started_at >= previous.latestAt)).map((call) => call.id) : []
  return {
    initialized: previous.initialized || ready,
    observedIds: ids,
    latestAt: latestAt || null,
    unreadIds: following ? [] : [...new Set([...previous.unreadIds, ...additions])].filter((id) => current.has(id)),
  }
}

export function matchesFilter(call: RadioCall, filter: CallFilter): boolean {
  if (filter === 'text') return Boolean(call.original || call.translated)
  if (filter === 'processing') return ['queued', 'transcribing', 'translating', 'translation_pending', 'recording'].includes(call.status)
  if (filter === 'failed') return call.status === 'failed'
  return true
}

export function currentCall(calls: RadioCall[], slot?: SlotStatus): RadioCall | undefined {
  for (let index = calls.length - 1; index >= 0; index--) {
    const call = calls[index]
    if (call.status === 'recording' && (slot?.source_id === undefined || call.source_id === slot.source_id)) return call
  }
  return undefined
}

export function emptyFeedView({ ready, loading, stream, listening, capture, filtered }: {
  ready: boolean; loading: boolean; stream: StreamState; listening: ListeningState;
  capture?: RadioStatus['capture_state']; filtered: boolean
}) {
  if (!ready && loading) return { title: '正在同步日志', description: '读取近期通联记录。' }
  if (!ready) return { title: '日志同步失败', description: '点击「刷新」重试。' }
  if (filtered) return { title: '没有匹配的通话', description: '调整搜索条件或筛选项。' }
  if (stream !== 'connected') return { title: '链路断开', description: '连接恢复后会自动同步。' }
  if (listening === 'stopped') return { title: '监听已停止', description: '点击「开始监听」接收新的通话。' }
  if (capture === 'offline') return { title: '链路断开', description: '检查接收设备连接。' }
  return { title: '当前无通话', description: '新通话将在接收后显示。' }
}

export function listeningState(status: RadioStatus | null): ListeningState {
  if (status?.listening_state) return status.listening_state
  if (status?.capture_state === 'online') return 'running'
  if (status?.capture_state === 'connecting') return 'starting'
  if (!status || ['idle', 'stopped'].includes(status.capture_state)) return 'stopped'
  return 'error'
}

export function latestStatus(current: RadioStatus | null, incoming: RadioStatus): RadioStatus {
  if (current?.status_version !== undefined &&
    (incoming.status_version === undefined || incoming.status_version <= current.status_version)) return current
  return incoming
}

export function acceptsCallUpdates(status: RadioStatus | null): boolean {
  const state = listeningState(status)
  return state !== 'stopped' && state !== 'stopping'
}

export function controlView(state: ListeningState, pending = false) {
  const transitioning = state === 'starting' || state === 'stopping' || pending
  const stop = state === 'running' || state === 'error'
  return {
    action: (stop ? 'stop' : 'start') as 'start' | 'stop',
    disabled: transitioning,
    label: state === 'starting' ? '正在启动' : state === 'stopping' ? '正在停止' : stop ? '停止监听' : '开始监听',
  }
}

export function captureLabel(status: RadioStatus | null, stream: StreamState): string {
  if (!status) return '读取状态'
  if (stream !== 'connected') return '更新中断'
  const state = listeningState(status)
  if (state === 'stopped') return '监听已停止'
  if (state === 'starting') return '正在启动'
  if (state === 'stopping') return '正在停止'
  if (status.capture_state === 'online') return '接收在线'
  return '接收断开'
}
