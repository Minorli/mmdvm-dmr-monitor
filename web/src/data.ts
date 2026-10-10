export type CallState = 'recording' | 'queued' | 'transcribing' | 'translating' | 'translation_pending' | 'transcribed' | 'complete' | 'failed' | 'short' | 'discarded'
export type ListeningState = 'starting' | 'running' | 'stopping' | 'stopped' | 'error'

export interface RadioCall {
  id: string
  slot: number
  tg: number
  source_id: number
  callsign: string
  country: string
  country_code: string
  name: string
  started_at: number
  ended_at: number | null
  duration: number
  status: CallState
  original: string | null
  translated: string | null
  topic: string | null
  language: string | null
  error: string | null
  audio_url: string | null
  direction?: 'RX' | 'NET' | 'incoming' | 'outgoing' | null
  metadata?: { direction?: 'incoming' | 'outgoing' | null } | null
}

export interface SlotStatus {
  slot: number
  tg: number
  active: boolean
  source_id?: number
  callsign?: string
  country?: string
  started_at?: number
  label?: string | null
}

export interface RadioStatus {
  status_version?: number
  capture_state: 'connecting' | 'online' | 'offline' | 'idle' | 'stopped'
  listening_state?: ListeningState
  control_requires_token?: boolean
  last_packet_at: number | null
  slots: SlotStatus[]
  counts: { calls: number; translated: number }
  ai: {
    transcription: 'local' | 'remote' | 'loading' | 'paused' | 'unavailable'
    translation: 'ready' | 'disabled' | 'unavailable'
    model: string
    pending_transcription?: number
    pending_translation?: number
    processing_transcription?: number
    processing_translation?: number
    last_transcription_seconds?: number | null
    last_translation_seconds?: number | null
    queues?: {
      live: { transcription: number; translation: number }
      history: { transcription: number; translation: number }
    }
  }
  device: { name: string; address: string; capture_source?: string | null }
  message?: string
}

const callStates: readonly string[] = ['recording', 'queued', 'transcribing', 'translating', 'translation_pending', 'transcribed', 'complete', 'failed', 'short', 'discarded']
const queueCounts = ['pending_transcription', 'pending_translation', 'processing_transcription', 'processing_translation'] as const
const processingTimes = ['last_transcription_seconds', 'last_translation_seconds'] as const
const callStrings = ['id', 'callsign', 'country', 'country_code', 'name'] as const
const optionalStrings = ['original', 'translated', 'topic', 'language', 'error', 'audio_url'] as const
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const nullableNumber = (value: unknown) => value === null || finite(value)
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

export function parseCall(value: unknown): RadioCall {
  if (!record(value) || !callStrings.every((key) => typeof value[key] === 'string') ||
    !optionalStrings.every((key) => value[key] === null || typeof value[key] === 'string') ||
    ![1, 2].includes(value.slot as number) || !finite(value.tg) || !finite(value.source_id) ||
    !finite(value.started_at) || !nullableNumber(value.ended_at) || !finite(value.duration) ||
    (value.direction !== undefined && value.direction !== null && !['RX', 'NET', 'incoming', 'outgoing'].includes(value.direction as string)) ||
    (value.metadata !== undefined && value.metadata !== null && (!record(value.metadata) || (value.metadata.direction !== undefined && value.metadata.direction !== null && !['incoming', 'outgoing'].includes(value.metadata.direction as string)))) ||
    !callStates.includes(value.status as string) || !value.id) {
    throw new Error('通话数据格式异常，请刷新重试。')
  }
  return value as unknown as RadioCall
}

