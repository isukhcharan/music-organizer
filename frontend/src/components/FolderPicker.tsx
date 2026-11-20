import { useState } from 'react'
import { api } from '../api'
import type { DirListing } from '../api'

interface Props {
  label: string
  value: string
  onChange: (path: string) => void
  hint?: string
}

/**
 * Text box + "Choose folder…", which opens the operating system's own folder dialog (via the local backend).
 * If that is unavailable, an in-app folder browser (directories only) is used instead.
 */
export function FolderPicker({ label, value, onChange, hint }: Props) {
  const [busy, setBusy] = useState(false)
  const [fallback, setFallback] = useState(false)
  const [listing, setListing] = useState<DirListing | null>(null)
  const [error, setError] = useState('')

  const go = (path: string) =>
    api
      .listDirs(path)
      .then((l) => (setListing(l), setError('')))
      .catch((e: Error) => setError(e.message))

  const choose = async () => {
    setBusy(true)
    setError('')
    try {
      const r = await api.pickFolder(value, label)
      if (r.path) onChange(r.path)
    } catch {
      // no native dialog on this system: use the in-app browser
      setFallback(true)
      void go(value || '')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="field">
      <label>{label}</label>
      <div className="row">
        <input value={value} onChange={(e) => onChange(e.target.value)} placeholder="Choose a folder, or paste a path…" />
        <button type="button" className="primary" disabled={busy} onClick={() => void choose()}>
          {busy ? 'Waiting for dialog…' : 'Choose folder…'}
        </button>
      </div>
      {hint && <small className="muted">{hint}</small>}
      {busy && <small className="muted">A folder window should have opened. It may be behind this browser window.</small>}
      {fallback && (
        <div className="browser">
          <div className="browser-head">
            <button type="button" disabled={listing?.parent == null} onClick={() => void go(listing?.parent ?? '')}>
              ↑ Up
            </button>
            <code>{listing?.path || 'This computer'}</code>
            {listing?.path && (
              <button
                type="button"
                className="primary"
                onClick={() => {
                  onChange(listing.path)
                  setFallback(false)
                }}
              >
                Use this folder
              </button>
            )}
            <button type="button" onClick={() => setFallback(false)}>Close</button>
          </div>
          {listing?.audio_files_here ? <small className="muted">{listing.audio_files_here} audio file(s) here</small> : null}
          {error && <p className="error">{error}</p>}
          <ul>
            {listing?.dirs.map((d) => (
              <li key={d.path}>
                <button type="button" className="link" onClick={() => void go(d.path)}>
                  📁 {d.name}
                </button>
              </li>
            ))}
            {listing && listing.dirs.length === 0 && <li className="muted">No sub-folders</li>}
          </ul>
        </div>
      )}
    </div>
  )
}
