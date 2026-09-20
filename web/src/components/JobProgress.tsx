/** 作业进度：订阅 SSE，实时显示阶段与百分比。 */
import { useEffect, useRef, useState } from 'react'
import { TERMINAL, watchJob, type Job } from '../api'

interface Props {
  jobId: string
  /** 作业结束时回调一次（用来刷新项目数据） */
  onDone?: (job: Job) => void
  compact?: boolean
}

export default function JobProgress({ jobId, onDone, compact = false }: Props) {
  const [job, setJob] = useState<Partial<Job> | null>(null)
  const doneRef = useRef(false)
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  useEffect(() => {
    doneRef.current = false
    setJob(null)
    const stop = watchJob(jobId, (patch) => {
      setJob((prev) => ({ ...prev, ...patch }))
      if (patch.status && TERMINAL.includes(patch.status) && !doneRef.current) {
        doneRef.current = true
        onDoneRef.current?.(patch as Job)
      }
    })
    return stop
  }, [jobId])

  const pct = Math.round(((job?.progress as number) ?? 0) * 100)
  const status = (job?.status as string) ?? 'connecting'

  return (
    <div className={`job ${compact ? 'compact' : ''}`}>
      <div className="job-head">
        <span className={`pill ${status}`}>{statusLabel(status)}</span>
        <span className="job-stage">{job?.stage ?? '等待中…'}</span>
        <span className="job-pct">{pct}%</span>
      </div>
      <div className="bar">
        <div className={`bar-fill ${status}`} style={{ width: `${pct}%` }} />
      </div>
      {!compact && job?.error && <pre className="job-error">{job.error}</pre>}
      {!compact && job?.message && !job.error && (
        <details className="job-log">
          <summary>日志</summary>
          <pre>{job.message}</pre>
        </details>
      )}
    </div>
  )
}

function statusLabel(s: string): string {
  return (
    {
      connecting: '连接中',
      queued: '排队中',
      running: '进行中',
      done: '已完成',
      failed: '失败',
      cancelled: '已取消',
    }[s] ?? s
  )
}
