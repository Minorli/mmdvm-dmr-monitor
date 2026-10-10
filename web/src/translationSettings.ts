import { ControlError } from './control'

export interface TranslationSettings {
  base_url: string
  model: string
  api_mode: 'responses' | 'chat_completions'
  enabled: boolean
  api_key_configured: boolean
}

export interface TranslationDraft {
  base_url: string
  model: string
  api_mode: 'responses' | 'chat_completions'
  enabled: boolean
  api_key: string
  clear_api_key: boolean
}

export const defaultTranslationDraft: TranslationDraft = {
  base_url: 'https://api.openai.com/v1', model: '', api_mode: 'responses', enabled: false, api_key: '', clear_api_key: false,
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

export function parseTranslationSettings(value: unknown): TranslationSettings {
  if (!record(value) || typeof value.base_url !== 'string' || typeof value.model !== 'string' ||
    !['responses', 'chat_completions'].includes(value.api_mode as string) || typeof value.enabled !== 'boolean' ||
    typeof value.api_key_configured !== 'boolean') throw new Error('翻译设置格式异常，请重新读取。')
  return { base_url: value.base_url, model: value.model, api_mode: value.api_mode as TranslationSettings['api_mode'],
    enabled: value.enabled, api_key_configured: value.api_key_configured }
}

export function canonicalTranslationUrl(value: string): string {
  if (/[\x00-\x1f\x7f]/.test(value)) throw new Error('API基础地址不能包含控制字符。')
  let url: URL
  try { url = new URL(value.trim()) } catch { throw new Error('请输入有效的HTTP(S) API基础地址。') }
  if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password || /[?#]/.test(url.href)) {
    throw new Error('API基础地址只允许HTTP(S)，不包含登录信息、查询参数或片段。')
  }
  const base = url.href.replace(/\/+$/, '')
  return base.endsWith('/v1') ? base : `${base}/v1`
}

export function sameTranslationProvider(draft: TranslationDraft, settings: TranslationSettings | null): boolean {
  if (!settings?.base_url) return false
  try { return canonicalTranslationUrl(draft.base_url) === canonicalTranslationUrl(settings.base_url) } catch { return false }
}

export function translationPayload(draft: TranslationDraft, settings: TranslationSettings | null) {
  const base_url = canonicalTranslationUrl(draft.base_url)
  const model = draft.model.trim()
  const api_key = draft.api_key.replace(/^ +| +$/g, '')
  if (base_url.length > 1024) throw new Error('API基础地址过长。')
  if (!model || model.length > 200 || /[\x00-\x1f\x7f]/.test(draft.model)) throw new Error('请填写有效的模型名称（不超过200字符）。')
  if (!['responses', 'chat_completions'].includes(draft.api_mode)) throw new Error('API格式不正确。')
  if (draft.api_key.length > 4096 || !/^[\x20-\x7e]*$/.test(draft.api_key)) throw new Error('API Key只允许不超过4096字符的可打印ASCII。')
  if (draft.clear_api_key && api_key) throw new Error('不能同时清除已保存Key并填写新Key。')
  if (draft.clear_api_key && draft.enabled) throw new Error('请先关闭翻译，再清除已保存Key。')
  if (draft.enabled && !api_key && !(settings?.api_key_configured && !draft.clear_api_key && sameTranslationProvider(draft, settings))) {
    throw new Error('请输入此API地址的Key；启用新地址不能复用旧地址保存的Key。')
  }
  return { base_url, model, api_mode: draft.api_mode, enabled: draft.enabled,
    ...(api_key ? { api_key } : {}), ...(draft.clear_api_key ? { clear_api_key: true } : {}) }
}

async function requestTranslationSettings(method: 'GET' | 'PUT', token: string, body?: unknown, signal?: AbortSignal): Promise<unknown> {
  if (token.length > 4096 || /[\r\n]/.test(token)) throw new ControlError('控制凭据格式不正确', 401)
  let response: Response
  try {
    response = await fetch('/api/translation-settings', { method, signal, mode: 'same-origin', credentials: 'same-origin', redirect: 'error',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Starwave-Request': '1', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  } catch { throw new ControlError('无法连接服务，请检查连接后重试。', 0) }
  if (response.status === 401) throw new ControlError('控制凭据不正确', 401)
  if (response.status === 403) throw new ControlError('当前页面无控制权限，请使用本站页面操作。', 403)
  if (response.status === 409) throw new ControlError('请先停止监听，再保存翻译设置。', 409)
  if (response.status === 404) throw new ControlError('当前服务尚未提供翻译设置，请更新服务后重试。', 404)
  const value: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const message = record(value) && typeof value.detail === 'string' ? value.detail.slice(0, 500) :
      record(value) && typeof value.error === 'string' ? value.error.slice(0, 500) : '翻译设置操作失败，请重试。'
    throw new ControlError([400, 429].includes(response.status) ? message : '翻译设置操作失败，请检查服务状态后重试。', response.status)
  }
  return value
}

export async function readTranslationSettings(token: string, signal?: AbortSignal) {
  return parseTranslationSettings(await requestTranslationSettings('GET', token, undefined, signal))
}

export async function saveTranslationSettings(body: ReturnType<typeof translationPayload>, token: string, signal?: AbortSignal) {
  const value = await requestTranslationSettings('PUT', token, body, signal)
  if (record(value) && value.ok === false) throw new Error(typeof value.message === 'string' ? value.message.slice(0, 500) : '翻译设置未保存。')
  return parseTranslationSettings(value)
}
