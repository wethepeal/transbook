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

export type Route =
  | { name: 'list' }
  | { name: 'detail'; docId: string }
  | { name: 'review'; docId: string }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  const parts = path.split('/').filter(Boolean)
  if (parts[0] === 'p' && parts[1]) {
    const docId = decodeURIComponent(parts[1])
    return parts[2] === 'review' ? { name: 'review', docId } : { name: 'detail', docId }
  }
  return { name: 'list' }
}

export function href(route: Route): string {
  switch (route.name) {
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

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          transbook
        </a>
        <span className="sub">电子书翻译流水线</span>
        <nav className="crumbs">
          {route.name !== 'list' && (
            <>
              <a href="#/">项目</a>
              <span className="sep">/</span>
              <a href={href({ name: 'detail', docId: route.docId })}>{route.docId}</a>
              {route.name === 'review' && (
                <>
                  <span className="sep">/</span>
                  <span className="current">段落校对</span>
                </>
              )}
            </>
          )}
        </nav>
      </header>

      {error && (
        <div className="banner error" onClick={() => setError('')}>
          {error} <span className="dismiss">×</span>
        </div>
      )}

      <main>
        {route.name === 'list' && <ProjectList onError={setError} />}
        {route.name === 'detail' && <ProjectDetail docId={route.docId} onError={setError} />}
        {route.name === 'review' && <SegmentEditor docId={route.docId} onError={setError} />}
      </main>
    </div>
  )
}
