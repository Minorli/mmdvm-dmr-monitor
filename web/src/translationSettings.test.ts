import { afterEach, describe, expect, it, vi } from 'vitest'
import { canonicalTranslationUrl, parseTranslationSettings, saveTranslationSettings, translationPayload } from './translationSettings'
import type { TranslationDraft, TranslationSettings } from './translationSettings'

const settings: TranslationSettings = { base_url: 'https://api.example.com/v1', model: 'example-model', api_mode: 'responses', enabled: true, api_key_configured: true }
const draft: TranslationDraft = { ...settings, api_key: '', clear_api_key: false }
afterEach(() => vi.unstubAllGlobals())

describe('translation settings credentials', () => {
  it('retains keys for the same canonical URL when model or API format changes', () => {
    const payload = translationPayload({ ...draft, base_url: `${settings.base_url}/`, model: 'another-model', api_mode: 'chat_completions' }, settings)
    expect(payload.base_url).toBe(settings.base_url)
    expect(canonicalTranslationUrl('https://API.EXAMPLE.COM:443')).toBe(settings.base_url)
    expect(payload).not.toHaveProperty('api_key')
    expect(() => translationPayload({ ...draft, base_url: 'https://other.example.com/v1' }, settings)).toThrow('新地址')
    expect(translationPayload({ ...draft, base_url: 'https://other.example.com/v1', enabled: false }, settings)).not.toHaveProperty('api_key')
  })
  it('rejects key-bearing URLs and keeps public settings free of private key fields', () => {
    for (const url of ['https://user:secret@api.example.com/v1', 'https://api.example.com/v1?key=secret', 'https://api.example.com/v1#secret', 'file:///tmp/example']) {
      expect(() => canonicalTranslationUrl(url)).toThrow()
    }
    expect(parseTranslationSettings({ ...settings, api_key: 'must-not-restore' })).not.toHaveProperty('api_key')
    expect(() => translationPayload({ ...draft, api_key: 'bad\nkey' }, settings)).toThrow('可打印ASCII')
    expect(() => canonicalTranslationUrl('https://api.example.com/\n')).toThrow('控制字符')
    expect(() => translationPayload({ ...draft, model: '' }, settings)).toThrow('模型名称')
  })
  it('clears saved keys only while disabled and rejects simultaneous replacement', () => {
    expect(() => translationPayload({ ...draft, clear_api_key: true }, settings)).toThrow('关闭翻译')
    expect(() => translationPayload({ ...draft, enabled: false, clear_api_key: true, api_key: 'test-only-key' }, settings)).toThrow('同时清除')
    expect(translationPayload({ ...draft, enabled: false, clear_api_key: true }, settings)).toHaveProperty('clear_api_key', true)
  })
  it('uses same-origin control headers and shows 409 and 429 details', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"detail":"busy"}', { status: 409 }))
      .mockResolvedValueOnce(new Response('{"detail":"请求过于频繁"}', { status: 429 }))
    vi.stubGlobal('fetch', fetcher)
    const payload = translationPayload({ ...draft, api_key: 'test-only-key' }, settings)
    await expect(saveTranslationSettings(payload, 'test-control')).rejects.toThrow('请先停止监听')
    await expect(saveTranslationSettings(payload, 'test-control')).rejects.toThrow('请求过于频繁')
    expect(fetcher).toHaveBeenCalledWith('/api/translation-settings', expect.objectContaining({ method: 'PUT', mode: 'same-origin', redirect: 'error',
      headers: expect.objectContaining({ 'X-Starwave-Request': '1', Authorization: 'Bearer test-control' }) }))
  })
  it('never echoes unauthorized secrets or treats HTTP200 failure as a save', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{"detail":"private must not echo"}', { status: 401 }))
      .mockResolvedValueOnce(new Response('{"ok":false,"message":"设置未保存"}')))
    await expect(saveTranslationSettings(translationPayload(draft, settings), '')).rejects.toThrow('控制凭据不正确')
    await expect(saveTranslationSettings(translationPayload(draft, settings), '')).rejects.toThrow('设置未保存')
  })
})
