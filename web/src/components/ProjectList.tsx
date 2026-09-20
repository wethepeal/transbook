/** 项目列表：上传入口 + 已有项目一览。 */
import { useCallback, useEffect, useState } from 'react'
import { api, watchJob, type Project } from '../api'
import UploadForm from './UploadForm'

interface Props {
  onError: (msg: string) => void
}

export default function ProjectList({ onError }: Props) {
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
        onUploaded={(docId, jobId) => {
          setWatching(jobId)
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
          <p className="hint">加载中…</p>
        ) : projects.length === 0 ? (
          <p className="hint">还没有项目，上传一本书试试。</p>
        ) : (
          <table className="table">
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
                  <td>
                    <a href={`#/p/${encodeURIComponent(p.doc_id)}`}>{p.doc_id}</a>
                  </td>
                  <td>
                    <span className={p.has_db ? 'pill done' : 'pill queued'}>
                      {p.has_db ? '可翻译' : '仅抽取'}
                    </span>
                  </td>
                  <td className="mono small">{p.source ?? '—'}</td>
                  <td className="mono small">{p.outputs.join('、') || '—'}</td>
                  <td className="right">
                    <a className="btn small" href={`#/p/${encodeURIComponent(p.doc_id)}/review`}>
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
