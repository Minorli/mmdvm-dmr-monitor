import { describe, expect, it } from 'vitest'
import { audioUrl, countryFlag, directionLabel, isVisibleCall, matchesSearch, mergeCalls, parseCall, parseStatus, reconcileCalls } from './data'
import type { RadioCall } from './data'

function call(id: string, started_at = 1, slot = 1): RadioCall {
  return { id, started_at, slot, tg: 91, source_id: 1234567, callsign: 'TEST', country: '测试地区',
    country_code: 'CN', name: '', ended_at: 5, duration: 4, status: 'complete', original: 'Good morning',
    translated: '早上好', topic: '天气', language: 'en', error: null, audio_url: `/api/audio/${id}` }
}

describe('real call stream data', () => {
  it('orders snapshots oldest first, deduplicates updates, and retains unaffected cards', () => {
    const earlier = call('earlier', 1)
    const later = call('later', 2)
    const snapshot = mergeCalls([], [later, earlier])
    expect(snapshot.map((item) => item.id)).toEqual(['earlier', 'later'])
    expect(mergeCalls(snapshot, [{ ...later }])).toBe(snapshot)
    const updated = mergeCalls(snapshot, [{ ...later, translated: '更新译文' }])
    expect(updated).toHaveLength(2)
    expect(updated[0]).toBe(earlier)
    expect(updated[1].translated).toBe('更新译文')
  })

  it('retains the newest 200 calls independently in each slot', () => {
    const records = Array.from({ length: 230 }, (_, index) => call(`one-${index}`, index))
    const secondSlot = call('two', 0, 2)
    const merged = mergeCalls([], [...records, secondSlot])
    expect(merged.filter((item) => item.slot === 1)).toHaveLength(200)
    expect(merged.find((item) => item.id === 'one-0')).toBeUndefined()
    expect(merged).toContain(secondSlot)
  })

  it('searches original, translation, topics, country, callsign, and DMR ID', () => {
    const item = call('search')
    for (const query of ['test', 'GOOD', '早上', '天气', '测试', '1234567', '  ']) {
      expect(matchesSearch(item, query)).toBe(true)
    }
    expect(matchesSearch(item, 'missing')).toBe(false)
  })

  it('hides historical short/discarded calls and ended calls shorter than one second', () => {
    const good = call('good')
    const records: RadioCall[] = [good, { ...call('short'), status: 'short' },
      { ...call('discarded'), status: 'discarded' }, { ...call('brief-complete'), duration: 0.9 },
      { ...call('brief-queued'), status: 'queued', duration: 0.5 }]
    expect(parseCall({ ...call('discarded'), status: 'discarded' }).status).toBe('discarded')
    expect(mergeCalls([], records)).toEqual([good])
    expect(isVisibleCall({ ...call('at-threshold'), duration: 1 })).toBe(true)
    expect(isVisibleCall({ ...call('recording'), duration: 0, status: 'recording' })).toBe(true)
  })

  it('removes an existing card when a short or discarded stream event arrives', () => {
    const unaffected = call('unaffected')
    const recording = { ...call('live'), status: 'recording' as const, duration: 1 }
    for (const status of ['short', 'discarded'] as const) {
      const updated = mergeCalls([unaffected, recording], [{ ...recording, status, duration: 0.7 }])
      expect(updated).toEqual([unaffected])
      expect(updated[0]).toBe(unaffected)
    }
    expect(mergeCalls([recording], [{ ...recording, status: 'complete', duration: 0.9 }])).toEqual([])
  })

  it('uses the fresh snapshot to remove omitted cards, then replays newer stream updates', () => {
    const kept = call('kept')
    const omitted = { ...call('omitted'), status: 'queued' as const }
    const duringRequest = call('new')
    expect(reconcileCalls([kept, omitted], [{ ...kept }], [duringRequest])).toEqual([kept, duringRequest])
    expect(reconcileCalls([kept, omitted], [{ ...kept }])[0]).toBe(kept)
    expect(reconcileCalls([kept], [kept], [{ ...kept, status: 'discarded' }])).toEqual([])
  })

  it('rejects invalid stream records and external recording URLs', () => {
    expect(parseCall(call('valid'))).toEqual(call('valid'))
    expect(() => parseCall({ ...call('invalid'), slot: 3 })).toThrow()
    expect(() => parseCall({ ...call('invalid'), started_at: Infinity })).toThrow()
    expect(audioUrl('/api/audio/real-call')).toBe('/api/audio/real-call')
    expect(audioUrl('https://external.example/audio.wav')).toBeUndefined()
    expect(audioUrl('//external.example/audio.wav')).toBeUndefined()
    expect(audioUrl('javascript:alert(1)')).toBeUndefined()
    expect(countryFlag('CN')).toBe('🇨🇳')
    expect(countryFlag('unknown')).toBe('🌐')
  })

  it('preserves legacy radio direction fields and transcribed-only records', () => {
    const network = parseCall({ ...call('network'), direction: 'incoming', status: 'transcribed', translated: null })
    expect(directionLabel(network)).toBe('NET')
    expect(matchesSearch(network, 'NET')).toBe(true)
    expect(directionLabel(parseCall({ ...call('rf'), metadata: { direction: 'outgoing' } }))).toBe('RX')
    expect(directionLabel(call('unknown'))).toBe('—')
    expect(() => parseCall({ ...call('invalid'), direction: 'TX' })).toThrow()
  })

  it('accepts the real model cold-start state without discarding the snapshot', () => {
    const coldStart = { capture_state: 'online', last_packet_at: null,
      slots: [{ slot: 1, tg: 91, active: false }, { slot: 2, tg: 46001, active: false }],
      counts: { calls: 0, translated: 0 }, ai: { transcription: 'loading', translation: 'ready', model: 'test-model',
        pending_transcription: 5, pending_translation: 3, processing_transcription: 2, processing_translation: 1,
        last_transcription_seconds: 8.5, last_translation_seconds: null },
      device: { name: 'Pi-Star', address: 'radio.example.test' } }
    expect(parseStatus(coldStart).ai.transcription).toBe('loading')
    expect(parseStatus(coldStart).ai.pending_transcription).toBe(5)
    expect(() => parseStatus({ ...coldStart, ai: { ...coldStart.ai, transcription: 'invalid' } })).toThrow()
    expect(() => parseStatus({ ...coldStart, ai: { ...coldStart.ai, pending_transcription: -1 } })).toThrow()
    expect(parseStatus({ ...coldStart, capture_state: 'idle', listening_state: 'stopped', control_requires_token: true }).listening_state).toBe('stopped')
    expect(parseStatus({ ...coldStart, ai: { ...coldStart.ai, transcription: 'paused', translation: 'disabled' } }).ai.transcription).toBe('paused')
    expect(parseStatus({ ...coldStart, status_version: 1791590380000000 }).status_version).toBe(1791590380000000)
    for (const status_version of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      expect(() => parseStatus({ ...coldStart, status_version })).toThrow()
    }
  })
})
