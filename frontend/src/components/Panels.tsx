import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { CleanupPreview, Counts, Operation, Plan, Track, TransferMode } from '../api'

/** Move or copy. Copy never touches the original files: the library you started from is preserved. */
export function TransferPicker({ mode, onChange }: { mode: TransferMode; onChange: (m: TransferMode) => void }) {
  return (
    <fieldset className="transfer">
      <label className="check">
        <input type="radio" name="transfer" checked={mode === 'move'} onChange={() => onChange('move')} />{' '}
        <strong>Move</strong> <span className="muted">· originals leave the input folder</span>
      </label>
      <label className="check">
        <input type="radio" name="transfer" checked={mode === 'copy'} onChange={() => onChange('copy')} />{' '}
        <strong>Copy</strong> <span className="muted">· originals are never touched (uses more disk space)</span>
      </label>
    </fieldset>
  )
}

export function TracksPanel({ jobId, counts }: { jobId: string; counts: Counts }) {
  const [status, setStatus] = useState('')
  const [rows, setRows] = useState<Track[]>([])
  const [total, setTotal] = useState(0)

  useEffect(() => {
    api.tracks(jobId, status || undefined).then((r) => (setRows(r.items), setTotal(r.total)))
  }, [jobId, status, counts])

  return (
    <div className="stack">
      <div className="chips">
        <button className={status === '' ? 'primary' : ''} onClick={() => setStatus('')}>All</button>
        {Object.entries(counts).map(([s, n]) => (
          <button key={s} className={status === s ? 'primary' : ''} onClick={() => setStatus(s)}>
            {s} ({n})
          </button>
        ))}
        <span className="spacer" />
        <a href={api.reportUrl(jobId)} download>Download CSV</a>
      </div>
      <table className="grid">
        <thead>
          <tr><th>Status</th><th>Local title</th><th>Matched</th><th>Album</th><th>#</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.source} title={r.source}>
              <td><span className={`pill ${r.status}`}>{r.status}</span></td>
              <td>{r.local_title}</td>
              <td>{r.matched_title}</td>
              <td>{r.matched_album}</td>
              <td>{r.track_number}</td>
              <td className="muted">{r.detail_status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > rows.length && <p className="muted">Showing {rows.length} of {total}.</p>}
    </div>
  )
}

interface TransferProps {
  transfer: TransferMode
  onTransfer: (m: TransferMode) => void
}

