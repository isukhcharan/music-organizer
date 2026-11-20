import { useEffect, useState } from 'react'
import { api } from '../api'
import type { RepairRow, RepairStatus } from '../api'
import { FolderPicker } from './FolderPicker'

const FILL_LABEL: Record<string, string> = { TPE2: 'album artist', TRCK: 'track #', TDRC: 'year' }

/** Fix tags of files that stay where they are (e.g. an existing library). Preview first, then apply, with undo. */
export function RepairView() {
  const [folder, setFolder] = useState('')
  const [itunes, setItunes] = useState(true)
  const [fallback, setFallback] = useState(false)
  const [id, setId] = useState<string | null>(null)
  const [status, setStatus] = useState<RepairStatus | null>(null)
  const [rows, setRows] = useState<RepairRow[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // poll while the preview is being prepared
  useEffect(() => {
    if (!id) return
    let stop = false
    const tick = async () => {
      try {
        const s = await api.repair(id)
        if (stop) return
        setStatus(s)
        if (s.state === 'ready' || s.state === 'applied') setRows((await api.repairChanges(id)).items)
        else if (s.state !== 'failed') setTimeout(tick, 1000)
      } catch (e) {
        if (!stop) setError((e as Error).message)
      }
    }
    void tick()
    return () => {
      stop = true
    }
  }, [id])

  const start = async () => {
    setBusy(true)
    setError('')
    setMessage('')
    setStatus(null)
    setRows([])
    try {
      setId((await api.repairStart(folder, itunes, fallback)).id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    if (!id || !status) return
    const n = status.summary.will_change
    if (!window.confirm(`Change the tags of ${n} file(s) in place?\n\n${status.folder}\n\nYou can undo this afterwards.`)) return
    setBusy(true)
    try {
      const r = await api.repairApply(id)
      setMessage('Done: ' + JSON.stringify(r.results))
      setStatus(await api.repair(id))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const undo = async () => {
    if (!id || !window.confirm('Restore every tag changed by this repair?')) return
    try {
      const r = await api.repairUndo(id)
      setMessage(`Restored ${r.undone} file(s).`)
      setStatus(await api.repair(id))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const working = status && (status.state === 'scanning' || status.state === 'planning')
  const sm = status?.summary
  const pct = status && status.progress.total ? Math.round((status.progress.done / status.progress.total) * 100) : 0

  return (
    <div className="stack">
      <section className="card">
        <h2>Repair tags</h2>
        <p className="muted">
          For music that is already organised (for example your Navidrome library). Removes junk such as the
          comment <code>latest</code> or <code>Downloaded from DjPunjab.Com</code>, clears junk genres, and fills blank
          genre, album artist, track number and year where it can tell. Files are not moved, and nothing is
          written until you apply.
        </p>
        <FolderPicker label="Music folder to repair" value={folder} onChange={setFolder} />
        <div className="field">
          <label>
            <input type="checkbox" checked={itunes} onChange={(e) => setItunes(e.target.checked)} /> Look up each
            song&apos;s genre on iTunes
          </label>
          <small className="muted">
            Slow: about 3 seconds per song, because of iTunes&apos; rate limit. Answers are cached, so repeat runs are fast.
            Without it, only junk is cleaned.
          </small>
          <label>
            <input type="checkbox" checked={fallback} onChange={(e) => setFallback(e.target.checked)} /> Also fill blank
            genres with the artist&apos;s usual genre
          </label>
          <small className="muted">Less accurate: every song by an artist gets the same genre, including sad songs tagged Bhangra.</small>
        </div>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy || !folder || !!working} onClick={() => void start()}>
          Preview changes
        </button>
      </section>

      {working && (
        <section className="card">
          <strong>{status.state === 'scanning' ? 'Reading tags…' : 'Working out changes…'}</strong>
          <div className="bar"><div style={{ width: `${pct}%` }} /></div>
          <small className="muted">{status.progress.done}/{status.progress.total} files</small>
        </section>
      )}
      {status?.state === 'failed' && <p className="error">Failed: {status.error}</p>}

      {sm && (status.state === 'ready' || status.state === 'applied') && (
        <>
          <section className="card">
            <div className="toolbar">
              <span>
                <strong>{sm.will_change}</strong> of {sm.files} files {status.state === 'applied' ? 'were changed' : 'would change'}
              </span>
              <span className="spacer" />
              {status.state === 'ready' && (
                <button className="primary" disabled={busy || sm.will_change === 0} onClick={() => void apply()}>
                  Change {sm.will_change} file(s)
                </button>
              )}
              {status.state === 'applied' && <button className="danger" onClick={() => void undo()}>Undo this repair</button>}
            </div>
            <div className="chips">
              <span className="pill">junk comments removed: {sm.junk_comments}</span>
              <span className="pill">junk genres cleared: {sm.junk_genres_cleared}</span>
              <span className="pill">genres filled: {sm.genres_filled}</span>
              <span className="pill">genres replaced: {sm.genres_overridden}</span>
              <span className="pill">album artist: {sm.album_artist_filled}</span>
              <span className="pill">track #: {sm.track_number_filled}</span>
              <span className="pill">year: {sm.year_filled}</span>
            </div>
            {message && <p className="ok">{message}</p>}
            {status.skipped > 0 && <p className="muted">{status.skipped} non-mp3 or unreadable file(s) are left untouched.</p>}
          </section>
          <table className="grid">
            <thead><tr><th>File</th><th>Comment removed</th><th>Genre</th><th>Filled</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.path}>
                  <td className="path">{r.path}</td>
                  <td>{r.removed_comments.join(', ')}</td>
                  <td>
                    {r.old_genre.join(' / ') || <span className="blank">(none)</span>}
                    {(r.new_genre.length > 0 || r.clear_genre) && (
                      <>
                        {' → '}
                        {r.new_genre.length ? <strong>{r.new_genre.join(' / ')}</strong> : <span className="blank">cleared</span>}
                        {r.overrides && <span className="pill REVIEW"> replaced</span>}
                      </>
                    )}
                  </td>
                  <td>{Object.entries(r.fills).map(([k, v]) => `${FILL_LABEL[k] ?? k}: ${v}`).join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
