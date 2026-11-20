import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Job, TransferMode } from '../api'
import { CleanupPanel, HistoryPanel, PlanPanel, TracksPanel } from './Panels'
import { ReviewPanel } from './ReviewPanel'

type Tab = 'review' | 'tracks' | 'plan' | 'cleanup' | 'history'

/** Live job state: WebSocket pushes while scanning; `refresh` re-reads after the user changes something. */
function useJob(id: string) {
  const [job, setJob] = useState<Job | null>(null)
  const [version, setVersion] = useState(0)
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    api.job(id).then((j) => (setJob(j), setVersion((v) => v + 1))).catch((e: Error) => setError(e.message))
  }, [id])

  useEffect(() => {
    const ws = new WebSocket(api.wsUrl(id))
    ws.onmessage = (m) => setJob(JSON.parse(m.data) as Job)
    ws.onclose = () => refresh()
    ws.onerror = () => refresh()
    return () => ws.close()
  }, [id, refresh])

  return { job, version, refresh, error }
}

/** Move/copy choice, remembered between sessions (storage may be unavailable, so it is optional). */
function useTransferMode(): [TransferMode, (m: TransferMode) => void] {
  const [mode, setMode] = useState<TransferMode>(() => {
    try {
      return localStorage.getItem('transferMode') === 'copy' ? 'copy' : 'move'
    } catch {
      return 'move'
    }
  })
  const set = (m: TransferMode) => {
    setMode(m)
    try {
      localStorage.setItem('transferMode', m)
    } catch {
      /* ignore */
    }
  }
  return [mode, set]
}

export function JobView({ id }: { id: string }) {
  const { job, version, refresh, error } = useJob(id)
  const [tab, setTab] = useState<Tab>('review')
  const [transfer, setTransfer] = useTransferMode()

  if (error) return <p className="error">{error}</p>
  if (!job) return <p className="muted">Loading…</p>

  const p = job.progress
  const scanning = job.state === 'scanning'
  const pct = p && p.total ? Math.round((p.done / p.total) * 100) : 0
  const pending = job.pending_review ?? 0

  return (
    <div className="stack">
      <section className="card">
        <div className="row">
          <div>
            <strong>{job.input_dir}</strong> <span className="muted">→</span> <strong>{job.output_dir}</strong>
          </div>
          <span className="spacer" />
          <span className={`pill ${job.state}`}>{job.state}</span>
        </div>
        {scanning && (
          <>
            <div className="bar"><div style={{ width: `${pct}%` }} /></div>
            <small className="muted">{p?.done}/{p?.total} · {p?.label}</small>
          </>
        )}
        {job.state === 'failed' && <p className="error">Scan failed: {p?.error}</p>}
        <div className="chips">
          {Object.entries(job.counts).map(([s, n]) => (
            <span key={s} className={`pill ${s}`}>{s}: {n}</span>
          ))}
        </div>
      </section>

      {!scanning && job.state !== 'failed' && (
        <>
          <nav className="tabs">
            <button className={tab === 'review' ? 'active' : ''} onClick={() => setTab('review')}>
              Review {pending > 0 && <span className="badge">{pending}</span>}
            </button>
            <button className={tab === 'tracks' ? 'active' : ''} onClick={() => setTab('tracks')}>All tracks</button>
            <button className={tab === 'plan' ? 'active' : ''} onClick={() => setTab('plan')}>Plan &amp; apply</button>
            <button className={tab === 'cleanup' ? 'active' : ''} onClick={() => setTab('cleanup')}>Finish &amp; clean up</button>
            <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>History &amp; undo</button>
          </nav>
          {tab === 'review' && <ReviewPanel jobId={id} onChanged={refresh} transfer={transfer} onTransfer={setTransfer} />}
          {tab === 'tracks' && <TracksPanel jobId={id} counts={job.counts} />}
          {tab === 'plan' && <PlanPanel jobId={id} version={version} onChanged={refresh} transfer={transfer} onTransfer={setTransfer} />}
          {tab === 'cleanup' && <CleanupPanel jobId={id} version={version} onChanged={refresh} />}
          {tab === 'history' && <HistoryPanel jobId={id} version={version} onChanged={refresh} appliedMode={job.applied_mode} />}
        </>
      )}
    </div>
  )
}
