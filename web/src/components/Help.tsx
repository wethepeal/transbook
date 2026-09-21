/**
 * 快速上手：把「网页端从头到尾走一遍」讲清楚。
 *
 * **为什么放在应用里，而不是只留文档**：文档在 GitHub 上，而用户是在这个界面里干活的。
 * 想确认"下一步点哪"的时候，不该再切出去翻仓库。
 *
 * **为什么不把手册搬进来**：完整功能（命令行 18 条命令、术语表、滚动摘要、引擎对比、
 * 故障排查）在 `docs/DELIVERY.md` 里。在这里再写一份，两处迟早说法不一致。
 * 所以这一页只讲流程，其余链接出去。
 */

/** 仓库地址。换仓库/改名字时要同步这里（目前只有这一处硬编码）。 */
const REPO = 'https://github.com/wethepeal/transbook'
const MANUAL = `${REPO}/blob/main/docs/DELIVERY.md`
const RELEASES = `${REPO}/releases`

export default function Help() {
  return (
    <div className="stack">
      <section className="card">
        <h2>快速上手</h2>
        <p className="hint">
          这一页把<strong>网页端从头到尾走一遍</strong>讲清楚。装在另一台电脑上、
          或者想用命令行，看下面「换一台电脑」和「命令行速查」。
        </p>

        <ol className="steps">
          <li>
            <h3>上传一本书</h3>
            <p>
              首页选好文件，点「上传并开始翻译」。其余三项都有默认值，不认识就跳过。
            </p>
            <ul className="step-points">
              <li>
                <b>项目名</b>可以留空，默认取书名。它只是<strong>管理标识</strong>——
                产物文件名用的是书名，不是它。
              </li>
              <li>
                <b>引擎</b>：默认 DeepSeek。想先零成本验证流程通不通，选
                <span className="cmd">Fake</span>（它写的是假译文，会进翻译记忆库，
                正式翻译前记得用真引擎覆盖）。
              </li>
              <li>
                <b>输出</b>：双语对照（供你审核）或纯中文。不必现在纠结，
                两种之后都能再渲染。
              </li>
            </ul>
          </li>

          <li>
            <h3>等它跑完（可以走开）</h3>
            <p>
              流水线是抽取 → 入库 → 翻译 → 渲染，作业跑在<strong>独立子进程</strong>里：
              关掉这个页面不会中断，重启服务也不丢进度。
            </p>
            <ul className="step-points">
              <li>详情页有实时进度条，能看到当前卡在哪一阶段。</li>
              <li>实测一本 40 万字的书约 <b>12 分钟</b>、花费约 <b>¥0.8</b>。</li>
              <li>有成本上限护栏，跑到上限会停下，已翻的段落不会重复计费。</li>
            </ul>
          </li>

          <li>
            <h3>逐段校对</h3>
            <p>点「校对」进左右对照：左边原文（只读），右边译文（可直接改）。</p>
            <ul className="step-points">
              <li>可以搜索原文/译文、按状态筛选、翻页；工具栏是吸顶的。</li>
              <li>
                保存后这一段的「已定稿」会亮起来。
                <strong>机器译文永远不会被覆盖</strong>——「撤销定稿」随时回到机翻。
              </li>
              <li>
                段落太多不想在网页里改？到「产物」下载 <span className="cmd">校对稿 TSV</span>，
                在 Excel / VS Code 里改译文列，再用命令行回灌。
              </li>
            </ul>
          </li>

          <li>
            <h3>下载成品</h3>
            <p>回到详情页的「产物」标签，下载你需要的版本。</p>
            <ul className="step-points">
              <li><b>双语版</b>：原文与译文对照，适合自己核对或学习。</li>
              <li><b>纯中文版</b>：终版，直接读。</li>
              <li>每种都有 EPUB 与 PDF。文件名用书名，例如
                <span className="cmd">书名.zh.epub</span>。</li>
            </ul>
          </li>

          <li>
            <h3>换一台电脑 / 重新填密钥</h3>
            <p>
              密钥不会跟着项目走。在「配置」页填上新的即可，<strong>保存后立即生效</strong>，
              不用重启服务。
            </p>
            <ul className="step-points">
              <li>密钥明文不会被回传到页面上，接口只给打码值。</li>
              <li>同一页还能改接口地址（接本地模型）和默认模型。</li>
            </ul>
          </li>

          <li>
            <h3>不要这个项目了</h3>
            <p>
              在首页项目行的「删除」可以把它的内容清空（源书、译文库、产物），
              <strong>任务日志会保留</strong>，方便日后回看跑过什么。
            </p>
          </li>
        </ol>
      </section>

      <section className="card">
        <h2>命令行速查</h2>
        <p className="hint">
          等价于上面的操作。日常只用界面的话可以跳过；批量跑、或想把每一步分开控制时更方便。
        </p>
        <pre className="cmdblock">{`tp extract <书> -o data/work/X   # ① 抽取 → 先看 preview.md
tp import  data/work/X           # ② 入库（重复段落自动复用旧译文）
tp summarize data/work/X         # ⑩ 按章生成前情摘要（长篇建议）
tp translate data/work/X --rolling-summary --max-cost 3
tp qa      data/work/X           # ⑧ 质检：未译 / 繁体 / 假名残留 / 术语
tp render  data/work/X -m bilingual --to both   # ⑤ 双语，供审核
tp render  data/work/X -m zh --to both          # ⑤ 纯中文终版
tp validate data/work/X          # ⑨ EPUB 校验（内置 + epubcheck）`}</pre>
        <p className="hint small">
          全部 18 条命令见 <span className="cmd">tp --help</span>；
          每条命令的参数说明、验收数据、故障排查在下面的手册里。
        </p>
      </section>

      <section className="card">
        <h2>换一台电脑</h2>
        <p className="hint">
          不想碰命令行的话，去 Release 下载 Windows 压缩包，解压双击
          <span className="cmd">start.cmd</span>：它会自动装好运行环境、问一次密钥、
          打开界面。那台电脑<strong>不需要装 Python 或 Node</strong>。
        </p>
        <p>
          <a className="btn ghost" href={RELEASES} target="_blank" rel="noreferrer">
            打开发布页
          </a>
        </p>
      </section>

      <div className="callout">
        <h3>完整功能手册</h3>
        <p>
          术语表、滚动摘要、引擎对比、审核回流的细节，命令行 18 条命令的完整参数，
          44 卷 EPUB 与竖排 PDF 的验收数据，以及故障排查——都在
          <span className="cmd">docs/DELIVERY.md</span> 里。
        </p>
        <p style={{ marginTop: 'var(--s-2)' }}>
          <a className="btn ghost" href={MANUAL} target="_blank" rel="noreferrer">
            打开 DELIVERY.md
          </a>
        </p>
      </div>
    </div>
  )
}
