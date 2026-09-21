/** 项目列表：上传入口 + 已有项目一览。 */
import { useCallback, useEffect, useState } from 'react'
import { api, watchJob, type Project } from '../api'
import UploadForm from './UploadForm'

interface Props {
  onError: (msg: string) => void
  /** 成功提示（会自动消失）：建完项目要给用户一个明确的反馈 */
  onNotice: (msg: string) => void
}

export default function ProjectList({ onError, onNotice }: Props) {
  const [projects, setProjects] = useState<Project[]>([])
  const [watching, setWatching] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setProjects(await api.projects())
    } catch (e) {
      onError(String(e))
    } finally {
      setLoading(false)
    }
  }, [onError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 上传后边跑边刷新列表，用户能立刻看到新项目出现
  useEffect(() => {
    if (!watching) return
    return watchJob(watching, (job) => {
      if (job.status === 'done' || job.status === 'failed') {
        setWatching(null)
        void refresh()
      } else if (job.status === 'running') {
        void refresh()
      }
    })
  }, [watching, refresh])

  return (
    <div className="stack">
      <UploadForm
        onError={onError}
        onUploaded={(docId, jobId, filename) => {
          setWatching(jobId)
          // 创建成功要**明说**。以前上传后直接跳走，成没成功只能靠猜——
          // 而抽取还没跑完时详情页又会报"项目不存在"，看起来就像建失败了。
          onNotice(`项目「${docId}」创建成功，正在后台抽取 ${filename}…`)
          void refresh()
          location.hash = `#/p/${encodeURIComponent(docId)}`
        }}
      />

      <section className="card">
        <h2>
          项目 <span className="count">{projects.length}</span>
          <button className="ghost" onClick={() => void refresh()}>
            刷新
          </button>
        </h2>
        {loading ? (
          /* 骨架屏而不是"加载中…"三个字：形状贴近真实内容，
             界面不会在数据到达时整体跳一下 */
          <div aria-hidden="true">
            <div className="skeleton skeleton-row" />
            <div className="skeleton skeleton-row" />
            <div className="skeleton skeleton-row" />
          </div>
        ) : projects.length === 0 ? (
          <div className="empty">
            <strong>还没有项目</strong>
            <p className="empty-hint">
              用上面的表单上传一个 EPUB 或 PDF，流水线会自动跑抽取、入库、翻译与渲染。
            </p>
          </div>
        ) : (
          <table className="table table-projects">
            {/* 固定各列宽度：不约束的话自动布局会把「状态」撑得很宽、
                把「产物」挤扁（项目名与文件名长短差得很多） */}
            <colgroup>
              <col className="c-name" />
              <col className="c-status" />
              <col className="c-source" />
              <col />
              <col className="c-action" />
            </colgroup>
            <thead>
              <tr>
                <th>项目</th>
                <th>状态</th>
                <th>输入</th>
                <th>产物</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.doc_id}>
                  <td className="name nowrap">
                    <a href={`#/p/${encodeURIComponent(p.doc_id)}`}>{p.doc_id}</a>
                  </td>
                  <td className="nowrap">
                    {/* 三种状态要分开：刚上传、抽取还没跑完的项目既不是
                        "可翻译"也不是"已抽取"，原来会被错标成后者 */}
                    {p.has_db ? (
                      <span className="pill done">可翻译</span>
                    ) : p.has_ir ? (
                      <span className="pill queued">已抽取</span>
                    ) : (
                      <span className="pill running">抽取中</span>
                    )}
                  </td>
                  <td className="mono small nowrap">{p.source ?? '—'}</td>
                  {/* 产物做成小标签：文件名很长，直接拼成一行会把整列撑开、
                      挤扁「状态」和操作列（实测截图）。标签内截断、悬停看全名，
                      完整清单与下载在详情页。 */}
                  <td className="out">
                    {p.outputs.length === 0 ? (
                      <span className="dim">—</span>
                    ) : (
                      <div className="chips">
                        {p.outputs.map((f) => (
                          <span key={f} className="chip mono" title={f}>
                            {f}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="right nowrap">
                    {/* 行内次级操作：用 ghost 而不是实心强调色。
                        五行实心按钮会糊成一堵蓝墙，把项目名都压没了。 */}
                    <a
                      className="btn small ghost"
                      href={`#/p/${encodeURIComponent(p.doc_id)}/review`}
                    >
                      校对
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
