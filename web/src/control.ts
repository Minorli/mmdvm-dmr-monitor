import { parseStatus } from './data'

export class ControlError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

export async function requestListening(action: 'start' | 'stop', token = '', signal?: AbortSignal) {
  if (token.length > 4096 || /[\r\n]/.test(token)) throw new ControlError('控制凭据格式不正确', 401)
  let response: Response
  try {
    response = await fetch(`/api/listening/${action}`, {
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', signal,
      headers: { 'Content-Type': 'application/json', 'X-Starwave-Request': '1', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: '{}',
    })
  } catch {
    throw new ControlError('无法连接服务，请检查连接后重试。', 0)
  }
  if (response.status === 401) throw new ControlError('控制凭据不正确', 401)
  if (response.status === 403) throw new ControlError('当前页面无控制权限，请使用本站页面操作。', 403)
  if (response.status === 409) throw new ControlError('监听状态正在转换，请稍后重试。', 409)
  const result: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const message = result && typeof result === 'object' && 'detail' in result && typeof result.detail === 'string'
      ? result.detail.slice(0, 500) : result && typeof result === 'object' && 'error' in result && typeof result.error === 'string'
        ? result.error.slice(0, 500) : '监听操作失败，请检查服务配置后重试。'
    throw new ControlError(response.status === 400 || response.status === 429 ? message : '监听操作失败，请检查服务状态后重试。', response.status)
  }
  return parseStatus(result)
}
