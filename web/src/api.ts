/**
 * 后端 API 客户端（与 `transbook/service/api.py` 一一对应）。
 *
 * 刻意不引入 axios / react-query：接口只有十几个，`fetch` + 少量类型足够，
 * 少一层依赖就少一份构建与升级负担。
 */

export interface Project {
  doc_id: string
  dir: string
  has_ir: boolean
  has_db: boolean
  source: string | null
  outputs: string[]
}

export interface IrSummary {
  title: string
  author: string
  source_lang: string
  origin: string
  vertical: boolean
  blocks: number
  counts: Record<string, number>
  matter: Record<string, number>
  toc: number
  translatable: number
  cover_image: string | null
}

export interface DbStats {
  segments: number
  done: number
  cost: number
  by_status: Record<string, number>
  tm_entries: number
}

export interface BookDetail extends Project {
  ir?: IrSummary
  stats?: DbStats
}

export interface Segment {
  seg_id: string
  block_id: string
  ord: number
  kind: string
  source_text: string
  translation: string | null
  final_translation: string | null
  status: string
}

export interface SegmentPage {
  total: number
  offset: number
  limit: number
  items: Segment[]
}

export interface QaIssue {
  kind: string
  seg_id: string
  detail: string
  severity: string
}

export interface QaReport {
  summary: string
  counts: Record<string, number>
  issues: QaIssue[]
}

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface Job {
  id: string
  kind: string
  doc_id: string
  status: JobStatus
  stage: string | null
  progress: number
  message: string | null
  error: string | null
  result: Record<string, unknown> | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

/** 作业终态——到了就不再重连 SSE */
export const TERMINAL: JobStatus[] = ['done', 'failed', 'cancelled']

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? JSON.stringify(body)
    } catch {
      /* 非 JSON 错误体，用 statusText */
    }
    throw new Error(`${res.status} ${detail}`)
  }
  const ct = res.headers.get('content-type') ?? ''
  return (ct.includes('application/json') ? res.json() : res.text()) as Promise<T>
}

export const api = {
  health: () => req<{ status: string; root: string }>('/api/health'),

  projects: () => req<Project[]>('/api/projects'),

  book: (docId: string) => req<BookDetail>(`/api/books/${encodeURIComponent(docId)}`),

  segments: (docId: string, opts: { offset?: number; limit?: number; q?: string; status?: string } = {}) => {
    const p = new URLSearchParams()
    if (opts.offset) p.set('offset', String(opts.offset))
    if (opts.limit) p.set('limit', String(opts.limit))
    if (opts.q) p.set('q', opts.q)
    if (opts.status) p.set('status', opts.status)
    return req<SegmentPage>(`/api/books/${encodeURIComponent(docId)}/segments?${p}`)
  },

  /** 单段保存（校对界面逐段编辑用） */
  saveSegment: (docId: string, segId: string, finalTranslation: string) =>
    req<{ seg_id: string }>(`/api/books/${encodeURIComponent(docId)}/segments/${encodeURIComponent(segId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ final_translation: finalTranslation }),
    }),

  /**
   * 撤销定稿，回到机器译文。
   *
   * 必须用 `clear: true` 而不是 `final_translation: null`——后者语义含糊
   * （"把定稿设成空" 还是 "取消定稿"？），后端会直接 400。
   */
  clearSegment: (docId: string, segId: string) =>
    req<{ seg_id: string }>(`/api/books/${encodeURIComponent(docId)}/segments/${encodeURIComponent(segId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clear: true }),
    }),

  qa: (docId: string) => req<QaReport>(`/api/books/${encodeURIComponent(docId)}/qa`),

  jobs: (docId?: string, limit = 20) =>
    req<Job[]>(`/api/jobs?limit=${limit}${docId ? `&doc_id=${encodeURIComponent(docId)}` : ''}`),

  job: (jobId: string) => req<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),

  cancelJob: (jobId: string) =>
    req<{ job_id: string }>(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' }),

  submitJob: (docId: string, kind: string, params: Record<string, unknown> = {}) =>
    req<{ job_id: string }>(`/api/books/${encodeURIComponent(docId)}/jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, params }),
    }),

  upload: (file: File, fields: Record<string, string>) => {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(fields)) fd.append(k, v)
    return req<{ doc_id: string; job_id: string }>('/api/books', { method: 'POST', body: fd })
  },

  reviewTsvUrl: (docId: string) => `/api/books/${encodeURIComponent(docId)}/review.tsv`,

  /** 导出文件的下载地址 */
  outputUrl: (docId: string, name: string) =>
    `/api/books/${encodeURIComponent(docId)}/files/${encodeURIComponent(name)}`,
}

/**
 * 订阅作业进度（SSE）。
 *
 * 用原生 `EventSource`：它自带断线重连，而后端在终态会发 `event: end`，
 * 我们收到就主动关闭——否则 EventSource 会一直重连一个已经结束的作业。
 */
export function watchJob(jobId: string, onUpdate: (job: Partial<Job>) => void): () => void {
  const es = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`)
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (data.error) return
      onUpdate(data as Partial<Job>)
    } catch {
      /* 忽略无法解析的心跳 */
    }
  }
  es.addEventListener('end', () => es.close())
  es.onerror = () => {
    /* 交给 EventSource 自己重连；终态时后端已发 end 并关闭 */
  }
  return () => es.close()
}