export function PlanPanel({
  jobId, version, onChanged, transfer, onTransfer,
}: { jobId: string; version: number; onChanged: () => void } & TransferProps) {
  const verb = transfer === 'copy' ? 'Copy' : 'Move'
  const [plan, setPlan] = useState<Plan | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.plan(jobId).then(setPlan).catch((e: Error) => setError(e.message))
  }, [jobId, version])

  const apply = async () => {
    const note = transfer === 'copy' ? 'The originals will not be touched.' : 'The originals will leave the input folder.'
    if (!plan || !window.confirm(`${verb} ${plan.will_move} file(s) into the output folder?\n\n${note}\nYou can undo this afterwards.`)) return
    setBusy(true)
    try {
      const r = await api.apply(jobId, transfer)
      setMessage('Done: ' + JSON.stringify(r.results))
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!plan) return <p className="muted">{error || 'Loading…'}</p>
  return (
    <div className="stack">
      <div className="toolbar">
        <span>
          <strong>{plan.will_move}</strong> will be {transfer === 'copy' ? 'copied' : 'moved'} · <strong>{plan.stay}</strong> stay in place{' '}
          <span className="muted">{Object.entries(plan.stay_by_status).map(([k, v]) => `${k}: ${v}`).join(', ')}</span>
        </span>
        <span className="spacer" />
        <button className="primary" disabled={busy || plan.will_move === 0} onClick={apply}>
          {verb} {plan.will_move} file(s)
        </button>
      </div>
      <TransferPicker mode={transfer} onChange={onTransfer} />
      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}
      <p className="muted">This is a preview. Nothing is {transfer === 'copy' ? 'copied' : 'moved'} until you press the button.</p>
      <table className="grid">
        <thead><tr><th>From</th><th>To</th><th></th></tr></thead>
        <tbody>
          {plan.ops.map((o) => (
            <tr key={o.source}>
              <td className="path">{o.source}</td>
              <td className="path">{o.dest}</td>
              <td>
                {o.collision && <span className="pill REVIEW">name taken → renamed</span>}
                {o.problem && <span className="pill ERROR">{o.problem}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Final step: remove what is left behind in the INPUT folder after the songs were moved out. */
export function CleanupPanel({ jobId, version, onChanged }: { jobId: string; version: number; onChanged: () => void }) {
  const [preview, setPreview] = useState<CleanupPreview | null>(null)
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api
      .cleanupPreview(jobId)
      .then((p) => {
        setPreview(p)
        setTicked(new Set(p.dirs.filter((d) => d.default_selected && (d.kind === 'empty' || p.trash_available)).map((d) => d.path)))
      })
      .catch((e: Error) => setError(e.message))
  }, [jobId])

  useEffect(() => {
    load()
  }, [load, version])

  const toggle = (path: string) =>
    setTicked((t) => {
      const n = new Set(t)
      if (n.has(path)) n.delete(path)
      else n.add(path)
      return n
    })

  const run = async () => {
    if (!preview) return
    const chosen = preview.dirs.filter((d) => ticked.has(d.path))
    const empty = chosen.filter((d) => d.kind === 'empty').length
    const left = chosen.length - empty
    const text =
      `Remove ${chosen.length} folder(s) from the input folder?\n\n` +
      `• ${empty} empty folder(s) will be deleted\n• ${left} folder(s) with leftover files will go to the Recycle Bin`
    if (!window.confirm(text)) return
    setBusy(true)
    setError('')
    try {
      const r = await api.cleanup(jobId, chosen.map((d) => d.path))
      setMessage(
        `Removed ${r.removed_empty} empty folder(s), sent ${r.trashed} to the Recycle Bin.` +
          (r.skipped.length ? ` Skipped ${r.skipped.length}: ${r.skipped.map((s) => s.reason).join('; ')}` : ''),
      )
      load()
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!preview) return <p className="muted">{error || 'Loading…'}</p>
  if (preview.copied)
    return (
      <p className="muted">
        This job <strong>copied</strong> your files, so nothing was removed from the input folder and there is nothing
        to clean up. Your original library is exactly as it was.
      </p>
    )
  return (
    <div className="stack">
      <p className="muted">
        Folders in the <strong>input</strong> folder that are now empty or hold only leftovers (notes, thumbnails, cover
        images). Folders that still contain songs, videos or any other files are never listed, and nothing outside the
        input folder is touched. Empty folders are deleted; folders with leftovers go to the Recycle Bin, so they can be
        restored.
      </p>
      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}
      {!preview.trash_available && preview.dirs.some((d) => d.kind === 'leftovers') && (
        <p className="error">Recycle Bin support is not installed, so folders with leftovers cannot be removed.</p>
      )}
      {preview.dirs.length === 0 ? (
        <p className="muted">Nothing to clean up. 🎉</p>
      ) : (
        <>
          <div className="toolbar">
            <span>{ticked.size} of {preview.dirs.length} selected</span>
            <span className="spacer" />
            <button className="danger" disabled={busy || ticked.size === 0} onClick={() => void run()}>
              Remove {ticked.size} folder(s)
            </button>
          </div>
          <ul className="cleanlist">
            {preview.dirs.map((d) => {
              const blocked = d.kind === 'leftovers' && !preview.trash_available
              return (
                <li key={d.path} className="card">
                  <label>
                    <input type="checkbox" disabled={blocked} checked={ticked.has(d.path)} onChange={() => toggle(d.path)} />{' '}
                    <span className={`pill ${d.kind === 'empty' ? 'done' : 'REVIEW'}`}>{d.kind}</span>{' '}
                    <span className="path">{d.path}</span>
                  </label>
                  {d.empty_subfolders > 0 && <small className="muted"> · includes {d.empty_subfolders} empty sub-folder(s)</small>}
                  {d.files.length > 0 && (
                    <small className="muted"> · {d.files.join(', ')}</small>
                  )}
                  {d.has_images && <small className="muted"> · contains cover images, so not ticked by default</small>}
                </li>
              )
            })}
          </ul>
        </>
      )}
    </div>
  )
}

export function HistoryPanel({
  jobId, version, onChanged, appliedMode,
}: { jobId: string; version: number; onChanged: () => void; appliedMode?: string }) {
  const [ops, setOps] = useState<Operation[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(() => api.operations(jobId).then(setOps), [jobId])
  useEffect(() => {
    void load()
  }, [load, version])

  const undo = async () => {
    const text =
      appliedMode === 'copy'
        ? 'Delete the copies this job made? Your originals are not touched. Copies you have edited since are left alone.'
        : 'Move every file from this job back to where it came from and restore its original tags?'
    if (!window.confirm(text)) return
    try {
      const r = await api.undo(jobId)
      setMessage(`${appliedMode === 'copy' ? 'Removed' : 'Restored'} ${r.undone} file(s).` + (r.skipped.length ? ` Skipped ${r.skipped.length}: ${r.skipped.map((s) => s.reason).join('; ')}` : ''))
      await load()
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="stack">
      <div className="toolbar">
        <span>{ops.length} recorded operation(s)</span>
        <span className="spacer" />
        <button className="danger" disabled={!ops.some((o) => o.status === 'done')} onClick={undo}>{appliedMode === 'copy' ? 'Undo (delete the copies)' : 'Undo this job'}</button>
      </div>
      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}
      <table className="grid">
        <thead><tr><th>Status</th><th>Type</th><th>From</th><th>To</th></tr></thead>
        <tbody>
          {ops.map((o) => (
            <tr key={o.id}>
              <td><span className={`pill ${o.status}`}>{o.status}</span></td>
              <td>{o.op_type}</td>
              <td className="path">{o.source}</td>
              <td className="path">{o.dest}{o.error && <span className="error"> {o.error}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
