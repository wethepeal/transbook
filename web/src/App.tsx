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
import NotFound from './components/NotFound'

export type Route =
  | { name: 'list' }
  | { name: 'settings' }
  | { name: 'detail'; docId: string }
  | { name: 'review'; docId: string }
  | { name: 'notfound'; path: string }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 0) return { name: 'list' }
  if (parts[0] === 'settings' && parts.length === 1) return { name: 'settings' }
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
    case 'detail':
      return `#/p/${encodeURIComponent(route.docId)}`
    case 'review':
      return `#/p/${encodeURIComponent(route.docId)}/review`
    default:
      return '#/'
  }
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash))
  const [error, setError] = useState('')

  useEffect(() => {
    const onHash = () => setRoute(parseHash(location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

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
        {/* 配置是全局设置，从哪一页都该一步到达。放在右侧而不是面包屑里；
            当前就在配置页时只改样式、不移除，避免链接位置跳动。 */}
        <a
          className={route.name === 'settings' ? 'nav-link current' : 'nav-link'}
          href={href({ name: 'settings' })}
          aria-current={route.name === 'settings' ? 'page' : undefined}
        >
          配置
        </a>
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

      <main id="main" tabIndex={-1}>
        {route.name === 'list' && <ProjectList onError={setError} />}
        {route.name === 'settings' && <Settings onError={setError} />}
        {route.name === 'detail' && <ProjectDetail docId={route.docId} onError={setError} />}
        {route.name === 'review' && <SegmentEditor docId={route.docId} onError={setError} />}
        {route.name === 'notfound' && <NotFound path={route.path} />}
      </main>
    </div>
  )
}
