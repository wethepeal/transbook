/** 项目详情：统计看板 + 作业进度 + 操作 + 产物下载。 */
import { useCallback, useEffect, useState } from 'react'
import { api, type BookDetail, type Job } from '../api'
import JobProgress from './JobProgress'

interface Props {
  docId: string
  onError: (msg: string) => void
}

type Tab = 'overview' | 'outputs' | 'jobs'

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: '概览' },
  { id: 'outputs', label: '产物' },
  { id: 'jobs', label: '作业' },
]

export default function ProjectDetail({ docId, onError }: Props) {
  const [book, setBook] = useState<BookDetail | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [busy, setBusy] = useState('')
  const [tab, setTab] = useState<Tab>('overview')

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
      setTab('jobs')
      await refresh()
    } catch (e) {
      onError(String(e))
    } finally {
      setBusy('')
    }
  }

  if (!book) {
    return (
      <div className="stack">
        <section className="card">
          <div className="skeleton" style={{ height: 24, width: 260, marginBottom: 16 }} />
          <div className="stats" aria-hidden="true">
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="skeleton skeleton-stat" />
            ))}
          </div>
        </section>
      </div>
    )
  }

  const ir = book.ir
  const st = book.stats

  return (
    <div className="stack">
      {/* 标题与统计常驻：它们是这个项目的"身份"，切标签时不该消失 */}
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
      </section>

      {/* 正在跑的作业**不放进标签里**：切到别的标签也得看得见进度，
          否则用户提交完就没法确认它到底在不在跑 */}
      {active && (
        <section className="card card-live">
          <div className="active-job">
            <JobProgress jobId={active} onDone={() => void refresh()} />
            <button className="ghost danger" onClick={() => void api.cancelJob(active).then(refresh)}>
              取消作业
            </button>
          </div>
        </section>
      )}

      <section className="card">
        <div className="tabs" role="tablist" aria-label="项目详情视图">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              className={tab === t.id ? 'tab active' : 'tab'}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.id === 'outputs' && book.outputs.length > 0 && (
                <span className="tab-count">{book.outputs.length}</span>
              )}
              {t.id === 'jobs' && jobs.length > 0 && <span className="tab-count">{jobs.length}</span>}
            </button>
          ))}
        </div>

        {/* ── 概览：操作 ── */}
        <div id="panel-overview" role="tabpanel" aria-labelledby="tab-overview" hidden={tab !== 'overview'}>
          {/* 主次分明：真翻译是这一页的主线动作，用实心强调色；其余同级。
              「试跑」单列到分隔线右边——它是开发用的，而且**会往翻译记忆库里
              写 `[译]原文` 这种假译文**，之后真翻译可能复用它们。摆在真翻译旁边
              等权重太容易误点。 */}
          <div className="row actions">
            <div className="action-group">
              <button
                className="primary"
                disabled={!!busy || !!active}
                onClick={() => void start('translate', { engine: 'deepseek' })}
              >
                翻译（DeepSeek）
              </button>
              <button disabled={!!busy || !!active} onClick={() => void start('summarize')}>
                生成滚动摘要
              </button>
              <button
                disabled={!!busy || !!active}
                onClick={() => void start('render', { mode: 'bilingual', to: 'both' })}
              >
                渲染（双语 · EPUB+PDF）
              </button>
              <button
                disabled={!!busy || !!active}
                onClick={() => void start('render', { mode: 'zh', to: 'both' })}
              >
                渲染（纯中文 · EPUB+PDF）
              </button>
            </div>
            <span className="action-sep" aria-hidden="true" />
            <div className="action-group">
              <button
                className="ghost"
                disabled={!!busy || !!active}
                onClick={() => void start('translate', { engine: 'fake' })}
                title="不调用 API，用假译文跑通流程。写入的是 [译]原文，会进翻译记忆库"
              >
                试跑（Fake 引擎）
              </button>
            </div>
          </div>
          <p className="hint small">
            试跑只用来验证流程是否通，不花钱；它写入的假译文会进翻译记忆库，
            正式翻译前建议对同一本书用「翻译（DeepSeek）」覆盖。
          </p>
          {ir?.matter && Object.keys(ir.matter).length > 1 && (
            <p className="hint small">
              附页归类：
              {Object.entries(ir.matter)
                .map(([k, v]) => `${k} ${v}`)
                .join(' ｜ ')}
            </p>
          )}
        </div>

        {/* ── 产物 ── */}
        <div id="panel-outputs" role="tabpanel" aria-labelledby="tab-outputs" hidden={tab !== 'outputs'}>
          {book.outputs.length === 0 ? (
            <div className="empty">
              <strong>还没有产物</strong>
              <p className="empty-hint">
                到「概览」跑一次渲染，双语版与纯中文版的 EPUB / PDF 会出现在这里。
              </p>
            </div>
          ) : (
            <ul className="files">
              {book.outputs.map((f) => (
                <li key={f}>
                  {/* 用 ghost：多行实心强调色会和主按钮抢注意力 */}
                  <a className="btn small ghost" href={api.outputUrl(docId, f)}>
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
          {/* 段落校对是全项目用得最多的一页，入口给足分量，别做成一行容易被略过的小字 */}
          <p>
            <a className="btn ghost" href={`#/p/${encodeURIComponent(docId)}/review`}>
              进入段落校对
            </a>
          </p>
        </div>

        {/* ── 作业 ── */}
        <div id="panel-jobs" role="tabpanel" aria-labelledby="tab-jobs" hidden={tab !== 'jobs'}>
          {/* 空表只剩表头会显得像坏了，给一句说明 */}
          {jobs.length === 0 ? (
            <div className="empty">
              <strong>还没有作业记录</strong>
              <p className="empty-hint">在这里提交的作业会登记到界面里；用命令行跑的不会。</p>
            </div>
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
        </div>
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
