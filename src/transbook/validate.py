"""EPUB 结构校验（M4）。

两层：

1. **内置结构检查**（零依赖，永远可跑）：mimetype 位置与压缩方式、container→OPF
   解析、清单/书脊自洽、所有 `href` 都真实存在、XHTML 良构、图片引用可解析、
   封面三类声明是否齐全。
2. **epubcheck**（若有）：官方实现，覆盖规范细节。装在 `Z:\\Tools\\epubcheck-*`，
   需要 Java。不可用时**跳过并说明**，而不是静默通过。

之所以两层都要：epubcheck 是标准答案，但它要外部依赖；内置检查能立刻给出
"打包器有没有写坏"的确定结论，且可单测。
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

CONTAINER = "META-INF/container.xml"
OPF_NS = "http://www.idpf.org/2007/opf"
XHTML_NS = "http://www.w3.org/1999/xhtml"
XLINK_NS = "http://www.w3.org/1999/xlink"
_REF = re.compile(r'(?:xlink:)?(?:href|src)="([^"]+)"', re.I)


@dataclass
class Issue:
    level: str          # error / warning
    code: str
    message: str
    where: str = ""

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{'✗' if self.level == 'error' else '⚠'}{loc} {self.message}"


@dataclass
class ValidateReport:
    path: Path
    issues: list[Issue] = field(default_factory=list)
    images: int = 0
    xhtml: int = 0
    epubcheck: str = ""
    epubcheck_ran: bool = False

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    def summary(self) -> str:
        base = (f"EPUB 校验 · {self.path.name} ｜ XHTML {self.xhtml} ｜ 图片 {self.images} ｜ "
                f"错误 {len(self.errors)} ｜ 警告 {len(self.warnings)}")
        if self.epubcheck_ran:
            base += f" ｜ epubcheck: {self.epubcheck}"
        else:
            base += f" ｜ epubcheck 未运行（{self.epubcheck}）"
        return base


def _localname(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def validate_epub(path: str | Path, *, run_epubcheck_check: bool = True) -> ValidateReport:
    """对已生成的 EPUB 做结构校验。

    `run_epubcheck_check=False` 只跑内置检查——单元测试用这个，避免测试结果
    取决于"这台机器有没有装 epubcheck"。
    """
    p = Path(path)
    rep = ValidateReport(path=p)
    if not p.is_file():
        rep.issues.append(Issue("error", "missing", f"文件不存在：{p}"))
        return rep

    z = zipfile.ZipFile(p)
    try:
        # ① mimetype 必须是**第一项**且**不压缩**（规范硬要求，很多阅读器靠它认格式）
        infos = z.infolist()
        if not infos or infos[0].filename != "mimetype":
            rep.issues.append(Issue("error", "mimetype-order",
                                    "mimetype 必须是压缩包里的第一个条目"))
        else:
            if infos[0].compress_type != zipfile.ZIP_STORED:
                rep.issues.append(Issue("error", "mimetype-compressed",
                                        "mimetype 必须以 STORED（不压缩）写入"))
            if z.read("mimetype") != b"application/epub+zip":
                rep.issues.append(Issue("error", "mimetype-content", "mimetype 内容不正确"))

        # ② container.xml → OPF
        if CONTAINER not in z.namelist():
            rep.issues.append(Issue("error", "no-container", f"缺少 {CONTAINER}"))
            return rep
        container = etree.fromstring(z.read(CONTAINER))
        rootfile = next((e.get("full-path") for e in container.iter()
                         if _localname(e.tag) == "rootfile"), None)
        if not rootfile or rootfile not in z.namelist():
            rep.issues.append(Issue("error", "bad-rootfile",
                                    f"container.xml 指向的 OPF 不存在：{rootfile}"))
            return rep

        opf = etree.fromstring(z.read(rootfile))
        opf_dir = posixpath.dirname(rootfile)
        names = set(z.namelist())

        # ③ 清单 / 书脊自洽
        items: dict[str, dict[str, str]] = {}
        for e in opf.iter(f"{{{OPF_NS}}}item"):
            iid = e.get("id") or ""
            if iid in items:
                rep.issues.append(Issue("error", "dup-id", f"清单 id 重复：{iid}"))
            items[iid] = {"href": e.get("href") or "", "type": e.get("media-type") or "",
                          "props": e.get("properties") or ""}
        hrefs = [v["href"] for v in items.values()]
        for h in {x for x in hrefs if hrefs.count(x) > 1}:
            rep.issues.append(Issue("warning", "dup-href", f"清单 href 重复：{h}"))
        for iid, it in items.items():
            full = posixpath.normpath(posixpath.join(opf_dir, it["href"]))
            if full not in names:
                rep.issues.append(Issue("error", "missing-item",
                                        f"清单项 {iid} 指向的文件不存在：{it['href']}"))

        spine = [e.get("idref") or "" for e in opf.iter(f"{{{OPF_NS}}}itemref")]
        if not spine:
            rep.issues.append(Issue("error", "empty-spine", "书脊为空"))
        for s in spine:
            if s not in items:
                rep.issues.append(Issue("error", "bad-idref",
                                        f"书脊引用了不存在的清单项：{s}"))
        navs = [i for i, v in items.items() if "nav" in v["props"]]
        if not navs:
            rep.issues.append(Issue("error", "no-nav", "清单里没有 properties=\"nav\" 的项"))
        elif not any(i in spine for i in navs):
            # 不硬断言规范要求：EPUB 3.0.1 曾经强制、后续版本放宽，且本项目的书自身
            # 已带渲染好的目次页，把 nav 再塞进书脊会重复。留给 epubcheck 定论。
            rep.issues.append(Issue("warning", "nav-not-in-spine",
                                    "nav 文档未列入书脊（若需兼容只认书脊目录的老阅读器，"
                                    "可把它加进 spine；本项目的书自带目次页，故未加）"))

        # ④ 封面三类声明（M4）
        covers = [i for i, v in items.items() if "cover-image" in v["props"]]
        meta_cover = [e.get("content") for e in opf.iter(f"{{{OPF_NS}}}meta")
                      if (e.get("name") or "") == "cover"]
        if covers and not meta_cover:
            rep.issues.append(Issue("warning", "no-cover-meta",
                                    "有 cover-image 但缺少 <meta name=\"cover\">（EPUB2 阅读器认不到）"))
        if meta_cover and meta_cover[0] not in items:
            rep.issues.append(Issue("error", "bad-cover-meta",
                                    f"<meta name=\"cover\"> 指向不存在的清单项：{meta_cover[0]}"))

        # ⑤ 逐文档：良构 + 引用可解析
        for it in items.values():
            if it["type"] not in ("application/xhtml+xml", "text/html"):
                continue
            rep.xhtml += 1
            full = posixpath.normpath(posixpath.join(opf_dir, it["href"]))
            if full not in names:
                continue  # ③ 已报过 missing-item，这里再 read 会直接抛 KeyError
            raw = z.read(full)
            try:
                doc = etree.fromstring(raw)
            except etree.XMLSyntaxError as exc:
                rep.issues.append(Issue("error", "malformed", f"XHTML 不是良构 XML：{exc}",
                                        it["href"]))
                continue
            doc_dir = posixpath.dirname(full)
            for ref in _REF.findall(raw.decode("utf-8", "ignore")):
                if ref.startswith(("http:", "https:", "data:", "mailto:", "#", "urn:")):
                    continue
                target = posixpath.normpath(
                    posixpath.join(doc_dir, ref.split("#", 1)[0]))
                if target not in names:
                    rep.issues.append(Issue("error", "dangling-ref",
                                            f"引用了不存在的文件：{ref}", it["href"]))
            # XML 声明里带 BOM 或非法控制字符会让部分阅读器拒收
            if _localname(doc.tag) != "html":
                rep.issues.append(Issue("warning", "not-html",
                                        "根元素不是 html", it["href"]))

        rep.images = sum(1 for n in names
                         if n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")))
    finally:
        z.close()

    if run_epubcheck_check:
        _run_epubcheck(p, rep)
    else:
        rep.epubcheck = "已跳过（仅内置检查）"
    return rep


# ── epubcheck（可选）────────────────────────────────────────────────
#: epubcheck 的 main 类（5.x）
EPUBCHECK_MAIN = "com.adobe.epubcheck.tool.Checker"


def _classpath_install() -> tuple[list[str], Path] | None:
    """Maven Central 装配出来的安装：`Z:\\Tools\\epubcheck-lib\\*.jar` + classpath.txt。"""
    root = Path(r"Z:\Tools\epubcheck-lib")
    cp_file = root / "classpath.txt"
    jars = sorted(root.glob("*.jar"))
    if cp_file.is_file() and jars:
        return [str(p) for p in jars], root
    return None


def find_epubcheck(roots: tuple[str, ...] = (r"Z:\Tools",)) -> Path | None:
    """找一个可用的 epubcheck：fat jar（`-jar` 直接跑）优先，否则返回装配目录里的主 jar。"""
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for cand in sorted(base.glob("epubcheck*/**/epubcheck.jar")):
            if cand.is_file():
                return cand
    install = _classpath_install()
    if install:
        main = next((Path(p) for p in install[0] if "epubcheck-5" in p), None)
        if main:
            return main
    return None


def _epubcheck_argv(epub: Path) -> list[str] | None:
    """构造 epubcheck 命令行；装了哪种形态就用哪种。"""
    install = _classpath_install()
    if install:
        jars, _root = install
        return ["java", "-Duser.language=en", "-cp", os.pathsep.join(jars),
                EPUBCHECK_MAIN, "--json", "-", str(epub)]
    jar = next((c for r in (r"Z:\Tools",) for c in sorted(Path(r).glob("epubcheck*/**/epubcheck.jar"))
                if c.is_file()), None)
    if jar:
        return ["java", "-Duser.language=en", "-jar", str(jar), "--json", "-", str(epub)]
    return None


def _run_epubcheck(epub: Path, rep: ValidateReport, *, timeout: float = 300.0) -> None:
    """跑 epubcheck；不可用就说明原因，**绝不静默通过**。"""
    argv = _epubcheck_argv(epub)
    if argv is None:
        rep.epubcheck = "未安装（可跑 tools/fetch_epubcheck_lib.py 从 Maven Central 装配）"
        return
    if not _has_java():
        rep.epubcheck = "未找到 java"
        return
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="ignore")
    except (OSError, subprocess.TimeoutExpired) as exc:
        rep.epubcheck = f"运行失败：{type(exc).__name__}"
        return
    rep.epubcheck_ran = True
    import json

    blob = proc.stdout or ""
    try:
        data = json.loads(blob[blob.index("{"):blob.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        # JSON 拿不到时退回文本输出（epubcheck 会打印 "No errors or warnings detected."）
        if "No errors or warnings detected" in blob:
            rep.epubcheck = "错误 0 / 警告 0（文本模式）"
        else:
            rep.epubcheck = f"输出无法解析（退出码 {proc.returncode}）"
        return
    msgs = data.get("messages") or []
    fatal = [m for m in msgs if (m.get("severity") or "").lower() in ("fatal", "error")]
    warn = [m for m in msgs if (m.get("severity") or "").lower() == "warning"]
    for m in fatal:
        rep.issues.append(Issue("error", f"epubcheck:{m.get('ID', '')}",
                                (m.get("message") or "")[:220], m.get("path") or ""))
    for m in warn:
        rep.issues.append(Issue("warning", f"epubcheck:{m.get('ID', '')}",
                                (m.get("message") or "")[:220], m.get("path") or ""))
    rep.epubcheck = f"错误 {len(fatal)} / 警告 {len(warn)}"


def _has_java() -> bool:
    import shutil

    return shutil.which("java") is not None
