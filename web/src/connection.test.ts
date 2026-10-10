import { afterEach, describe, expect, it, vi } from 'vitest'
import { connectionPayload, parseConnection, saveConnection, testConnection } from './connection'
import type { ConnectionDraft, ConnectionProfile } from './connection'

const profile: ConnectionProfile = { host: 'pistar.local', port: 22, username: 'pi-star', auth_mode: 'password', password_configured: true, key_available: false, configured: true }
const draft: ConnectionDraft = { host: profile.host, port: 22, username: profile.username, auth_mode: 'password', password: '' }
afterEach(() => vi.unstubAllGlobals())

describe('connection credentials and trust', () => {
  it('retains a stored password only for the same complete target without sending it back', () => {
    expect(connectionPayload(draft, profile)).not.toHaveProperty('password')
    for (const changed of [{ host: 'other.local' }, { port: 2222 }, { username: 'other' }]) {
      expect(() => connectionPayload({ ...draft, ...changed }, profile)).toThrow('新目标')
    }
    expect(connectionPayload({ ...draft, host: 'other.local', password: 'test-secret' }, profile).password).toBe('test-secret')
  })
  it('rejects user@host and strips unexpected private fields from public settings', () => {
    expect(() => connectionPayload({ ...draft, host: 'pi-star@pistar.local' }, profile)).toThrow()
    expect(() => connectionPayload({ ...draft, host: 'ssh://pistar.local' }, profile)).toThrow()
    expect(() => connectionPayload({ ...draft, host: 'fe80::1%eth0' }, profile)).toThrow()
    expect(parseConnection({ ...profile, password: 'must-not-be-restored' })).not.toHaveProperty('password')
  })
  it('does not treat a needs-trust save response as a successful persistence', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false, message: '核对指纹', needs_trust: true, host_key: { fingerprint: 'SHA256:test', algorithm: 'ssh-ed25519' } }))))
    const result = await saveConnection(connectionPayload(draft, profile), '')
    expect(result).toHaveProperty('trust.needs_trust', true)
    expect(result).not.toHaveProperty('profile')
  })
  it('shows HTTP200 scan failures without treating them as a save, and accepts public success fields', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"ok":false,"message":"无法扫描设备指纹"}'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, ...profile })))
    vi.stubGlobal('fetch', fetcher)
    await expect(saveConnection(connectionPayload(draft, profile), '')).rejects.toThrow('无法扫描设备指纹')
    expect(await saveConnection(connectionPayload(draft, profile), '')).toEqual({ profile })
  })
  it('reads safe FastAPI detail errors and checks the UTF8 password limit', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"SSH用户名不正确"}', { status: 400 })))
    await expect(testConnection(connectionPayload(draft, profile), '')).rejects.toThrow('SSH用户名不正确')
    expect(() => connectionPayload({ ...draft, password: '字'.repeat(1366) }, profile)).toThrow('SSH口令格式')
    expect(() => connectionPayload({ ...draft, password: 'x'.repeat(4096) }, profile)).toThrow('SSH口令格式')
    expect(connectionPayload({ ...draft, host: '[2001:db8::1]', password: 'test-secret' }, profile).host).toBe('2001:db8::1')
  })
  it('sends explicit fingerprint trust with the same-origin control header and handles 409', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 409 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(testConnection(connectionPayload(draft, profile, 'SHA256:checked'), 'test-control')).rejects.toThrow('请先停止监听')
    expect(fetcher).toHaveBeenCalledWith('/api/connection/test', expect.objectContaining({ method: 'POST', mode: 'same-origin', redirect: 'error',
      headers: expect.objectContaining({ 'X-Starwave-Request': '1', Authorization: 'Bearer test-control' }),
      body: expect.stringContaining('"trust_fingerprint":"SHA256:checked"') }))
  })
})
