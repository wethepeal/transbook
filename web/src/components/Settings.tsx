/**
 * 配置页：部署到新机器、或想更换 API Key 时用。
 *
 * 两条刻意的设计决定：
 *
 * 1. **密钥明文永不回前端**。后端只回打码串（`sk-0457****ba86`），输入框留空即
 *    "不修改"；要抹掉已保存的密钥必须点明确的「清除」。否则用户只改模型、
 *    顺手点了保存，就会把密钥一起清空——这是最容易踩的坑。
 * 2. **只提交改动过的字段**。不做整表覆盖，避免明文多绕一圈，也避免误清。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type ConfigView } from '../api'

interface Props {
  onError: (msg: string) => void
}

const KEY = 'DEEPSEEK_API_KEY'
const SECRET_NAMES = new Set([KEY])

/** 逐字段校验；返回空串表示没问题。 */
export function validateField(name: string, value: string): string {
  const v = value.trim()
  if (!v) return ''
  if (name === KEY) {
    if (/\s/.test(v)) return '密钥里不能有空格或换行'
    if (v.length < 8) return '太短了，看起来不是一个完整的密钥'
  }
  if (name === 'DEEPSEEK_BASE_URL' && !/^https?:\/\//i.test(v)) {
    return '要填完整地址，以 http:// 或 https:// 开头'
  }
  return ''
}

export default function Settings({ onError }: Props) {
  const [view, setView] = useState<ConfigView | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [clearingKey, setClearingKey] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savedTo, setSavedTo] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setView(await api.config())
      setDraft({})
      setClearingKey(false)
    } catch (e) {
      onError(String(e))
    } finally {
      setLoading(false)
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  const errors = useMemo(() => {
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(draft)) {
      const msg = validateField(k, v)
      if (msg) out[k] = msg
    }
    return out
  }, [draft])

  /** 密钥只在"填了新值"或"显式清除"时才算改动，留空一律视为不修改。 */
  const pending: Record<string, string> = useMemo(() => {
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(draft)) {
      if (errors[k]) continue
      if (SECRET_NAMES.has(k)) {
        if (clearingKey) out[k] = ''
        else if (v.trim()) out[k] = v.trim()
      } else {
        out[k] = v.trim()
      }
    }
    if (clearingKey && !(KEY in out)) out[KEY] = ''
    return out
  }, [draft, errors, clearingKey])

  const changed = Object.keys(pending)
  const canSave = changed.length > 0 && !saving

  const save = async () => {
    if (!canSave) return
    setSaving(true)
    setSavedTo('')
    try {
      const res = await api.saveConfig(pending)
      setSavedTo(res.env_file)
      await load()
    } catch (e) {
      onError(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="stack">
        <section className="card">
          <h2>配置</h2>
          <p className="hint">加载中…</p>
        </section>
      </div>
    )
  }
  if (!view) {
    return (
      <div className="stack">
        <section className="card">
          <h2>配置</h2>
          <p className="hint">读不到配置，刷新页面试试。</p>
        </section>
      </div>
    )
  }

  const keySet = Boolean(view.fields.find((f) => f.name === KEY)?.is_set)

  return (
    <div className="stack">
      <section className="card">
        <h2>
          翻译接口
          <span className={keySet ? 'pill done' : 'pill failed'}>
            {keySet ? '密钥已配置' : '密钥未配置'}
          </span>
        </h2>
        <p className="hint">
          换机器或重新部署后，翻译要用的 API Key 不会跟过来，在这里补上即可。
          保存后<strong>立即生效</strong>，不用重启服务。
        </p>

        <div className="config-form">
          {view.fields.map((f) => {
            const isSecret = f.secret
            const value = draft[f.name] ?? (isSecret ? '' : f.value)
            const err = errors[f.name]
            const inputId = `cfg-${f.name}`
            const errId = `${inputId}-err`

            return (
              <div className="config-field" key={f.name}>
                {/* 打码值**放在 label 外面**：放进去会让输入框的可访问名变成
                    "DeepSeek API Key 当前 sk-045******ba86"，屏幕阅读器读起来又长又乱。
                    视觉上两行仍同排。 */}
                <div className="config-label-row">
                  <label htmlFor={inputId}>{f.label}</label>
                  {isSecret && f.is_set && (
                    <span className="mono field-current">当前 {f.masked}</span>
                  )}
                </div>

                <div className="config-input">
                  <input
                    id={inputId}
                    type={isSecret ? 'password' : 'text'}
                    className={err ? 'invalid' : undefined}
                    value={value}
                    placeholder={isSecret
                      ? (f.is_set ? '留空 = 不修改' : 'sk-…')
                      : '留空使用默认'}
                    autoComplete={isSecret ? 'off' : undefined}
                    spellCheck={false}
                    disabled={saving}
                    aria-invalid={err ? true : undefined}
                    aria-describedby={err ? errId : undefined}
                    onChange={(e) => {
                      setSavedTo('')
                      if (isSecret) setClearingKey(false)
                      setDraft((d) => ({ ...d, [f.name]: e.target.value }))
                    }}
                  />
                  {isSecret && f.is_set && (
                    <button
                      type="button"
                      className="ghost tiny"
                      disabled={saving}
                      onClick={() => {
                        setSavedTo('')
                        setClearingKey((c) => !c)
                        setDraft((d) => ({ ...d, [KEY]: '' }))
                      }}
                    >
                      {clearingKey ? '撤销清除' : '清除密钥'}
                    </button>
                  )}
                </div>

                {err ? (
                  <p className="field-error" id={errId}>{err}</p>
                ) : clearingKey && isSecret ? (
                  <p className="field-warn">保存后密钥会被清空，翻译将不可用。</p>
                ) : (
                  <p className="field-hint">{f.hint}</p>
                )}
              </div>
            )
          })}
        </div>

        <div className="config-actions">
          <button onClick={() => void save()} disabled={!canSave}>
            {saving ? '保存中…' : '保存'}
          </button>
          <button className="ghost" onClick={() => void load()} disabled={saving}>
            放弃改动
          </button>
          <span className={savedTo ? 'config-status saved' : 'config-status'}
                role="status" aria-live="polite">
            {savedTo
              ? `已保存，立即生效（写入 ${savedTo}）`
              : changed.length > 0
                ? `有 ${changed.length} 项待保存`
                : ''}
          </span>
        </div>
      </section>

      <section className="card">
        <h2>当前生效</h2>
        <dl className="config-active">
          <div>
            <dt>引擎</dt>
            <dd className="mono">{view.active.engine}</dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd className="mono">{view.active.model}</dd>
          </div>
          <div>
            <dt>密钥</dt>
            <dd className="mono">{view.active.key_set ? '已配置' : '未配置'}</dd>
          </div>
        </dl>
        <p className="field-hint">
          配置文件：<span className="mono">{view.env_file}</span>
          {view.env_file_exists ? '' : '（还不存在，保存后创建）'}
        </p>
      </section>
    </div>
  )
}
