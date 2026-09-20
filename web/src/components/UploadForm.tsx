/** 上传一本书并（可选）直接开始翻译。 */
import { useRef, useState } from 'react'
import { api } from '../api'

interface Props {
  onUploaded: (docId: string, jobId: string) => void
  onError: (msg: string) => void
}

export default function UploadForm({ onUploaded, onError }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [docId, setDocId] = useState('')
  const [engine, setEngine] = useState('deepseek')
  const [mode, setMode] = useState('bilingual')
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    try {
      const r = await api.upload(file, {
        ...(docId ? { doc_id: docId } : {}),
        engine,
        mode,
        to: 'both',
        translate: 'true',
      })
      onUploaded(r.doc_id, r.job_id)
      setFile(null)
      setDocId('')
      if (inputRef.current) inputRef.current.value = ''
    } catch (err) {
      onError(String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card upload" onSubmit={submit}>
      <h2>上传一本书</h2>
      <p className="hint">
        支持 EPUB / PDF。上传后自动跑完整流水线：抽取 → 入库 → 翻译 → 渲染，
        作业在后台子进程里执行，关掉页面也不会中断。
      </p>
      {/* 字段排成一行、按钮对齐到控件底边。原来拆成两行、各自只占卡片左侧
          三分之一，右边一大片空白，看着像页面没做完。 */}
      <div className="row upload-fields">
        {/* 包进 label 才有可访问名——原生文件控件自己那个"选择文件"按钮
            对屏幕阅读器来说是空的，光有一个控件说明不了要选什么 */}
        <label>
          选择文件（EPUB / PDF）
          <input
            ref={inputRef}
            type="file"
            accept=".epub,.pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
        </label>
        <label>
          项目名（可留空，默认取书名）
          <input
            type="text"
            value={docId}
            placeholder="my-book"
            onChange={(e) => setDocId(e.target.value)}
          />
        </label>
        <label>
          引擎
          <select value={engine} onChange={(e) => setEngine(e.target.value)}>
            <option value="deepseek">DeepSeek API</option>
            <option value="local">本地模型</option>
            <option value="fake">Fake（不花钱，验证流程）</option>
          </select>
        </label>
        <label>
          输出
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="bilingual">双语对照（供审核）</option>
            <option value="zh">纯中文（终版）</option>
          </select>
        </label>
        <button type="submit" disabled={!file || busy}>
          {busy ? '上传中…' : '上传并开始翻译'}
        </button>
      </div>
    </form>
  )
}
