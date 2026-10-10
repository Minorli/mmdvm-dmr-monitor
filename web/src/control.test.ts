import { afterEach, describe, expect, it, vi } from 'vitest'
import { requestListening } from './control'

afterEach(() => vi.unstubAllGlobals())

describe('listening control requests', () => {
  it('shows the server rate-limit explanation when a control request is rejected', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"error":"请求过于频繁，请稍后重试。"}', { status: 429 })))
    await expect(requestListening('stop')).rejects.toThrow('请求过于频繁，请稍后重试。')
  })
  it('accepts an asynchronous starting response from the service', async () => {
    const starting = { capture_state: 'connecting', listening_state: 'starting', control_requires_token: false,
      last_packet_at: null, slots: [{ slot: 1, tg: 91, active: false }, { slot: 2, tg: 46001, active: false }],
      counts: { calls: 0, translated: 0 }, ai: { transcription: 'loading', translation: 'unavailable', model: '' },
      device: { name: 'test station', address: 'localhost' } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(starting), { status: 202 })))
    expect((await requestListening('start')).listening_state).toBe('starting')
  })
  it('sends only same-origin JSON and the required request header without unsolicited authorization', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(requestListening('start')).rejects.toThrow('控制凭据不正确')
    expect(fetcher).toHaveBeenCalledWith('/api/listening/start', expect.objectContaining({
      method: 'POST', mode: 'same-origin', redirect: 'error', body: '{}',
      headers: { 'Content-Type': 'application/json', 'X-Starwave-Request': '1' },
    }))
  })
  it('never echoes a rejected credential and rejects invalid header input before sending', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"error":"sensitive response"}', { status: 401 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(requestListening('stop', 'unit-test-credential')).rejects.toThrow('控制凭据不正确')
    await expect(requestListening('stop', 'invalid\r\nheader')).rejects.toThrow('控制凭据格式不正确')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
