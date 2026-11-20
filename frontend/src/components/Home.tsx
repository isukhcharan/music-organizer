import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Job } from '../api'
import { FolderPicker } from './FolderPicker'

const SOURCES = [
  { id: 'ytmusic', name: 'YouTube Music', note: '· best for Punjabi music, real track order' },
  { id: 'jiosaavn', name: 'JioSaavn', note: '· fallback; no track numbers' },
  { id: 'itunes', name: 'iTunes', note: '· real track numbers, year and genre; slower (rate limited)' },
]

export function Home({ open }: { open: (id: string) => void }) {
  const [input, setInput] = useState('')
  const [output, setOutput] = useState('')
  const [sources, setSources] = useState<Record<string, boolean>>({ ytmusic: true, jiosaavn: true, itunes: true })
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.jobs().then(setJobs).catch((e: Error) => setError(e.message))
  }, [])

  const anySource = Object.values(sources).some(Boolean)

  const start = async () => {
    setBusy(true)
    setError('')
    try {
      const chosen = Object.keys(sources).filter((k) => sources[k]).join(',')
      const { id } = await api.createJob(input, output, chosen)
      open(id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <section className="card">
        <h2>New scan</h2>
        <p className="muted">
          Scanning identifies your tracks and moves nothing. You review the plan before any file is touched.
        </p>
        <FolderPicker label="Music folder to organise" value={input} onChange={setInput} />
        <FolderPicker
          label="Output folder"
          value={output}
          onChange={setOutput}
          hint="Approved tracks are moved here as Artist/Album. Review and duplicate tracks stay where they are."
        />
        <div className="field">
          <label>Metadata sources</label>
          <small className="muted">Tried in this order. Later sources are only used when earlier ones find nothing.</small>
          {SOURCES.map((s) => (
            <label key={s.id} className="check">
              <input
                type="checkbox"
                checked={sources[s.id]}
                onChange={(e) => setSources((cur) => ({ ...cur, [s.id]: e.target.checked }))}
              />{' '}
              <strong>{s.name}</strong> <span className="muted">{s.note}</span>
            </label>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy || !input || !output || !anySource} onClick={start}>
          {busy ? 'Starting…' : 'Scan'}
        </button>
      </section>

      <section className="card">
        <h2>Previous jobs</h2>
        {jobs.length === 0 && <p className="muted">None yet.</p>}
        <ul className="joblist">
          {jobs.map((j) => (
            <li key={j.id}>
              <button className="link" onClick={() => open(j.id)}>
                {new Date(j.created_at * 1000).toLocaleString()}
              </button>
              <span className={`pill ${j.state}`}>{j.state}</span>
              <span className="muted"> {j.input_dir}</span>
              <span className="muted"> · {Object.values(j.counts).reduce((a, b) => a + b, 0)} tracks</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