export function parseStatus(value: unknown): RadioStatus {
  if (!record(value) || !['connecting', 'online', 'offline', 'idle', 'stopped'].includes(value.capture_state as string) ||
    (value.status_version !== undefined && (!Number.isSafeInteger(value.status_version) || (value.status_version as number) <= 0)) ||
    (value.listening_state !== undefined && !['starting', 'running', 'stopping', 'stopped', 'error'].includes(value.listening_state as string)) ||
    (value.control_requires_token !== undefined && typeof value.control_requires_token !== 'boolean') ||
    !nullableNumber(value.last_packet_at) || !record(value.counts) ||
    !finite(value.counts.calls) || !finite(value.counts.translated) || !record(value.ai) ||
    !['local', 'remote', 'loading', 'paused', 'unavailable'].includes(value.ai.transcription as string) ||
    !['ready', 'disabled', 'unavailable'].includes(value.ai.translation as string) || typeof value.ai.model !== 'string' ||
    !queueCounts.every((key) => value.ai && record(value.ai) && (value.ai[key] === undefined ||
      (finite(value.ai[key]) && Number.isInteger(value.ai[key]) && value.ai[key] >= 0))) ||
    !processingTimes.every((key) => value.ai && record(value.ai) && (value.ai[key] === undefined ||
      value.ai[key] === null || (finite(value.ai[key]) && value.ai[key] >= 0))) ||
    (value.ai.queues !== undefined && (!record(value.ai.queues) || !['live', 'history'].every((key) => {
      if (!record(value.ai) || !record(value.ai.queues)) return false
      const queue = value.ai.queues[key]
      return record(queue) && [queue.transcription, queue.translation].every((count) => finite(count) && Number.isInteger(count) && count >= 0)
    }))) ||
    !record(value.device) || typeof value.device.name !== 'string' || typeof value.device.address !== 'string' ||
    (value.device.capture_source !== undefined && value.device.capture_source !== null && typeof value.device.capture_source !== 'string') ||
    !Array.isArray(value.slots) || !value.slots.every((slot) => record(slot) &&
      [1, 2].includes(slot.slot as number) && finite(slot.tg) && typeof slot.active === 'boolean' &&
      (slot.label === undefined || slot.label === null || typeof slot.label === 'string') &&
      (slot.callsign === undefined || typeof slot.callsign === 'string') &&
      (slot.source_id === undefined || finite(slot.source_id))) ||
    (value.message !== undefined && typeof value.message !== 'string')) {
    throw new Error('电台状态格式异常，请刷新重试。')
  }
  return value as unknown as RadioStatus
}

function sameCall(left: RadioCall, right: RadioCall): boolean {
  return (Object.keys(right) as (keyof RadioCall)[]).every((key) => left[key] === right[key])
}

export function isVisibleCall(call: RadioCall): boolean {
  return call.status !== 'short' && call.status !== 'discarded' &&
    (call.status === 'recording' || call.duration >= 1)
}

/** Merge both snapshots and stream updates, retaining unchanged card references. */
export function mergeCalls(current: RadioCall[], incoming: RadioCall[]): RadioCall[] {
  const visible = current.filter(isVisibleCall)
  const byId = new Map(visible.map((call) => [call.id, call]))
  let changed = visible.length !== current.length
  for (const call of incoming) {
    if (!isVisibleCall(call)) {
      if (byId.delete(call.id)) changed = true
      continue
    }
    const previous = byId.get(call.id)
    if (!previous || !sameCall(previous, call)) {
      byId.set(call.id, call)
      changed = true
    }
  }
  if (!changed) return current
  const sorted = [...byId.values()].sort((a, b) => b.started_at - a.started_at || b.id.localeCompare(a.id))
  const kept = { 1: 0, 2: 0 }
  return sorted.filter((call) => ++kept[call.slot as 1 | 2] <= 200).reverse()
}

/** A refreshed list is authoritative; omitted calls may have been discarded. */
export function reconcileCalls(current: RadioCall[], snapshot: RadioCall[], streamUpdates: RadioCall[] = []): RadioCall[] {
  const snapshotIds = new Set(snapshot.map((call) => call.id))
  const retained = current.filter((call) => snapshotIds.has(call.id))
  return mergeCalls(mergeCalls(retained, snapshot), streamUpdates)
}

export function matchesSearch(call: RadioCall, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase()
  if (!needle) return true
  return [call.callsign, call.name, call.country, call.country_code, call.original,
    call.translated, call.topic, String(call.source_id), String(call.tg), directionLabel(call)]
    .some((value) => value?.toLocaleLowerCase().includes(needle))
}

export function countryFlag(code: string): string {
  if (!/^[A-Za-z]{2}$/.test(code)) return '🌐'
  return String.fromCodePoint(...code.toUpperCase().split('').map((char) => char.charCodeAt(0) + 127397))
}

export function directionLabel(call: RadioCall): string {
  const direction = call.direction ?? call.metadata?.direction
  return direction === 'incoming' ? 'NET' : direction === 'outgoing' ? 'RX' : direction || '—'
}

export function audioUrl(url: string | null): string | undefined {
  // Recordings are served by this application; never load arbitrary remote media.
  return url?.startsWith('/api/') && !url.includes('\\') ? url : undefined
}

export function durationLabel(seconds: number): string {
  const duration = Math.max(0, Math.floor(seconds))
  return `${String(Math.floor(duration / 60)).padStart(2, '0')}:${String(duration % 60).padStart(2, '0')}`
}

export const timeLabel = (timestamp: number) => new Date(timestamp * 1000)
  .toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })

export const dayLabel = (timestamp: number) => new Date(timestamp * 1000)
  .toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })

export const dayKey = (timestamp: number) => new Date(timestamp * 1000).toDateString()
