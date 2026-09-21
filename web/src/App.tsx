/**
 * 应用外壳与路由。
 *
 * 路由用 **hash**（`#/p/<docId>`）而不是 history API：后端把静态页面挂在 `/`，
 * hash 不需要服务端为每条前端路由做回退，刷新页面也不会 404。
 */
import { useEffect, useState } from 'react'
import ProjectList from './components/ProjectList'
import ProjectDetail from './components/ProjectDetail'
import SegmentEditor from './components/SegmentEditor'
import Settings from './components/Settings'
import Help from './components/Help'
import NotFound from './components/NotFound'

export type Route =
  | { name: 'list' }
  | { name: 'settings' }
  | { name: 'help' }
  | { name: 'detail'; docId: string }
  | { name: 'review'; docId: string }
  | { name: 'notfound'; path: string }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 0) return { name: 'list' }
  if (parts[0] === 'settings' && parts.length === 1) return { name: 'settings' }
  if (parts[0] === 'help' && parts.length === 1) return { name: 'help' }
  if (parts[0] === 'p' && parts[1]) {
    const docId = decodeURIComponent(parts[1])
    if (parts.length === 2) return { name: 'detail', docId }
    if (parts.length === 3 && parts[2] === 'review') return { name: 'review', docId }
  }
  // 认不出来就明说找不到。以前这里静默回落到项目列表——坏链接看起来像正常页面，
  // 用户不知道自己点错了什么。
  return { name: 'notfound', path }
}

export function href(route: Route): string {
  switch (route.name) {
    case 'settings':
      return '#/settings'
    case 'help':
      return '#/help'
    case 'detail':
      return `#/p/${encodeURIComponent(route.docId)}`
    case 'review':
      return `#/p/${encodeURIComponent(route.docId)}/review`
    default:
      return '#/'
  }
}

/** ── 主题 ────────────────────────────────────────────────────────────
 *  颜色全部在 `styles.css` 的 CSS 变量里，这里只负责往 `<html>` 上写
 *  `data-theme`。之所以不把两套色值搬进 JS：那样就有了两个真相来源，
 *  迟早对不上。
 */
type Theme = 'dark' | 'light'
const THEME_KEY = 'transbook-theme'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'dark' || saved === 'light') return saved
  } catch {
    // 隐私模式下 localStorage 会抛异常。读不到就跟随系统，不该因此崩掉。
  }
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

/** 内联 SVG，颜色走 currentColor 所以自动跟随主题。
 *  不用 emoji——它是最典型的"机器生成感"，而且在不同系统上长得不一样。 */
function SunIcon() {
  return (
    <svg
      width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg
      width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    >
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  )
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash))
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    const onHash = () => setRoute(parseHash(location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // 主题落在 <html> 上而不是某个容器上：`color-scheme` 需要它来让原生控件
  // （滚动条、下拉、文件选择器）跟着换，而这些不属于 React 树。
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(THEME_KEY, theme)
    } catch {
      // 存不了就算了，不影响本次会话
    }
  }, [theme])

  // 成功提示几秒后自己消失；错误留着等用户处理完再关。
  // 建完项目**必须给个成功反馈**：以前上传后直接跳走，成没成功只能自己猜。
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(''), 6000)
    return () => clearTimeout(t)
  }, [notice])

  const inProject = route.name === 'detail' || route.name === 'review'

  return (
    <div className="app">
      {/* 键盘用户不必每页都 Tab 过整个顶栏。
          这里拦下默认跳转：`#main` 会被 hash 路由当成一条未知路由而渲染 404。 */}
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault()
          const m = document.getElementById('main')
          m?.focus()
          m?.scrollIntoView({ block: 'start' })
        }}
      >
        跳到主内容
      </a>

      <header className="topbar">
        <a className="brand" href="#/">
          transbook
        </a>
        <span className="sub">电子书翻译流水线</span>
        <nav className="crumbs" aria-label="面包屑">
          {inProject && (
            <>
              <a href="#/">项目</a>
              <span className="sep">/</span>
              {route.name === 'review' ? (
                <a href={href({ name: 'detail', docId: route.docId })}>{route.docId}</a>
              ) : (
                <span className="current">{route.docId}</span>
              )}
              {route.name === 'review' && (
                <>
                  <span className="sep">/</span>
                  <span className="current">段落校对</span>
                </>
              )}
            </>
          )}
        </nav>
        {/* 两个全局入口都从哪一页都该一步到达。放在右侧而不是面包屑里；
            当前就在该页时只改样式、不移除，避免链接位置跳动。 */}
        <a
          className={route.name === 'help' ? 'nav-link current' : 'nav-link'}
          href={href({ name: 'help' })}
          aria-current={route.name === 'help' ? 'page' : undefined}
        >
          快速上手
        </a>
        <a
          className={route.name === 'settings' ? 'nav-link current' : 'nav-link'}
          href={href({ name: 'settings' })}
          aria-current={route.name === 'settings' ? 'page' : undefined}
          style={{ marginLeft: 'var(--s-2)' }}
        >
          配置
        </a>
        <button
          className="theme-toggle"
          onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
          aria-label={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
          title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
        >
          {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          {theme === 'dark' ? '浅色' : '深色'}
        </button>
      </header>

      {/* role="alert" 让屏幕阅读器把新出现的错误念出来；关闭按钮是真按钮，
          键盘用户才关得掉（原来只能点整条横幅）。 */}
      {error && (
        <div className="banner error" role="alert">
          <span className="banner-text">{error}</span>
          <button className="dismiss" onClick={() => setError('')} aria-label="关闭提示">
            ×
          </button>
        </div>
      )}

      {/* role="status" 而不是 alert：成功提示不该打断屏幕阅读器正在读的内容 */}
      {notice && (
        <div className="banner ok" role="status">
          <span className="banner-text">{notice}</span>
          <button className="dismiss" onClick={() => setNotice('')} aria-label="关闭提示">
            ×
          </button>
        </div>
      )}

      <main id="main" tabIndex={-1}>
        {route.name === 'list' && <ProjectList onError={setError} onNotice={setNotice} />}
        {route.name === 'help' && <Help />}
        {route.name === 'settings' && <Settings onError={setError} />}
        {route.name === 'detail' && <ProjectDetail docId={route.docId} onError={setError} />}
        {route.name === 'review' && <SegmentEditor docId={route.docId} onError={setError} />}
        {route.name === 'notfound' && <NotFound path={route.path} />}
      </main>
    </div>
  )
}
