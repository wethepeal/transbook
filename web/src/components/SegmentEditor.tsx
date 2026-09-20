/**
 * 段落级对照校对（M6 的核心界面）。
 *
 * 布局是"原文 / 译文"两栏逐段对照：左侧只读原文，右侧可编辑译文。
 * 编辑的是 `final_translation`（定稿），**不覆盖机翻**——所以可以随时"撤销定稿"
 * 回到机器译文，这与命令行 `tp apply-review` / `clear-review` 是同一套语义。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Segment } from '../api'

interface Props {
  docId: string
  onError: (msg: string) => void
}

const PAGE = 40

export default function SegmentEditor({ docId, onError }: Props) {
  const [items, setItems] = useState<Segment[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const timer = useRef<number | null>(null)

  const load = useCallback(
    async (off = offset) => {
      setLoading(true)
      try {
        const page = await api.segments(docId, { offset: off, limit: PAGE, q: query, status })
        setItems(page.items)
        setTotal(page.total)
        const d: Record<string, string> = {}
        const s: Record<string, string> = {}
        for (const it of page.items) {
          const cur = it.final_translation ?? it.translation ?? ''
          d[it.seg_id] = cur
          s[it.seg_id] = cur
        }
        setDraft(d)
        setSaved(s)
      } catch (e) {
        onError(String(e))
      } finally {
        setLoading(false)
      }
    },
    [docId, offset, query, status, onError],
  )

  useEffect(() => {
    void load(offset)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, offset, status])

  // 搜索做 300ms 防抖，避免每敲一个字打一次接口
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      setOffset(0)
      void load(0)
    }, 300)
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  const changed = useMemo(
    () => items.filter((it) => (draft[it.seg_id] ?? '') !== (saved[it.seg_id] ?? '')).map((it) => it.seg_id),
    [items, draft, saved],
  )

  async function saveOne(segId: string) {
    try {
      const text = draft[segId] ?? ''
      await api.saveSegment(docId, segId, text)
      setSaved((p) => ({ ...p, [segId]: text }))
      // 同步更新本地 items：不更新的话「已定稿」标记与「撤销定稿」按钮要刷新页面
      // 才出现——用户保存完看不到任何反馈（真实浏览器操作才暴露的 bug）
      setItems((prev) => prev.map((it) => (it.seg_id === segId
        ? { ...it, final_translation: text } : it)))
    } catch (e) {
      onError(String(e))
    }
  }

  async function saveAll() {
    setBusy(true)
    try {
      for (const id of changed) await api.saveSegment(docId, id, draft[id] ?? '')
      setSaved((p) => {
        const n = { ...p }
        for (const id of changed) n[id] = draft[id] ?? ''
        return n
      })
      setItems((prev) => prev.map((it) => (changed.includes(it.seg_id)
        ? { ...it, final_translation: draft[it.seg_id] ?? '' } : it)))
    } catch (e) {
      onError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function revertOne(segId: string, machine: string) {
    try {
      await api.clearSegment(docId, segId)
      setDraft((p) => ({ ...p, [segId]: machine }))
      setSaved((p) => ({ ...p, [segId]: machine }))
      setItems((prev) => prev.map((it) => (it.seg_id === segId
        ? { ...it, final_translation: null } : it)))
    } catch (e) {
      onError(String(e))
    }
  }

  const pages = Math.max(1, Math.ceil(total / PAGE))
  const curPage = Math.floor(offset / PAGE) + 1

  return (
    <div className="stack">
      <section className="card sticky">
        {/* 控件与计数**分两行**：混在一行时窗口一窄就会把按钮挤到第二行、
            左半边空着，看着像坏了。控件行只放控件，计数另起一行更稳定。 */}
        <div className="row toolbar">
          <input
            className="search"
            placeholder="搜索原文或译文…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            <option value="done">已译</option>
            <option value="pending">待译</option>
            <option value="failed">失败</option>
          </select>
          <span className="grow" />
          {/* 两个按钮包在一起：窗口变窄时整组换行，而不是把「重新载入」单独丢到下一行 */}
          <div className="toolbar-actions">
            <button disabled={!changed.length || busy} onClick={() => void saveAll()}>
              {busy ? '保存中…' : `保存本页改动（${changed.length}）`}
            </button>
            <button className="ghost" onClick={() => void load(offset)}>
              重新载入
            </button>
          </div>
        </div>
        <div className="hint small toolbar-info">
          共 {total} 段 ｜ 本页 {items.length} 段 ｜ 未保存{' '}
          <b className={changed.length ? 'warn' : ''}>{changed.length}</b>
        </div>
      </section>

      {loading ? (
        <p className="hint">载入中…</p>
      ) : items.length === 0 ? (
        <p className="hint">没有匹配的段落。</p>
      ) : (
        <div className="segments">
          {items.map((it) => {
            const isFinal = it.final_translation != null
            const dirty = (draft[it.seg_id] ?? '') !== (saved[it.seg_id] ?? '')
            return (
              <article key={it.seg_id} className={`seg ${dirty ? 'dirty' : ''}`}>
                <header className="seg-head">
                  <span className="mono tiny">{it.seg_id}</span>
                  <span className="mono tiny dim">{it.kind}</span>
                  <span className={`pill ${it.status}`}>{it.status}</span>
                  {isFinal && <span className="pill ok">已定稿</span>}
                  <span className="grow" />
                  {dirty && <span className="warn small">未保存</span>}
                  <button className="ghost tiny" disabled={!dirty} onClick={() => void saveOne(it.seg_id)}>
                    保存
                  </button>
                  {isFinal && (
                    <button
                      className="ghost tiny"
                      title="清空定稿，回到机器译文"
                      onClick={() => void revertOne(it.seg_id, it.translation ?? '')}
                    >
                      撤销定稿
                    </button>
                  )}
                </header>
                <div className="seg-body">
                  <div className="src">{it.source_text}</div>
                  <textarea
                    className="tgt"
                    rows={Math.max(2, Math.ceil(it.source_text.length / 34))}
                    value={draft[it.seg_id] ?? ''}
                    spellCheck={false}
                    onChange={(e) => setDraft((p) => ({ ...p, [it.seg_id]: e.target.value }))}
                  />
                </div>
                {isFinal && it.translation && it.translation !== it.final_translation && (
                  <details className="machine">
                    <summary>查看机器译文</summary>
                    <div className="dim small">{it.translation}</div>
                  </details>
                )}
              </article>
            )
          })}
        </div>
      )}

      <section className="card pager">
        <button className="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          上一页
        </button>
        <span className="hint small">
          第 {curPage} / {pages} 页
        </span>
        <button
          className="ghost"
          disabled={offset + PAGE >= total}
          onClick={() => setOffset(offset + PAGE)}
        >
          下一页
        </button>
      </section>
    </div>
  )
}
