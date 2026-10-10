import { memo, useEffect, useRef, useState } from 'react'
import { Button } from '@arco-design/web-react'
import { IconDown, IconPause, IconPlayArrow, IconRight } from '@arco-design/web-react/icon'
import { audioUrl, countryFlag, directionLabel, durationLabel, isVisibleCall, timeLabel } from './data'
import type { CallState, RadioCall } from './data'

const stateLabels: Record<CallState, string> = {
  recording: '接收中', queued: '待识别', transcribing: '识别中', translating: '翻译中', translation_pending: '待翻译',
  transcribed: '已转写', complete: '完成', failed: '处理失败', short: '', discarded: '',
}

function RecordingPlayer({ call }: { call: RadioCall }) {
  const audio = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [length, setLength] = useState(call.duration)
  const [error, setError] = useState(false)
  const [seekable, setSeekable] = useState(false)
  const source = audioUrl(call.audio_url)
  useEffect(() => { setLength(call.duration) }, [call.duration])
  useEffect(() => {
    const element = audio.current
    return () => element?.pause()
  }, [source])
  if (!source) return null

  const toggle = async () => {
    const element = audio.current
    if (!element) return
    if (playing) { element.pause(); return }
    setError(false)
    document.querySelectorAll('audio').forEach((other) => { if (other !== element) other.pause() })
    try { await element.play() } catch { setError(true) }
  }

  return <div className="recording-player">
    <Button size="small" className="play-button" aria-label={`${playing ? '暂停' : '播放'} ${call.callsign || call.source_id} 的录音`}
      icon={playing ? <IconPause /> : <IconPlayArrow />} onClick={() => { void toggle() }}>{playing ? '暂停' : '回放'}</Button>
    <input className="audio-progress" type="range" min={0} max={Math.max(length, 1)} step={0.1}
      disabled={!seekable} value={Math.min(position, Math.max(length, 1))} aria-label={`${call.callsign || call.source_id} 录音播放进度`}
      onChange={(event) => {
        if (audio.current && Number.isFinite(audio.current.duration)) {
          const next = Number(event.target.value)
          audio.current.currentTime = next
          setPosition(next)
        }
      }} />
    <span className="player-time">{durationLabel(position)} / {durationLabel(length)}</span>
    {error && <span className="audio-error" role="status">录音读取失败，可重试</span>}
    <audio ref={audio} src={source} preload="none" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
      onEnded={() => setPlaying(false)} onError={() => { setError(true); setPlaying(false) }}
      onLoadedMetadata={() => {
        if (audio.current && Number.isFinite(audio.current.duration)) { setLength(audio.current.duration); setSeekable(true) }
      }} onTimeUpdate={() => setPosition(audio.current?.currentTime || 0)} />
  </div>
}

function ActiveDuration({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return <>{durationLabel(now - startedAt)}</>
}

export interface TextPreferences { original: boolean; translated: boolean }

const CallCard = memo(function CallCard({ call, text }: { call: RadioCall; text: TextPreferences }) {
  const [expanded, setExpanded] = useState(true)
  if (!isVisibleCall(call)) return null
  const identity = call.callsign || String(call.source_id)
  const pending = ['recording', 'queued', 'transcribing', 'translating', 'translation_pending'].includes(call.status)
  const original = text.original && Boolean(call.original)
  const translated = text.translated && Boolean(call.translated)
  const direction = directionLabel(call)

  return <article className={`call-card state-${call.status}`} data-call-id={call.id} aria-label={`${identity} 的通联记录`}>
    <button className="log-row log-toggle" aria-expanded={expanded} aria-label={`${expanded ? '折叠' : '展开'} ${identity} 通话内容`} onClick={() => setExpanded((previous) => !previous)}>
      <time className="log-time" dateTime={new Date(call.started_at * 1000).toISOString()}>{timeLabel(call.started_at)}</time>
      <span className="log-station"><strong>{identity}</strong><span className="station-meta"><span>{call.source_id}</span>{call.country && <span className="country-tag">{/^[A-Za-z]{2}$/.test(call.country_code) && <span className="country-emoji" aria-hidden="true">{countryFlag(call.country_code)}</span>}{call.country}</span>}</span></span>
      <span className={`log-direction direction-${direction}`} title={direction === '—' ? '来源方向未记录' : direction === 'NET' ? '网络下行' : '射频上行'}>{direction}</span>
      <span className="log-tg">{call.tg}</span>
      <span className="log-duration">{call.status === 'recording' ? <ActiveDuration startedAt={call.started_at} /> : durationLabel(call.duration)}</span>
      <span className="log-state"><i />{stateLabels[call.status]}</span>
      <span className="log-chevron">{expanded ? <IconDown /> : <IconRight />}</span>
    </button>
    {expanded && <div className="call-detail">
      <div className="mobile-call-status"><span>TG {call.tg}{call.name ? ` · ${call.name}` : ''}</span><span>{stateLabels[call.status]}</span></div>
      {(original || translated) && <div className={`call-texts ${original && translated ? 'bilingual' : ''}`}>
        {original && <section className="original-text"><div className="text-heading"><span>原文{call.language ? ` · ${call.language.toUpperCase()}` : ''}</span><small>自动转写</small></div><p>{call.original}</p></section>}
        {translated && <section className="translated-text"><div className="text-heading"><span>中文</span><small>自动翻译</small></div><p>{call.translated}</p></section>}
      </div>}
      {pending && <p className="processing-note">{call.status === 'recording' ? '正在接收通话；结束后处理文字。' : call.status === 'queued' ? '等待语音识别。' : call.status === 'transcribing' ? '正在识别语音。' : '等待中文翻译。'}</p>}
      {call.status === 'transcribed' && text.translated && <p className="processing-note">当前仅转写，未生成译文。</p>}
      {call.status === 'failed' && <p className="call-error">{call.error || '文字处理失败，可回放录音。'}</p>}
      {call.topic && <details className="call-summary"><summary>内容摘要 <span>自动生成</span></summary><p>{call.topic}</p></details>}
      <RecordingPlayer call={call} />
    </div>}
  </article>
})

export default CallCard
