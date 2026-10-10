import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@arco-design/web-react'
import { IconArrowDown } from '@arco-design/web-react/icon'
import CallCard from './CallCard'
import type { TextPreferences } from './CallCard'
import { dayKey, dayLabel, directionLabel, matchesSearch } from './data'
import type { ListeningState, RadioCall, RadioStatus, SlotStatus } from './data'
import { currentCall, emptyFeedView, initialTracking, matchesFilter, updateTracking } from './monitorState'
import type { CallFilter, StreamState } from './monitorState'

export default function SlotPanel({ slot, status, calls, capture, listening, stream, follow, selected, query, filter, text, ready, loading }: {
  slot: 1 | 2; status?: SlotStatus; calls: RadioCall[]; capture?: RadioStatus['capture_state']; listening: ListeningState;
  stream: StreamState; follow: boolean; selected: boolean; query: string; filter: CallFilter; text: TextPreferences; ready: boolean; loading: boolean
}) {
  const feed = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [tracking, setTracking] = useState(initialTracking)
  const initializedScroll = useRef(false)
  const visible = useMemo(() => calls.filter((call) => matchesSearch(call, query) && matchesFilter(call, filter)), [calls, query, filter])
  const activeCall = currentCall(calls, status)
  const connected = stream === 'connected' && capture === 'online' && listening === 'running'
  const active = connected && status?.active && activeCall
  const following = follow && atBottom

  const toBottom = () => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight
    setAtBottom(true)
    setTracking((previous) => ({ ...previous, unreadIds: [] }))
  }

  useEffect(() => { setTracking((previous) => updateTracking(previous, calls, following, ready)) }, [calls, following, ready])
  useLayoutEffect(() => {
    if (visible.length && (!initializedScroll.current || following)) {
      if (feed.current) feed.current.scrollTop = feed.current.scrollHeight
      initializedScroll.current = true
    }
  }, [visible, following, selected])
  useEffect(() => { if (follow) toBottom() }, [follow])

  const unavailable = stream !== 'connected'
  const empty = emptyFeedView({ ready, loading, stream, listening, capture, filtered: Boolean(query || filter !== 'all') })

  return <section className={`slot-panel ${selected ? 'mobile-selected' : ''}`} id={`slot-panel-${slot}`} aria-labelledby={`slot-title-${slot}`}>
    <header className="slot-header"><div><h2 id={`slot-title-${slot}`}>TS{slot}</h2><strong className="slot-tg">{status?.tg && status.tg > 0 ? `TG ${status.tg}` : 'TG —'}</strong>{status?.label && <span className="channel-label">{status.label}</span>}</div><span className={`slot-presence ${active ? 'active' : ''}`}><i />{unavailable ? '未连接' : listening === 'stopped' ? '停止' : active ? `${directionLabel(activeCall)} ${activeCall.callsign || activeCall.source_id}` : connected ? '守听' : '未连接'}</span></header>
    <div className="log-row log-columns" aria-hidden="true"><span>时间</span><span>呼号 / DMR ID</span><span>方向</span><span className="log-tg">TG</span><span>时长</span><span className="log-state">处理状态</span><span /></div>
    <div className="slot-feed" ref={feed} role="log" aria-label={`时隙 ${slot} 通联日志`} aria-live="off" onScroll={() => {
      const element = feed.current
      if (element) setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight < 24)
    }}>
      {visible.length ? visible.map((call, index) => <Fragment key={call.id}>
        {(index === 0 || dayKey(call.started_at) !== dayKey(visible[index - 1].started_at)) && <div className="date-divider">{dayLabel(call.started_at)}</div>}
        <CallCard call={call} text={text} />
      </Fragment>) : <div className="empty-state"><strong>{empty.title}</strong><p>{empty.description}</p></div>}
    </div>
    <footer className="slot-footer"><span>{!ready ? loading ? '同步中' : '同步失败' : query || filter !== 'all' ? `${visible.length} / ${calls.length} 条` : `${calls.length} 条`}</span><span className="feed-follow-state">{following ? '追踪最新' : '追踪暂停'}</span>{(!atBottom || tracking.unreadIds.length > 0) && <Button size="mini" type={tracking.unreadIds.length ? 'primary' : 'secondary'} icon={<IconArrowDown />} onClick={toBottom}>{tracking.unreadIds.length ? `${tracking.unreadIds.length} 条新通话` : '跳到最新'}</Button>}</footer>
  </section>
}
