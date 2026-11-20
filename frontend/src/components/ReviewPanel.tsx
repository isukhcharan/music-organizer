import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Choices, Mode, Preview, ReviewItem, ReviewQueue, TransferMode } from '../api'
import { TransferPicker } from './Panels'

const MODE_LABEL: Record<Mode, string> = { local: 'Local', provider: 'Provider', manual: 'Type…', keep: 'Keep as is' }
const FIELD_LABEL: Record<string, string> = {
  title: 'Title', artist: 'Artist', album: 'Album', albumartist: 'Album artist', date: 'Year',
  tracknumber: 'Track #', genre: 'Genre',
}
const shown = (v: string | null | undefined) => (v ? v : <span className="blank">(blank)</span>)

interface CardProps {
  jobId: string
  item: ReviewItem
  fields: string[]
  defaults: Record<string, Mode>
  saved: boolean
  onSaved: (message: string) => void
  onNext: () => void
  onPrev: () => void
}

/** One track under review. Keyed by file in the parent, so its state resets whenever the track changes. */
function ReviewCard({ jobId, item, fields, defaults, saved, onSaved, onNext, onPrev }: CardProps) {
  // nothing is preselected: candidate #1 is often right but is never assumed
  const [selected, setSelected] = useState<number | null>(null)
  const [choices, setChoices] = useState<Choices>({})
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState('')
  const seq = useRef(0)
  const isDup = item.status === 'REVIEW_DUPLICATE'

  useEffect(() => {
    if (selected === null) return
    const mine = ++seq.current
    api
      .preview(jobId, item.source, selected, choices)
      .then((p) => mine === seq.current && (setPreview(p), setError('')))
      .catch((e: Error) => mine === seq.current && setError(e.message))
  }, [jobId, item.source, selected, choices])

  const save = async () => {
    if (selected === null) return
    try {
      await api.decide(jobId, item.source, selected, choices)
      onSaved('Saved. Nothing has moved yet.')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const keepBoth = async () => {
    try {
      await api.keepBoth(jobId, item.source)
      onSaved('Both copies will be moved when you apply the plan.')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // keyboard: 1-8 pick a candidate, Enter save, S / → next, ← previous
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(t.tagName) || e.ctrlKey || e.metaKey) return
      if (/^[1-8]$/.test(e.key) && Number(e.key) <= item.candidates.length) setSelected(Number(e.key) - 1)
      else if (e.key === 'Enter') void save()
      else if (e.key === 's' || e.key === 'ArrowRight') onNext()
      else if (e.key === 'ArrowLeft') onPrev()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const modeOf = (f: string): Mode => choices[f]?.mode ?? defaults[f] ?? 'keep'
  const setMode = (f: string, mode: Mode) => setChoices((c) => ({ ...c, [f]: { mode, value: c[f]?.value ?? '' } }))

  return (
    <section className="card">
      <h3>
        {item.title} {saved && <span className="pill applied">saved</span>}
      </h3>
      <p className="muted path">{item.source}</p>
      <p>
        <span className={`pill ${item.status}`}>{item.status.replace('REVIEW_', '').toLowerCase()}</span> {item.reason}
      </p>
      {error && <p className="error">{error}</p>}

      {isDup ? (
        <>
          <p>
            Other copy: <code>{item.duplicate_of}</code>
          </p>
          <p className="muted">Nothing is deleted. Keep both, or skip and decide later.</p>
          <button className="primary" onClick={() => void keepBoth()}>Keep both</button>
        </>
      ) : (
        <>
          <h4>Which one is it? <small className="muted">(keys 1–8)</small></h4>
          {item.candidates.length === 0 && <p className="muted">The provider found no candidates for this track.</p>}
          <ol className="candidates">
            {item.candidates.map((c, i) => (
              <li key={i}>
                <label className={selected === i ? 'chosen' : ''}>
                  <input type="radio" name="cand" checked={selected === i} onChange={() => setSelected(i)} />
                  <span className="num">{i + 1}</span>
                  <span>
                    <strong>{c.title}</strong> — {c.artist}
                    <br />
                    <small className="muted">
                      {c.album_hint || 'no album'} · {c.source}
                      {c.score !== undefined && ` · match ${Math.round(c.score * 100)}%`}
                    </small>
                  </span>
                </label>
              </li>
            ))}
          </ol>

          {selected !== null && (
            <>
              <h4>What to write</h4>
              <table className="fields">
                <thead>
                  <tr><th>Field</th><th>Local (now)</th><th>Provider</th><th>Use</th><th>Result</th></tr>
                </thead>
                <tbody>
                  {fields.map((f) => (
                    <tr key={f}>
                      <td>{FIELD_LABEL[f] ?? f}</td>
                      <td>{shown(item.local[f])}</td>
                      <td>{shown(preview?.provider[f])}</td>
                      <td>
                        <select value={modeOf(f)} onChange={(e) => setMode(f, e.target.value as Mode)}>
                          {(Object.keys(MODE_LABEL) as Mode[]).map((m) => (
                            <option key={m} value={m}>{MODE_LABEL[m]}</option>
                          ))}
                        </select>
                        {modeOf(f) === 'manual' && (
                          <input
                            value={choices[f]?.value ?? ''}
                            placeholder={FIELD_LABEL[f]}
                            onChange={(e) => setChoices((c) => ({ ...c, [f]: { mode: 'manual', value: e.target.value } }))}
                          />
                        )}
                      </td>
                      <td>
                        {preview ? (
                          preview.values[f] ? <strong>{preview.values[f]}</strong> : <span className="blank">unchanged</span>
                        ) : '…'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {preview && <p>Will be moved to <code>{preview.destination}</code></p>}
              <button className="primary" onClick={() => void save()}>Save &amp; next (Enter)</button>
            </>
          )}
        </>
      )}
    </section>
  )
}

export function ReviewPanel({
  jobId, onChanged, transfer, onTransfer,
}: { jobId: string; onChanged: () => void; transfer: TransferMode; onTransfer: (m: TransferMode) => void }) {
  const [queue, setQueue] = useState<ReviewQueue | null>(null)
  const [idx, setIdx] = useState(0)
  const [saved, setSaved] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    api.review(jobId).then(setQueue).catch((e: Error) => setError(e.message))
  }, [jobId])

  if (!queue) return <p className="muted">{error || 'Loading…'}</p>
  if (queue.items.length === 0) return <p className="muted">Nothing to review. 🎉</p>

  const last = queue.items.length - 1
  const go = (d: number) => setIdx((i) => Math.min(Math.max(i + d, 0), last))
  const item = queue.items[idx]

  const applyDecisions = async () => {
    const verb = transfer === 'copy' ? 'Copy' : 'Move'
    if (!window.confirm(`${verb} ${saved.size} reviewed track(s) to the output folder now?`)) return
    try {
      const r = await api.applyReviewed(jobId, transfer)
      setMessage(`${transfer === 'copy' ? 'Copied' : 'Moved'} ${r.moved} reviewed track(s)${r.failed ? `, ${r.failed} failed` : ''}.`)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="stack">
      <div className="toolbar">
        <span>
          Track <strong>{idx + 1}</strong> of {queue.items.length} · {saved.size} saved
        </span>
        <span className="spacer" />
        <button onClick={() => go(-1)} disabled={idx === 0}>← Prev</button>
        <button onClick={() => go(1)} disabled={idx === last}>Skip / Next →</button>
        <button className="primary" disabled={saved.size === 0} onClick={() => void applyDecisions()}>
          Apply {saved.size} saved decision(s)
        </button>
      </div>
      <TransferPicker mode={transfer} onChange={onTransfer} />
      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}
      <ReviewCard
        key={item.source}
        jobId={jobId}
        item={item}
        fields={queue.fields}
        defaults={queue.defaults}
        saved={saved.has(item.source)}
        onSaved={(m) => {
          setSaved((s) => new Set(s).add(item.source))
          setMessage(m)
          go(1)
          onChanged()
        }}
        onNext={() => go(1)}
        onPrev={() => go(-1)}
      />
    </div>
  )
}
