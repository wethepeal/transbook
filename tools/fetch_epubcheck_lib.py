"""从 Maven Central 装配 epubcheck 的可运行 classpath。

为什么要这样：epubcheck 的 GitHub 发行包在国内常年下不动（实测 25 分钟 0 字节），
而 Maven Central 通畅且给出了完整的依赖清单。这里做一个**最小 Maven 解析器**：
递归取 POM、解析 `${属性}` 与父 POM、按 scope 过滤，逐个下 jar。

产物：`Z:\\Tools\\epubcheck-lib\\*.jar`
用法：python tools/fetch_epubcheck_lib.py
"""

from __future__ import annotations

import pathlib
import re
import sys
import urllib.request
from xml.etree import ElementTree as ET

M2 = "https://repo1.maven.org/maven2"
ROOT = ("org.w3c", "epubcheck", "5.2.1")
OUT = pathlib.Path(r"Z:\Tools\epubcheck-lib")
SKIP_SCOPES = {"test", "provided", "system"}
NS = "{http://maven.apache.org/POM/4.0.0}"


def _text(node, path: str, default: str = "") -> str:
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else default


def fetch(url: str, *, binary: bool) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def pom_url(g: str, a: str, v: str) -> str:
    return f"{M2}/{g.replace('.', '/')}/{a}/{v}/{a}-{v}.pom"


def jar_url(g: str, a: str, v: str) -> str:
    return f"{M2}/{g.replace('.', '/')}/{a}/{v}/{a}-{v}.jar"


def load_pom(g: str, a: str, v: str) -> ET.Element | None:
    try:
        return ET.fromstring(fetch(pom_url(g, a, v), binary=False))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! POM 取不到 {g}:{a}:{v} — {type(exc).__name__}")
        return None


def collect_props(pom: ET.Element, out: dict[str, str], seen: set[str]) -> None:
    """把 POM 及其父链上的 `<properties>` 收进来（版本号常写成 ${x.version}）。"""
    key = f"{_text(pom, f'{NS}groupId')}:{_text(pom, f'{NS}artifactId')}"
    if key in seen:
        return
    seen.add(key)
    props = pom.find(f"{NS}properties")
    if props is not None:
        for c in props:
            tag = c.tag.rsplit("}", 1)[-1]
            if c.text:
                out.setdefault(tag, c.text.strip())
    parent = pom.find(f"{NS}parent")
    if parent is not None:
        pg, pa, pv = (_text(parent, f"{NS}groupId"), _text(parent, f"{NS}artifactId"),
                      _text(parent, f"{NS}version"))
        if pg and pa and pv:
            pp = load_pom(pg, pa, pv)
            if pp is not None:
                collect_props(pp, out, seen)
                dm = pp.find(f"{NS}dependencyManagement")
                if dm is not None:
                    for d in dm.findall(f"{NS}dependencies/{NS}dependency"):
                        k = f"{_text(d, f'{NS}groupId')}:{_text(d, f'{NS}artifactId')}"
                        v2 = _text(d, f"{NS}version")
                        if k and v2:
                            out.setdefault(f"__dm__{k}", v2)


def resolve(g: str, a: str, v: str, level: int = 0,
            acc: dict[tuple[str, str], str] | None = None,
            seen: set[str] | None = None) -> dict[tuple[str, str], str]:
    acc = {} if acc is None else acc
    seen = set() if seen is None else seen
    key = (g, a)
    if key in acc or len(seen) > 400:
        return acc
    pom = load_pom(g, a, v)
    if pom is None:
        return acc
    acc[key] = v
    pv = _text(pom, f"{NS}version") or v
    props: dict[str, str] = {}
    collect_props(pom, props, set())
    props.update({"project.version": pv, "project.groupId": g, "project.artifactId": a})

    dm = pom.find(f"{NS}dependencyManagement")
    dmv: dict[str, str] = {}
    if dm is not None:
        for d in dm.findall(f"{NS}dependencies/{NS}dependency"):
            dmv[f"{_text(d, f'{NS}groupId')}:{_text(d, f'{NS}artifactId')}"] = \
                _text(d, f"{NS}version")

    deps = pom.find(f"{NS}dependencies")
    if deps is None:
        return acc
    for d in deps.findall(f"{NS}dependency"):
        dg, da = _text(d, f"{NS}groupId"), _text(d, f"{NS}artifactId")
        dv = _text(d, f"{NS}version")
        scope = _text(d, f"{NS}scope", "compile")
        optional = _text(d, f"{NS}optional", "false")
        if not dg or not da or scope in SKIP_SCOPES or optional == "true":
            continue
        if not dv:
            dv = dmv.get(f"{dg}:{da}") or props.get(f"__dm__{dg}:{da}") or ""
        for k, val in props.items():
            dv = dv.replace(f"${{{k}}}", val)
        if not dv or "${" in dv:
            print(f"  ~ 跳过（版本无法解析）{dg}:{da}:{dv}")
            continue
        if (dg, da) in acc:
            continue
        print("  " + "  " * level + f"{dg}:{da}:{dv}")
        resolve(dg, da, dv, level + 1, acc, seen)
    return acc


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"解析 {ROOT[0]}:{ROOT[1]}:{ROOT[2]} 的依赖树…")
    deps = resolve(*ROOT)
    print(f"\n共 {len(deps)} 个构件，开始下载到 {OUT}")

    ok = fail = 0
    for (g, a), v in sorted(deps.items()):
        dst = OUT / f"{a}-{v}.jar"
        if dst.exists() and dst.stat().st_size > 0:
            ok += 1
            continue
        try:
            data = fetch(jar_url(g, a, v), binary=True)
            dst.write_bytes(data)
            ok += 1
            print(f"  ✓ {dst.name} ({len(data)//1024} KB)")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print(f"  ✗ {g}:{a}:{v} — {type(exc).__name__} {str(exc)[:60]}")
    print(f"\n完成：成功 {ok} / 失败 {fail}")
    if fail:
        return 1
    cp = ";".join(str(p) for p in sorted(OUT.glob("*.jar")))
    (OUT / "classpath.txt").write_text(cp, encoding="utf-8")
    print(f"classpath 已写入 {OUT / 'classpath.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
