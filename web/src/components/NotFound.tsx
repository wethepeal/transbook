/**
 * 未知路由。
 *
 * 以前 `parseHash` 对认不出的地址静默回落到项目列表，于是坏链接看起来像正常页面——
 * 用户不知道自己点错了什么、也不知道该去哪。这里明确说出来，并给出往回走的路。
 */
export default function NotFound({ path }: { path: string }) {
  return (
    <section className="card not-found">
      <div className="code">404</div>
      <h2>页面不存在</h2>
      <p className="hint">
        地址 <span className="mono">#{path}</span> 没有对应的页面。可能是链接写错了，
        或者这个项目已经被删掉了。
      </p>
      <p>
        <a className="btn" href="#/">
          回到项目列表
        </a>
      </p>
    </section>
  )
}
