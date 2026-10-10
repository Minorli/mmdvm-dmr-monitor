import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Checkbox, Input, Modal, Select, Tooltip } from '@arco-design/web-react'
import { IconInfoCircle, IconMoon, IconPause, IconPlayArrow, IconRecordStop, IconRefresh, IconSearch, IconSettings, IconSun, IconTranslate, IconWifi } from '@arco-design/web-react/icon'
import TranslationDialog from './TranslationDialog'
import ConnectionDialog from './ConnectionDialog'
import { readConnection } from './connection'
import type { ConnectionProfile } from './connection'
import Diagnostics from './Diagnostics'
import SlotPanel from './SlotPanel'
import type { TextPreferences } from './CallCard'
import { ControlError, requestListening } from './control'
import { captureLabel, controlView, listeningState } from './monitorState'
import type { CallFilter } from './monitorState'
import { useRadio } from './useRadio'
import { readFollowPreference, saveFollowPreference } from './followPreference'

function Clock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return <time className="header-clock">{now.toLocaleDateString('zh-CN')} <strong>{now.toLocaleTimeString('zh-CN', { hour12: false })}</strong></time>
}

function initialTheme(): 'light' | 'dark' {
  try { return localStorage.getItem('starwave.theme') === 'dark' ? 'dark' : 'light' } catch { return 'light' }
}

