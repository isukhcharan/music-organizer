export type Counts = Record<string, number>

export type TransferMode = 'move' | 'copy'

export interface Progress {
  done: number
  total: number
  label: string
  error: string
}

export interface Job {
  id: string
  input_dir: string
  output_dir: string
  state: string
  created_at: number
  counts: Counts
  pending_review?: number
  applied_mode?: '' | TransferMode
  progress?: Progress
}

export interface Candidate {
  source: string
  title: string
  artist: string
  album_hint?: string
  score?: number
  year?: string
}

export type Mode = 'local' | 'provider' | 'manual' | 'keep'
export interface Choice {
  mode: Mode
  value?: string
}
export type Choices = Record<string, Choice>
export type FieldValues = Record<string, string>

export interface ReviewItem {
  source: string
  status: string
  title: string
  reason: string
  duplicate_of: string
  local: FieldValues
  candidates: Candidate[]
}

export interface ReviewQueue {
  defaults: Record<string, Mode>
  fields: string[]
  items: ReviewItem[]
}

export interface Preview {
  values: Record<string, string | null>
  local: FieldValues
  provider: FieldValues
  destination: string
}

export interface PlanOp {
  source: string
  dest: string
  collision: boolean
  problem: string
}

export interface Plan {
  will_move: number
  stay: number
  stay_by_status: Counts
  ops: PlanOp[]
}

export interface Track {
  source: string
  status: string
  local_title?: string
  local_artist?: string
  matched_title?: string
  matched_album?: string
  track_number?: string
  detail_status?: string
  output?: string
}

export interface Operation {
  id: number
  op_type: string
  source: string
  dest: string
  status: string
  error: string
}

export interface CleanupDir {
  path: string
  kind: 'empty' | 'leftovers'
  files: string[]
  has_images: boolean
  empty_subfolders: number
  default_selected: boolean
}

export interface CleanupPreview {
  trash_available: boolean
  copied: boolean
  dirs: CleanupDir[]
}

export interface CleanupResult {
  removed_empty: number
  trashed: number
  skipped: { path: string; reason: string }[]
}

export interface RepairSummary {
  files: number
  will_change: number
  junk_comments: number
  junk_genres_cleared: number
  genres_filled: number
  genres_overridden: number
  album_artist_filled: number
  track_number_filled: number
  year_filled: number
  left_alone: number
}

export interface RepairStatus {
  id: string
  state: 'scanning' | 'planning' | 'ready' | 'applied' | 'failed'
  folder: string
  error: string
  job_id: string
  progress: { done: number; total: number }
  skipped: number
  summary: RepairSummary
}

export interface RepairRow {
  path: string
  title: string
  removed_comments: string[]
  old_genre: string[]
  new_genre: string[]
  clear_genre: boolean
  genre_source: string
  overrides: boolean
  fills: Record<string, string>
  notes: string[]
}

export interface DirListing {
  path: string
  parent: string | null
  dirs: { name: string; path: string }[]
  audio_files_here?: number
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail))
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body: unknown) => req<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  pickFolder: (initial: string, title: string) =>
    post<{ path: string | null; cancelled: boolean }>('/fs/pick', { initial, title }),
  listDirs: (path: string) => req<DirListing>('/fs/list?path=' + encodeURIComponent(path)),
  jobs: () => req<Job[]>('/jobs'),
  job: (id: string) => req<Job>(`/jobs/${id}`),
  createJob: (input_dir: string, output_dir: string, provider: string) =>
    post<{ id: string; files: number }>('/jobs', { input_dir, output_dir, provider }),
  tracks: (id: string, status?: string) =>
    req<{ total: number; items: Track[] }>(`/jobs/${id}/tracks?limit=1000${status ? '&status=' + status : ''}`),
  review: (id: string) => req<ReviewQueue>(`/jobs/${id}/review`),
  preview: (id: string, source: string, candidate_index: number, choices: Choices) =>
    post<Preview>(`/jobs/${id}/review/preview`, { source, candidate_index, choices }),
  decide: (id: string, source: string, candidate_index: number, choices: Choices) =>
    post<{ saved: boolean; pending: number }>(`/jobs/${id}/review/decision`, { source, candidate_index, choices }),
  keepBoth: (id: string, source: string) => post<{ saved: boolean }>(`/jobs/${id}/duplicates/keep-both`, { source }),
  plan: (id: string) => req<Plan>(`/jobs/${id}/plan`),
  apply: (id: string, mode: TransferMode) => post<{ results: Counts }>(`/jobs/${id}/apply`, { confirm: true, mode }),
  applyReviewed: (id: string, mode: TransferMode) =>
    post<{ moved: number; failed: number }>(`/jobs/${id}/apply-reviewed`, { confirm: true, mode }),
  undo: (id: string) =>
    post<{ undone: number; skipped: { path: string; reason: string }[] }>(`/jobs/${id}/undo`, { confirm: true }),
  repairStart: (folder: string, itunes: boolean, artist_fallback: boolean) =>
    post<{ id: string }>('/repair', { folder, itunes, artist_fallback }),
  repair: (id: string) => req<RepairStatus>(`/repair/${id}`),
  repairChanges: (id: string) => req<{ total: number; items: RepairRow[] }>(`/repair/${id}/changes?limit=500`),
  repairApply: (id: string) => post<{ job_id: string; results: Counts }>(`/repair/${id}/apply`, { confirm: true }),
  repairUndo: (id: string) =>
    post<{ undone: number; skipped: { path: string; reason: string }[] }>(`/repair/${id}/undo`, { confirm: true }),
  cleanupPreview: (id: string) => req<CleanupPreview>(`/jobs/${id}/cleanup`),
  cleanup: (id: string, paths: string[]) => post<CleanupResult>(`/jobs/${id}/cleanup`, { confirm: true, paths }),
  operations: (id: string) => req<Operation[]>(`/jobs/${id}/operations`),
  reportUrl: (id: string) => `/api/jobs/${id}/report.csv`,
  wsUrl: (id: string) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/jobs/${id}/ws`,
}
