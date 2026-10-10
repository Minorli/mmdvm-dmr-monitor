import { useEffect, useState } from 'react'
import type { RadioStatus } from './data'
import { timeLabel } from './data'
import { listeningState } from './monitorState'
import type { StreamState } from './monitorState'

function PacketTime({ timestamp }: { timestamp: number | null | undefined }) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [])
  if (!timestamp) return <>尚未收到</>
  const elapsed = Math.max(0, Math.floor(now - timestamp))
  return <>{timeLabel(timestamp)} <small>{elapsed < 60 ? `${elapsed}s 前` : `${Math.floor(elapsed / 60)}min 前`}</small></>
}

export default function Diagnostics({ status, stream }: { status: RadioStatus | null; stream: StreamState }) {
  const state = listeningState(status)
  const ai = status?.ai
  const suspended = Boolean(status) && (state === 'stopped' || state === 'stopping')
  const asr = suspended ? '停止' : ai?.transcription === 'paused' ? '暂停' : ai?.transcription === 'loading' ? '准备中' : ai?.transcription === 'unavailable' || !ai ? '不可用' : (ai.processing_transcription || 0) > 0 ? '处理中' : '就绪'
  const translation = ai?.translation === 'disabled' ? '未启用' : suspended ? '停止' : ai?.translation !== 'ready' ? '不可用' : (ai.processing_translation || 0) > 0 ? '处理中' : '就绪'
  const elapsed = (seconds: number | null | undefined) => seconds === null || seconds === undefined ? '—' : `${seconds.toFixed(1)}s`

  return <div className="diagnostics">
    <section><h3>接收状态</h3><dl>
      <div><dt>实时更新</dt><dd className={stream === 'connected' ? 'text-success' : 'text-warning'}>{stream === 'connected' ? '已连接' : '连接恢复中'}</dd></div>
      <div><dt>设备链路</dt><dd>{stream !== 'connected' ? '状态未知' : suspended ? '停止' : status?.capture_state === 'online' ? '在线' : status?.capture_state === 'connecting' ? '连接中' : '断开'}</dd></div>
      <div className="packet-row"><dt>最近报文</dt><dd><PacketTime timestamp={status?.last_packet_at} /></dd></div>
    </dl></section>
    <section><h3>文字处理</h3><dl>
      <div><dt>语音识别</dt><dd>{asr}</dd></div><div><dt>中文翻译</dt><dd>{translation}</dd></div>
      <div><dt>待识别</dt><dd>{ai?.pending_transcription ?? '—'}</dd></div><div><dt>待翻译</dt><dd>{ai?.pending_translation ?? '—'}</dd></div>
      <div><dt>识别耗时</dt><dd>{elapsed(ai?.last_transcription_seconds)}</dd></div><div><dt>翻译耗时</dt><dd>{elapsed(ai?.last_translation_seconds)}</dd></div>
    </dl>{ai?.queues && <div className="queue-breakdown"><div><span>实时队列</span><span>{ai.queues.live.transcription} 识别 / {ai.queues.live.translation} 翻译</span></div><div><span>历史队列</span><span>{ai.queues.history.transcription} 识别 / {ai.queues.history.translation} 翻译</span></div></div>}{suspended && <p className="diagnostic-note">队列暂停，开始监听后继续处理。</p>}</section>
    <section><h3>站点</h3><dl><div><dt>名称</dt><dd>{status?.device.name || '—'}</dd></div><div><dt>地址</dt><dd>{status?.device.address || '未配置'}</dd></div><div><dt>来源</dt><dd>{status?.device.capture_source || '—'}</dd></div></dl></section>
    <section><h3>记录统计</h3><dl><div><dt>有效通话</dt><dd>{status?.counts.calls ?? '—'}</dd></div><div><dt>已有译文</dt><dd>{status?.counts.translated ?? '—'}</dd></div></dl></section>
  </div>
}