export default function App() {
  const { calls, status, stream, refreshing, error, refresh, ready, applyStatus } = useRadio()
  const [query, setQuery] = useState('')
  const search = useDeferredValue(query)
  const [filter, setFilter] = useState<CallFilter>('all')
  const [follow, setFollow] = useState(readFollowPreference)
  const [text, setText] = useState<TextPreferences>({ original: true, translated: true })
  const [mobileSlot, setMobileSlot] = useState<1 | 2>(1)
  const [details, setDetails] = useState(false)
  const [translationOpen, setTranslationOpen] = useState(false)
  const closeTranslationSettings = useCallback(() => setTranslationOpen(false), [])
  const translationSaved = useCallback(() => refresh(), [refresh])
  const [connectionOpen, setConnectionOpen] = useState(false)
  const [connectionOpening, setConnectionOpening] = useState(false)
  const [connectionConfigured, setConnectionConfigured] = useState<boolean | null>(null)
  const connectionPrompted = useRef(false)
  const [theme, setTheme] = useState(initialTheme)
  const [controlPending, setControlPending] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)
  const [tokenAction, setTokenAction] = useState<'start' | 'stop' | null>(null)
  const [draftToken, setDraftToken] = useState('')
  const token = useRef('')
  const getToken = useCallback(() => token.current, [])
  const rememberToken = useCallback((value: string) => { token.current = value }, [])
  const controller = useRef<AbortController | null>(null)
  const slotOne = useMemo(() => calls.filter((call) => call.slot === 1), [calls])
  const slotTwo = useMemo(() => calls.filter((call) => call.slot === 2), [calls])
  const state = listeningState(status)
  const control = controlView(state, controlPending)
  const label = captureLabel(status, stream)
  const ai = status?.ai
  const receptionOnline = state === 'running' && stream === 'connected' && status?.capture_state === 'online'
  const notice = error || controlError || (state === 'error' || (state === 'running' && status?.capture_state === 'offline') ? status?.message || '接收链路断开，请检查设备连接。' : null)

  useEffect(() => {
    if (theme === 'dark') document.body.setAttribute('arco-theme', 'dark')
    else document.body.removeAttribute('arco-theme')
    try { localStorage.setItem('starwave.theme', theme) } catch { /* Theme remains usable when storage is disabled. */ }
  }, [theme])
  useEffect(() => () => controller.current?.abort(), [])
  const openConnectionSettings = useCallback(async (onlyUnconfigured = false) => {
    setConnectionOpening(true)
    if (!onlyUnconfigured) setControlError(null)
    try {
      const profile = await readConnection(getToken())
      setConnectionConfigured(profile.configured)
      if (!onlyUnconfigured || !profile.configured) setConnectionOpen(true)
    } catch (reason) {
      if (reason instanceof ControlError && reason.status === 401) setConnectionOpen(true)
      else if (!onlyUnconfigured) setControlError(reason instanceof Error ? reason.message : '无法读取连接设置。')
    } finally { setConnectionOpening(false) }
  }, [getToken])
  const closeConnectionSettings = useCallback(() => setConnectionOpen(false), [])
  const connectionSaved = useCallback((profile: ConnectionProfile) => {
    setConnectionConfigured(profile.configured)
    refresh()
  }, [refresh])
  useEffect(() => {
    if (status && state === 'stopped' && !connectionPrompted.current) {
      connectionPrompted.current = true
      void openConnectionSettings(true)
    }
  }, [status, state, openConnectionSettings])

  const runControl = useCallback(async (action: 'start' | 'stop', credential: string) => {
    const request = new AbortController()
    controller.current = request
    setControlPending(true)
    setControlError(null)
    try {
      const next = await requestListening(action, credential, request.signal)
      if (request.signal.aborted) return
      token.current = credential
      applyStatus(next)
      setTokenAction(null)
      setDraftToken('')
      refresh()
    } catch (reason) {
      if (request.signal.aborted) return
      setControlError(reason instanceof Error ? reason.message : '监听操作失败，请重试。')
      if (reason instanceof ControlError && reason.status === 401) {
        token.current = ''
        setDraftToken('')
        setTokenAction(action)
      }
    } finally {
      if (!request.signal.aborted) setControlPending(false)
    }
  }, [applyStatus, refresh])

  const operate = () => {
    if (control.action === 'start' && (!status?.device.address || connectionConfigured === false)) { void openConnectionSettings(); return }
    if (status?.control_requires_token && !token.current) { setControlError(null); setTokenAction(control.action); return }
    void runControl(control.action, token.current)
  }
  const submitToken = () => { if (tokenAction && draftToken.trim() && !controlPending) void runControl(tokenAction, draftToken.trim()) }
  const closeToken = () => { if (!controlPending) { setTokenAction(null); setDraftToken('') } }
  const toggleFollow = () => {
    const next = !follow
    saveFollowPreference(next)
    setFollow(next)
  }
  const stopped = Boolean(status) && (state === 'stopped' || state === 'stopping')

  return <div className="monitor-shell">
    <header className="app-header"><div className="app-title"><IconWifi /><h1>DMR 通联监控</h1><span className="product-name">Starwave</span></div><div className="header-tools"><Clock /><Tooltip content={theme === 'light' ? '切换深色' : '切换浅色'}><Button type="text" size="small" aria-label={theme === 'light' ? '切换深色' : '切换浅色'} icon={theme === 'light' ? <IconMoon /> : <IconSun />} onClick={() => setTheme((current) => current === 'light' ? 'dark' : 'light')} /></Tooltip><Button type="text" size="small" icon={<IconSettings />} loading={connectionOpening} onClick={() => { void openConnectionSettings() }}>连接设置</Button><Button type="text" size="small" icon={<IconTranslate />} onClick={() => setTranslationOpen(true)}>翻译设置</Button><Button type="text" size="small" icon={<IconInfoCircle />} onClick={() => setDetails(true)}>链路详情</Button></div></header>
    <main className="monitor-main">
      <div className="monitor-toolbar" aria-label="监听与显示控制">
        <Button className="listen-control" title="监听选择在服务重启后保留" type={control.action === 'stop' ? 'secondary' : 'primary'} size="small" disabled={!status || control.disabled} loading={controlPending || state === 'starting' || state === 'stopping'} icon={control.action === 'stop' ? <IconRecordStop /> : <IconPlayArrow />} onClick={operate}>{control.label}</Button>
        <Button className="follow-button" type={follow ? 'secondary' : 'outline'} size="small" icon={follow ? <IconPause /> : <IconPlayArrow />} onClick={toggleFollow}>{follow ? '暂停追踪' : '恢复追踪'}</Button>
        <Button className="refresh-button" size="small" icon={<IconRefresh />} loading={refreshing} onClick={refresh}>刷新</Button>
        <div className="toolbar-divider" />
        <Input className="call-search" size="small" aria-label="搜索通联记录" placeholder="呼号 / ID / TG / 内容" prefix={<IconSearch />} allowClear value={query} onChange={setQuery} />
        <Select className="call-filter" size="small" aria-label="筛选通话" value={filter} onChange={setFilter} options={[{ value: 'all', label: '全部通话' }, { value: 'text', label: '已有文字' }, { value: 'processing', label: '处理中' }, { value: 'failed', label: '处理失败' }]} />
        <div className="text-options"><Checkbox checked={text.original} onChange={(checked) => setText((previous) => ({ ...previous, original: checked }))}>原文</Checkbox><Checkbox checked={text.translated} onChange={(checked) => setText((previous) => ({ ...previous, translated: checked }))}>中文</Checkbox></div>
      </div>
      <div className="link-bar" aria-label="站点与链路状态"><div className="site-identity"><strong>{status?.device.name || '读取站点'}</strong><span>{status?.device.address || '地址未配置'}</span>{status?.device.capture_source && <span>{status.device.capture_source}</span>}</div><div className="pipeline-status"><span className={receptionOnline ? 'status-ok' : state === 'stopped' ? 'status-idle' : 'status-warning'}><i />{label}</span><span>识别 <strong>{stopped ? '停止' : ai?.transcription === 'paused' ? '暂停' : ai?.transcription === 'loading' ? '准备中' : !ai || ai.transcription === 'unavailable' ? '不可用' : (ai.processing_transcription || 0) > 0 ? '处理中' : '就绪'}</strong>{!stopped && ai?.pending_transcription !== undefined && <small>待处理 {ai.pending_transcription}</small>}</span><span>翻译 <strong>{ai?.translation === 'disabled' ? '未启用' : stopped ? '停止' : ai?.translation !== 'ready' ? '不可用' : (ai.processing_translation || 0) > 0 ? '处理中' : '就绪'}</strong>{!stopped && ai?.pending_translation !== undefined && <small>待处理 {ai.pending_translation}</small>}</span></div></div>
      {stream === 'reconnecting' && !notice && <div className="connection-notice" role="status">实时更新连接中断，正在自动恢复。已有日志和录音仍可使用。<Button type="text" size="mini" onClick={refresh}>重新同步</Button></div>}
      {notice && <div className="error-notice" role="alert"><IconInfoCircle /><span>{notice}</span><Button type="text" size="mini" onClick={refresh}>刷新状态</Button></div>}
      <div className="mobile-slot-tabs" role="tablist" aria-label="选择时隙">{([1, 2] as const).map((slot) => <button key={slot} role="tab" aria-selected={mobileSlot === slot} aria-controls={`slot-panel-${slot}`} className={mobileSlot === slot ? 'selected' : ''} onClick={() => setMobileSlot(slot)}>TS{slot}<span>TG {status?.slots.find((item) => item.slot === slot)?.tg || '—'}</span></button>)}</div>
      <div className="monitor-workspace"><div className="slots-grid">
        <SlotPanel slot={1} status={status?.slots.find((slot) => slot.slot === 1)} calls={slotOne} capture={status?.capture_state} listening={state} stream={stream} follow={follow} selected={mobileSlot === 1} query={search} filter={filter} text={text} ready={ready} loading={refreshing} />
        <SlotPanel slot={2} status={status?.slots.find((slot) => slot.slot === 2)} calls={slotTwo} capture={status?.capture_state} listening={state} stream={stream} follow={follow} selected={mobileSlot === 2} query={search} filter={filter} text={text} ready={ready} loading={refreshing} />
      </div><aside className="diagnostics-sidebar" aria-label="链路诊断"><Diagnostics status={status} stream={stream} /></aside></div>
      <footer className="app-footer"><span>{!status ? '等待服务连接' : state === 'stopped' ? '监听已停止，历史记录仍可回放' : `${calls.length} 条近期有效通话`}</span><span>原文、译文及摘要为自动处理结果</span></footer>
    </main>
    <Modal className="diagnostic-dialog" title="链路详情" visible={details} onCancel={() => setDetails(false)} footer={null} style={{ width: 'min(440px, calc(100vw - 24px))' }}><Diagnostics status={status} stream={stream} /></Modal>
    <ConnectionDialog visible={connectionOpen} onClose={closeConnectionSettings} state={state} requiresToken={Boolean(status?.control_requires_token)} getToken={getToken} rememberToken={rememberToken} onSaved={connectionSaved} />
    <TranslationDialog visible={translationOpen} onClose={closeTranslationSettings} state={state} requiresToken={Boolean(status?.control_requires_token)} getToken={getToken} rememberToken={rememberToken} onSaved={translationSaved} />
    <Modal className="control-dialog" title={tokenAction === 'stop' ? '停止监听' : '开始监听'} visible={Boolean(tokenAction)} onCancel={closeToken} style={{ width: 'min(400px, calc(100vw - 24px))' }}
      footer={<><Button size="small" disabled={controlPending} onClick={closeToken}>取消</Button><Button type="primary" size="small" disabled={!draftToken.trim()} loading={controlPending} onClick={submitToken}>确认{tokenAction === 'stop' ? '停止' : '开始'}</Button></>}>
      <p className="credential-help">此站点需要控制凭据。凭据仅用于当前页面，关闭页面后清除。</p>
      <Input.Password autoFocus aria-label="控制凭据" autoComplete="off" maxLength={4096} placeholder="输入控制凭据" value={draftToken} onChange={setDraftToken} onKeyDown={(event) => { if (event.key === 'Enter') submitToken() }} />
      {controlError && <p className="control-error" role="alert">{controlError}</p>}
    </Modal>
  </div>
}
