import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Checkbox, Input, InputNumber, Modal, Select } from '@arco-design/web-react'
import { ControlError } from './control'
import { connectionPayload, readConnection, sameTarget, saveConnection, testConnection } from './connection'
import type { ConnectionDraft, ConnectionProfile, HostKey } from './connection'
import type { ListeningState } from './data'

const emptyDraft: ConnectionDraft = { host: '', port: 22, username: 'pi-star', auth_mode: 'password', password: '' }

export default function ConnectionDialog({ visible, onClose, state, requiresToken, getToken, rememberToken, onSaved }: {
  visible: boolean; onClose: () => void; state: ListeningState; requiresToken: boolean;
  getToken: () => string; rememberToken: (value: string) => void; onSaved: (profile: ConnectionProfile) => void
}) {
  const [profile, setProfile] = useState<ConnectionProfile | null>(null)
  const [draft, setDraft] = useState<ConnectionDraft>(emptyDraft)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<'test' | 'save' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [hostKey, setHostKey] = useState<HostKey | null>(null)
  const [trustReason, setTrustReason] = useState<string | null>(null)
  const [trusted, setTrusted] = useState(false)
  const [needsToken, setNeedsToken] = useState(false)
  const [credential, setCredential] = useState('')
  const request = useRef<AbortController | null>(null)
  const allowed = state === 'stopped'
  const disabled = loading || Boolean(busy)
  const retainedPassword = profile?.password_configured && sameTarget(draft, profile)

  const reportError = useCallback((reason: unknown) => {
    setError(reason instanceof Error ? reason.message : '连接操作失败，请重试。')
    if (reason instanceof ControlError && reason.status === 401) {
      rememberToken('')
      setCredential('')
      setNeedsToken(true)
    }
  }, [rememberToken])

  const load = useCallback(async (token: string, controller: AbortController) => {
    setLoading(true)
    setError(null)
    try {
      const next = await readConnection(token, controller.signal)
      if (controller.signal.aborted) return
      setProfile(next)
      setDraft({ host: next.host, port: next.port, username: next.username || 'pi-star',
        auth_mode: next.auth_mode === 'key' && !next.key_available ? 'password' : next.auth_mode, password: '' })
    } catch (reason) { if (!controller.signal.aborted) reportError(reason) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [reportError])

  useEffect(() => {
    if (!visible) {
      setDraft((previous) => ({ ...previous, password: '' }))
      setCredential('')
      setHostKey(null)
      setTrustReason(null)
      setTrusted(false)
      return
    }
    const controller = new AbortController()
    request.current = controller
    setProfile(null)
    setDraft(emptyDraft)
    setMessage(null)
    setNeedsToken(false)
    void load(getToken(), controller)
    return () => controller.abort()
  }, [visible, getToken, load])
  useEffect(() => () => request.current?.abort(), [])

  const changeTarget = <K extends 'host' | 'port' | 'username'>(key: K, value: ConnectionDraft[K]) => {
    setDraft((previous) => ({ ...previous, [key]: value, password: '' }))
    setHostKey(null)
    setTrustReason(null)
    setTrusted(false)
    setMessage(null)
    setError(null)
  }

  const operate = async (action: 'test' | 'save') => {
    if (!allowed || disabled || !profile) return
    const token = credential.trim() || getToken()
    if ((requiresToken || needsToken) && !token) { setError('请输入控制凭据。'); setNeedsToken(true); return }
    let body: ReturnType<typeof connectionPayload>
    try { body = connectionPayload(draft, profile, trusted ? hostKey?.fingerprint : undefined) }
    catch (reason) { reportError(reason); return }
    const controller = new AbortController()
    request.current = controller
    setBusy(action)
    setError(null)
    setMessage(null)
    try {
      if (action === 'test') {
        const result = await testConnection(body, token, controller.signal)
        if (controller.signal.aborted) return
        if (result.needs_trust) { setHostKey(result.host_key!); setTrustReason(result.message); setTrusted(false); return }
        if (!result.ok) { setError(result.message || '连接测试未通过。'); return }
        rememberToken(token)
        setCredential('')
        setMessage(result.message || '连接测试通过。')
      } else {
        const result = await saveConnection(body, token, controller.signal)
        if (controller.signal.aborted) return
        if (result.trust) { setHostKey(result.trust.host_key!); setTrustReason(result.trust.message); setTrusted(false); return }
        const next = result.profile!
        setProfile(next)
        setDraft({ host: next.host, port: next.port, username: next.username, auth_mode: next.auth_mode, password: '' })
        rememberToken(token)
        setCredential('')
        setHostKey(null)
        setTrustReason(null)
        setTrusted(false)
        setMessage('连接设置已保存。')
        onSaved(next)
      }
    } catch (reason) { if (!controller.signal.aborted) reportError(reason) }
    finally { if (!controller.signal.aborted) setBusy(null) }
  }

  const reload = () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    void load(credential.trim() || getToken(), controller)
  }
  const close = () => {
    if (busy) return
    request.current?.abort()
    setDraft((previous) => ({ ...previous, password: '' }))
    setCredential('')
    onClose()
  }

  return <Modal className="connection-dialog" title="连接设置" visible={visible} onCancel={close}
    closable={!busy} maskClosable={!busy} escToExit={!busy} style={{ width: 'min(500px, calc(100vw - 24px))' }}
    footer={<><Button size="small" disabled={Boolean(busy)} onClick={close}>关闭</Button><Button size="small" disabled={!allowed || disabled || !profile} loading={busy === 'test'} onClick={() => { void operate('test') }}>测试连接</Button><Button type="primary" size="small" disabled={!allowed || disabled || !profile} loading={busy === 'save'} onClick={() => { void operate('save') }}>保存设置</Button></>}>
    {!allowed && <p className="connection-state-note">请先停止监听，再测试或保存连接设置。</p>}
    {loading && <p className="connection-state-note">正在读取连接设置…</p>}
    <div className="connection-fields">
      <div className="connection-address-row"><label htmlFor="ssh-host">设备地址<Input id="ssh-host" aria-label="设备地址" disabled={disabled} maxLength={253} placeholder="主机名或IP，不包含用户名" value={draft.host} onChange={(value) => changeTarget('host', value)} /></label><label htmlFor="ssh-port">SSH端口<InputNumber id="ssh-port" aria-label="SSH端口" disabled={disabled} min={1} max={65535} precision={0} value={Number.isFinite(draft.port) ? draft.port : undefined} onChange={(value) => changeTarget('port', value)} /></label></div>
      <label htmlFor="ssh-user">SSH用户名<Input id="ssh-user" aria-label="SSH用户名" disabled={disabled} maxLength={64} value={draft.username} onChange={(value) => changeTarget('username', value)} /></label>
      <div className="connection-field"><span>认证方式</span><Select aria-label="认证方式" disabled={disabled} value={draft.auth_mode} getPopupContainer={(node) => node.closest('.arco-modal') || document.body} options={[{ value: 'key', label: 'SSH密钥', disabled: profile?.key_available === false }, { value: 'password', label: '密码' }]} onChange={(value: 'key' | 'password') => { setDraft((previous) => ({ ...previous, auth_mode: value, password: '' })); setMessage(null) }} /></div>
      {draft.auth_mode === 'password' ? <label htmlFor="ssh-password">SSH口令<Input.Password id="ssh-password" aria-label="SSH口令" autoComplete="new-password" disabled={disabled} maxLength={4095} value={draft.password} placeholder={retainedPassword ? '已保存；留空保留当前目标口令' : '输入此设备的SSH口令'} onChange={(password) => setDraft((previous) => ({ ...previous, password }))} /><small>{retainedPassword ? '仅同一地址、端口和用户名可保留；目标变更须重新输入。' : '口令只写入服务端，页面不会读取已保存的口令。'}</small></label> : <p className="connection-field-help">使用服务端已配置的SSH密钥，页面不读取私钥。</p>}
      {(requiresToken || needsToken) && <label htmlFor="connection-control-token">控制凭据<Input.Password id="connection-control-token" aria-label="连接设置控制凭据" autoComplete="off" disabled={disabled} maxLength={4096} value={credential} placeholder={getToken() ? '留空使用本页面已验证凭据' : '输入站点控制凭据'} onChange={setCredential} /></label>}
    </div>
    {hostKey && <div className="host-trust"><strong>核对设备指纹</strong>{trustReason && <p className="host-trust-reason">{trustReason}</p>}<span>{hostKey.algorithm}</span><code>{hostKey.fingerprint}</code><p>请通过设备或管理员核对指纹，再确认信任。</p><Checkbox checked={trusted} disabled={disabled} onChange={setTrusted}>信任此设备</Checkbox></div>}
    {error && <p className="connection-error" role="alert">{error}</p>}
    {message && <p className="connection-success" role="status">{message}</p>}
    {!profile && !loading && <Button size="small" onClick={reload}>读取设置</Button>}
  </Modal>
}
