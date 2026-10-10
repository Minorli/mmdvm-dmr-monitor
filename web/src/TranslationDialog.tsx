import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Checkbox, Input, Modal, Select } from '@arco-design/web-react'
import { ControlError } from './control'
import { defaultTranslationDraft, readTranslationSettings, sameTranslationProvider, saveTranslationSettings, translationPayload } from './translationSettings'
import type { TranslationDraft, TranslationSettings } from './translationSettings'
import type { ListeningState } from './data'

export default function TranslationDialog({ visible, onClose, state, requiresToken, getToken, rememberToken, onSaved }: {
  visible: boolean; onClose: () => void; state: ListeningState; requiresToken: boolean;
  getToken: () => string; rememberToken: (token: string) => void; onSaved: () => void
}) {
  const [settings, setSettings] = useState<TranslationSettings | null>(null)
  const [draft, setDraft] = useState<TranslationDraft>(defaultTranslationDraft)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [needsToken, setNeedsToken] = useState(false)
  const [credential, setCredential] = useState('')
  const request = useRef<AbortController | null>(null)
  const allowed = state === 'stopped'
  const disabled = loading || saving
  const retainedKey = settings?.api_key_configured && sameTranslationProvider(draft, settings)

  const reportError = useCallback((reason: unknown) => {
    setError(reason instanceof Error ? reason.message : '翻译设置操作失败，请重试。')
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
      const next = await readTranslationSettings(token, controller.signal)
      if (controller.signal.aborted) return
      setSettings(next)
      setDraft({ base_url: next.base_url || defaultTranslationDraft.base_url, model: next.model,
        api_mode: next.api_mode, enabled: next.enabled, api_key: '', clear_api_key: false })
    } catch (reason) { if (!controller.signal.aborted) reportError(reason) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [reportError])

  useEffect(() => {
    if (!visible) {
      setDraft((previous) => ({ ...previous, api_key: '', clear_api_key: false }))
      setCredential('')
      return
    }
    const controller = new AbortController()
    request.current = controller
    setSettings(null)
    setDraft(defaultTranslationDraft)
    setMessage(null)
    setNeedsToken(false)
    void load(getToken(), controller)
    return () => controller.abort()
  }, [visible, getToken, load])
  useEffect(() => () => request.current?.abort(), [])

  const save = async () => {
    if (!allowed || disabled || !settings) return
    const token = credential.trim() || getToken()
    if ((requiresToken || needsToken) && !token) { setNeedsToken(true); setError('请输入控制凭据。'); return }
    let body: ReturnType<typeof translationPayload>
    try { body = translationPayload(draft, settings) } catch (reason) { reportError(reason); return }
    const controller = new AbortController()
    request.current = controller
    setSaving(true)
    setError(null)
    setMessage(null)
    try {
      const next = await saveTranslationSettings(body, token, controller.signal)
      if (controller.signal.aborted) return
      setSettings(next)
      setDraft({ base_url: next.base_url, model: next.model, api_mode: next.api_mode, enabled: next.enabled, api_key: '', clear_api_key: false })
      rememberToken(token)
      setCredential('')
      setMessage('翻译设置已保存。')
      onSaved()
    } catch (reason) { if (!controller.signal.aborted) reportError(reason) }
    finally { if (!controller.signal.aborted) setSaving(false) }
  }
  const reload = () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    void load(credential.trim() || getToken(), controller)
  }
  const close = () => {
    if (saving) return
    request.current?.abort()
    setDraft((previous) => ({ ...previous, api_key: '', clear_api_key: false }))
    setCredential('')
    onClose()
  }

  return <Modal className="translation-dialog" title="翻译设置" visible={visible} onCancel={close} closable={!saving} maskClosable={!saving} escToExit={!saving}
    style={{ width: 'min(500px, calc(100vw - 24px))' }}
    footer={<><Button size="small" disabled={saving} onClick={close}>关闭</Button><Button type="primary" size="small" disabled={!allowed || disabled || !settings} loading={saving} onClick={() => { void save() }}>保存翻译设置</Button></>}>
    {!allowed && <p className="connection-state-note">请先停止监听，再保存翻译设置。</p>}
    {loading && <p className="connection-state-note">正在读取翻译设置…</p>}
    <div className="connection-fields">
      <Checkbox disabled={disabled} checked={draft.enabled} onChange={(enabled) => { setDraft((previous) => ({ ...previous, enabled })); setMessage(null) }}>启用中文翻译</Checkbox>
      <label htmlFor="translation-base-url">API基础地址<Input id="translation-base-url" aria-label="API基础地址" disabled={disabled} maxLength={1024} placeholder="https://api.openai.com/v1" value={draft.base_url}
        onChange={(base_url) => { setDraft((previous) => ({ ...previous, base_url, api_key: '', clear_api_key: false })); setError(null); setMessage(null) }} /><small>使用服务商提供的基础地址，不在地址中填写Key。</small></label>
      <label htmlFor="translation-model">模型名称<Input id="translation-model" aria-label="模型名称" disabled={disabled} maxLength={200} placeholder="例如：example-model" value={draft.model} onChange={(model) => { setDraft((previous) => ({ ...previous, model })); setMessage(null) }} /></label>
      <div className="connection-field"><span>API格式</span><Select aria-label="API格式" disabled={disabled} value={draft.api_mode} getPopupContainer={(node) => node.closest('.arco-modal') || document.body}
        options={[{ value: 'responses', label: 'Responses API' }, { value: 'chat_completions', label: 'Chat Completions API' }]}
        onChange={(api_mode: TranslationDraft['api_mode']) => { setDraft((previous) => ({ ...previous, api_mode })); setMessage(null) }} /></div>
      <label htmlFor="translation-api-key">API Key<Input.Password id="translation-api-key" aria-label="API Key" autoComplete="new-password" allowClear disabled={disabled || draft.clear_api_key} maxLength={4096} value={draft.api_key}
        placeholder={retainedKey ? '已保存；同API地址留空保留' : '启用此API地址时请输入Key'} onChange={(api_key) => setDraft((previous) => ({ ...previous, api_key }))} />
        <small>Key只写入服务端，不回显。更改API地址后启用翻译，需重新输入Key。</small></label>
      {settings?.api_key_configured && <Checkbox disabled={disabled} checked={draft.clear_api_key} onChange={(clear_api_key) => { setDraft((previous) => ({ ...previous, clear_api_key, api_key: '' })); setMessage(null) }}>清除已保存Key（先关闭翻译）</Checkbox>}
      {(requiresToken || needsToken) && <label htmlFor="translation-control-token">控制凭据<Input.Password id="translation-control-token" aria-label="翻译设置控制凭据" autoComplete="off" disabled={disabled} maxLength={4096} value={credential}
        placeholder={getToken() ? '留空使用本页面已验证凭据' : '输入站点控制凭据'} onChange={setCredential} /></label>}
    </div>
    {error && <p className="connection-error" role="alert">{error}</p>}
    {message && <p className="connection-success" role="status">{message}</p>}
    {!settings && !loading && <Button size="small" onClick={reload}>读取翻译设置</Button>}
  </Modal>
}
