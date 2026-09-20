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
  /** 哪些段落展开了「机器译文」。默认收起——它原本每段都占一行 23px，
      而多数段落根本不需要看，40 段一页就白占 900 多 px。 */
  const [showMachine, setShowMachine] = useState<Record<string, boolean>>({})
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
          {/* placeholder 不是可访问名——屏幕阅读器读不到"搜索原文或译文…"，
              所以两个控件都补 aria-label */}
          <input
            className="search"
            placeholder="搜索原文或译文…"
            aria-label="搜索原文或译文"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            value={status}
            aria-label="按译文状态筛选"
            onChange={(e) => setStatus(e.target.value)}
          >
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
        {/* 计数与翻页放同一行、分列两端。翻页原本只在页面最底部，
            一本书 80 多页时每次换页都要滚到底——工具栏是吸顶的，
            放这里才真正"随手可得"。 */}
        <div className="toolbar-info">
          <span className="hint small">
            共 {total} 段 ｜ 本页 {items.length} 段 ｜ 未保存{' '}
            <b className={changed.length ? 'warn' : ''}>{changed.length}</b>
          </span>
          <span className="toolbar-pager">
            <button
              className="ghost tiny"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              上一页
            </button>
            <span className="hint small nowrap">
              第 {curPage} / {pages} 页
            </span>
            <button
              className="ghost tiny"
              disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}
            >
              下一页
            </button>
          </span>
        </div>
      </section>

      {loading ? (
        <div className="stack" aria-hidden="true">
          <div className="skeleton" style={{ height: 92 }} />
          <div className="skeleton" style={{ height: 92 }} />
          <div className="skeleton" style={{ height: 92 }} />
        </div>
      ) : items.length === 0 ? (
        <div className="empty">
          <strong>没有匹配的段落</strong>
          <p className="empty-hint">
            {query || status ? '换个关键词，或把状态筛选改回「全部状态」。' : '这本书还没有可校对的段落。'}
          </p>
        </div>
      ) : (
        <div className="segments">
          {items.map((it) => {
            const isFinal = it.final_translation != null
            const dirty = (draft[it.seg_id] ?? '') !== (saved[it.seg_id] ?? '')
            const hasMachine = isFinal && !!it.translation && it.translation !== it.final_translation
            const machineOpen = !!showMachine[it.seg_id]
            return (
              <article key={it.seg_id} className={`seg ${dirty ? 'dirty' : ''}`}>
                <header className="seg-head">
                  <span className="mono tiny">{it.seg_id}</span>
                  <span className="mono tiny dim">{it.kind}</span>
                  <span className={`pill ${it.status}`}>{it.status}</span>
                  {isFinal && <span className="pill ok">已定稿</span>}
                  <span className="grow" />
                  {dirty && <span className="warn small">未保存</span>}
                  {/* 机器译文从"每段一行"改成头部的一个开关 */}
                  {hasMachine && (
                    <button
                      className="ghost tiny"
                      aria-expanded={machineOpen}
                      title="展开/收起机器译文"
                      onClick={() =>
                        setShowMachine((p) => ({ ...p, [it.seg_id]: !p[it.seg_id] }))
                      }
                    >
                      机器译文
                    </button>
                  )}
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
                    // 下限给 1 行：短段（标题、拟声词）本来就只占一行，
                    // 强制两行纯属浪费纵向空间
                    rows={Math.max(1, Math.ceil(it.source_text.length / 34))}
                    value={draft[it.seg_id] ?? ''}
                    spellCheck={false}
                    onChange={(e) => setDraft((p) => ({ ...p, [it.seg_id]: e.target.value }))}
                  />
                </div>
                {machineOpen && hasMachine && (
                  <div className="machine-pop small dim">{it.translation}</div>
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
