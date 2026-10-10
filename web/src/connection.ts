import { ControlError } from './control'

export interface ConnectionProfile {
  host: string
  port: number
  username: string
  auth_mode: 'key' | 'password'
  password_configured: boolean
  key_available: boolean
  configured: boolean
}

export interface ConnectionDraft {
  host: string
  port: number
  username: string
  auth_mode: 'key' | 'password'
  password: string
}

export interface HostKey { fingerprint: string; algorithm: string }
export interface ConnectionTest {
  ok: boolean
  message: string
  needs_trust?: boolean
  host_key?: HostKey
}

const normalizedHost = (host: string) => host.trim().replace(/^\[([^\]]+)\]$/, '$1')

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

export function parseConnection(value: unknown): ConnectionProfile {
  if (!record(value) || typeof value.host !== 'string' || !Number.isInteger(value.port) ||
    (value.port as number) < 1 || (value.port as number) > 65535 || typeof value.username !== 'string' ||
    !['key', 'password'].includes(value.auth_mode as string) || typeof value.password_configured !== 'boolean' ||
    typeof value.key_available !== 'boolean' || typeof value.configured !== 'boolean') throw new Error('连接设置格式异常，请重新读取。')
  return { host: value.host, port: value.port as number, username: value.username,
    auth_mode: value.auth_mode as 'key' | 'password', password_configured: value.password_configured,
    key_available: value.key_available, configured: value.configured }
}

export function parseConnectionTest(value: unknown): ConnectionTest {
  if (!record(value) || typeof value.ok !== 'boolean' || typeof value.message !== 'string' ||
    (value.needs_trust !== undefined && typeof value.needs_trust !== 'boolean') ||
    (value.needs_trust === true && (!record(value.host_key) || typeof value.host_key.fingerprint !== 'string' ||
      typeof value.host_key.algorithm !== 'string'))) throw new Error('连接测试结果格式异常，请重试。')
  const hostKey = record(value.host_key) && typeof value.host_key.fingerprint === 'string' && typeof value.host_key.algorithm === 'string'
    ? { fingerprint: value.host_key.fingerprint, algorithm: value.host_key.algorithm } : undefined
  return { ok: value.ok, message: value.message.slice(0, 500), needs_trust: value.needs_trust as boolean | undefined, host_key: hostKey }
}

export function sameTarget(draft: ConnectionDraft, profile: ConnectionProfile | null): boolean {
  return Boolean(profile && normalizedHost(draft.host) === normalizedHost(profile.host) && draft.port === profile.port && draft.username.trim() === profile.username)
}

export function connectionPayload(draft: ConnectionDraft, profile: ConnectionProfile | null, fingerprint?: string) {
  const host = normalizedHost(draft.host)
  const username = draft.username.trim()
  if (!host || host.length > 253 || /[\s@/\\%]/.test(host)) throw new Error('设备地址只填写主机名或普通IP，不包含用户名、协议、路径或IPv6区域标识。')
  if (host.includes(':') && !/^[0-9a-fA-F:]+$/.test(host)) throw new Error('设备地址不要包含端口，请在端口栏填写。')
  if (!Number.isInteger(draft.port) || draft.port < 1 || draft.port > 65535) throw new Error('SSH端口应为1至65535的整数。')
  if (!/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/.test(username)) throw new Error('用户名格式不正确，请单独填写SSH用户名。')
  if (new TextEncoder().encode(draft.password).length > 4095 || /[\0\r\n]/.test(draft.password)) throw new Error('SSH口令格式不正确。')
  if (draft.auth_mode === 'password' && !draft.password && !(profile?.password_configured && sameTarget(draft, profile))) {
    throw new Error('请输入SSH口令；新目标不能使用旧目标保存的口令。')
  }
  if (draft.auth_mode === 'key' && profile && !profile.key_available) throw new Error('没有可用SSH密钥，请选择密码方式或由管理员配置密钥。')
  return { host, port: draft.port, username, auth_mode: draft.auth_mode,
    ...(draft.auth_mode === 'password' && draft.password ? { password: draft.password } : {}),
    ...(fingerprint ? { trust_fingerprint: fingerprint } : {}) }
}

async function connectionRequest(method: 'GET' | 'PUT' | 'POST', token: string, body?: unknown, signal?: AbortSignal): Promise<unknown> {
  if (token.length > 4096 || /[\r\n]/.test(token)) throw new ControlError('控制凭据格式不正确', 401)
  let response: Response
  try {
    response = await fetch(method === 'POST' ? '/api/connection/test' : '/api/connection', {
      method, signal, mode: 'same-origin', credentials: 'same-origin', redirect: 'error',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Starwave-Request': '1', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
  } catch { throw new ControlError('无法连接服务，请检查连接后重试。', 0) }
  if (response.status === 401) throw new ControlError('控制凭据不正确', 401)
  if (response.status === 403) throw new ControlError('当前页面无控制权限，请使用本站页面操作。', 403)
  if (response.status === 409) throw new ControlError('请先停止监听，再测试或保存连接设置。', 409)
  if (response.status === 404) throw new ControlError('当前服务尚未提供连接设置，请更新服务后重试。', 404)
  const value: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const message = record(value) && typeof value.detail === 'string' ? value.detail.slice(0, 500) :
      record(value) && typeof value.error === 'string' ? value.error.slice(0, 500) : '连接操作失败，请检查设置后重试。'
    throw new ControlError([400, 429].includes(response.status) ? message : '连接操作失败，请检查服务状态后重试。', response.status)
  }
  return value
}

export async function readConnection(token: string, signal?: AbortSignal) {
  return parseConnection(await connectionRequest('GET', token, undefined, signal))
}

export async function testConnection(body: ReturnType<typeof connectionPayload>, token: string, signal?: AbortSignal) {
  return parseConnectionTest(await connectionRequest('POST', token, body, signal))
}

export async function saveConnection(body: ReturnType<typeof connectionPayload>, token: string, signal?: AbortSignal) {
  const result = await connectionRequest('PUT', token, body, signal)
  if (record(result) && result.needs_trust === true) return { trust: parseConnectionTest(result) }
  if (record(result) && result.ok === false) throw new Error(parseConnectionTest(result).message || '保存前的连接检查未通过。')
  return { profile: parseConnection(result) }
}
