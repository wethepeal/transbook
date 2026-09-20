/** 项目详情：统计看板 + 作业进度 + 操作 + 产物下载。 */
import { useCallback, useEffect, useState } from 'react'
import { api, type BookDetail, type Job } from '../api'
import JobProgress from './JobProgress'

interface Props {
  docId: string
  onError: (msg: string) => void
}

export default function ProjectDetail({ docId, onError }: Props) {
  const [book, setBook] = useState<BookDetail | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [busy, setBusy] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [b, j] = await Promise.all([api.book(docId), api.jobs(docId, 6)])
      setBook(b)
      setJobs(j)
      const running = j.find((x) => x.status === 'running' || x.status === 'queued')
      setActive(running ? running.id : null)
    } catch (e) {
      onError(String(e))
    }
  }, [docId, onError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 详情页常开着：没在跑作业时也每 5 秒对一次账，避免状态陈旧
  useEffect(() => {
    if (active) return
    const t = setInterval(() => void refresh(), 5000)
    return () => clearInterval(t)
  }, [active, refresh])

  async function start(kind: string, params: Record<string, unknown> = {}) {
    setBusy(kind)
    try {
      const r = await api.submitJob(docId, kind, params)
      setActive(r.job_id)
      await refresh()
    } catch (e) {
      onError(String(e))
    } finally {
      setBusy('')
    }
  }

  if (!book) return <p className="hint">加载中…</p>
  const ir = book.ir
  const st = book.stats

  return (
    <div className="stack">
      <section className="card">
        <h2>
          {ir?.title || docId}
          <span className="count mono">{docId}</span>
          <button className="ghost" onClick={() => void refresh()}>
            刷新
          </button>
        </h2>
        {ir && (
          <p className="hint">
            {ir.author || '未知作者'} ｜ {ir.origin.toUpperCase()} ｜ {ir.source_lang || '?'}
            {ir.vertical ? ' ｜ 竖排（输出按中文横排）' : ''}
          </p>
        )}
        <div className="stats">
          <Stat label="块" value={ir?.blocks} />
          <Stat label="可翻译" value={ir?.translatable} />
          <Stat label="段落" value={st?.segments} />
          <Stat label="已译" value={st?.done} highlight={(st?.done ?? 0) === (st?.segments ?? -1)} />
          <Stat label="目录" value={ir?.toc} />
          <Stat label="封面" value={ir?.cover_image ?? '无'} />
          <Stat label="花费" value={st ? `¥${st.cost.toFixed(4)}` : undefined} />
          <Stat label="TM" value={st?.tm_entries} />
        </div>
        {ir?.matter && Object.keys(ir.matter).length > 1 && (
          <p className="hint small">
            附页归类：
            {Object.entries(ir.matter)
              .map(([k, v]) => `${k} ${v}`)
              .join(' ｜ ')}
          </p>
        )}
      </section>

      <section className="card">
        <h2>操作</h2>
        <div className="row actions">
          <button disabled={!!busy || !!active} onClick={() => void start('translate', { engine: 'deepseek' })}>
            翻译（DeepSeek）
          </button>
          <button disabled={!!busy || !!active} onClick={() => void start('translate', { engine: 'fake' })}>
            翻译（Fake）
          </button>
          <button disabled={!!busy || !!active} onClick={() => void start('summarize')}>
            生成滚动摘要
          </button>
          <button disabled={!!busy || !!active} onClick={() => void start('render', { mode: 'bilingual', to: 'both' })}>
            渲染（双语 · EPUB+PDF）
          </button>
          <button disabled={!!busy || !!active} onClick={() => void start('render', { mode: 'zh', to: 'both' })}>
            渲染（纯中文 · EPUB+PDF）
          </button>
        </div>
        {active && (
          <div className="active-job">
            <JobProgress jobId={active} onDone={() => void refresh()} />
            <button className="ghost danger" onClick={() => void api.cancelJob(active).then(refresh)}>
              取消作业
            </button>
          </div>
        )}
      </section>

      <section className="card">
        <h2>
          产物
          <a className="btn small" href={`#/p/${encodeURIComponent(docId)}/review`}>
            进入段落校对
          </a>
        </h2>
        {book.outputs.length === 0 ? (
          <p className="hint">还没有产物，先跑一次渲染。</p>
        ) : (
          <ul className="files">
            {book.outputs.map((f) => (
              <li key={f}>
                <a className="btn small" href={api.outputUrl(docId, f)}>
                  下载
                </a>
                <span className="mono small">{f}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="hint small">
          校对稿 TSV：<a href={api.reviewTsvUrl(docId)}>下载</a>（改完可在命令行用
          <code>tp apply-review</code> 回灌，或在页面里逐段编辑）
        </p>
      </section>

      <section className="card">
        <h2>最近作业</h2>
        {/* 空表只剩表头会显得像坏了，给一句说明 */}
        {jobs.length === 0 ? (
          <p className="hint">
            这个项目还没有作业记录（用命令行跑的作业不会登记到界面里）。
          </p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>作业</th>
                <th>类型</th>
                <th>状态</th>
                <th>阶段</th>
                <th>进度</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className={j.id === active ? 'active' : ''}>
                  <td className="mono small nowrap">{j.id}</td>
                  <td className="nowrap">{j.kind}</td>
                  <td className="nowrap">
                    <span className={`pill ${j.status}`}>{j.status}</span>
                  </td>
                  <td className="small">{j.stage}</td>
                  <td className="small nowrap">{Math.round((j.progress ?? 0) * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

function Stat({ label, value, highlight }: { label: string; value?: string | number; highlight?: boolean }) {
  return (
    <div className={`stat ${highlight ? 'ok' : ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value ?? '—'}</div>
    </div>
  )
}
