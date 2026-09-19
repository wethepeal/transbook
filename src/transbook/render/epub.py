"""EPUB 3 打包（手写，零第三方依赖）。

结构（EPUB 3.0 最小合规集）：
```
mimetype                    ← 必须是第一个条目且不压缩
META-INF/container.xml
OEBPS/content.opf           ← 清单 + 书脊 + 元数据
OEBPS/nav.xhtml             ← 目录（properties="nav"）
OEBPS/style.css
OEBPS/text/ch0001.xhtml …
OEBPS/images/…              ← 原书插图
```

之所以手写而不引入 `EbookLib`：结构简单、完全可控，且避免 AGPL 依赖（计划书 §4.6）。
"""

from __future__ import annotations

import posixpath
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from transbook.render.xhtml import Chapter

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}


def _opf(title: str, author: str, language: str, identifier: str,
         chapters: list[Chapter], images: list[str]) -> bytes:
    pkg = etree.Element(f"{{{OPF_NS}}}package", nsmap={None: OPF_NS, "dc": DC_NS})
    pkg.set("version", "3.0")
    pkg.set("unique-identifier", "bookid")
    pkg.set("{http://www.w3.org/XML/1998/namespace}lang", language)

    meta = etree.SubElement(pkg, f"{{{OPF_NS}}}metadata")
    etree.SubElement(meta, f"{{{DC_NS}}}identifier", id="bookid").text = identifier
    etree.SubElement(meta, f"{{{DC_NS}}}title").text = title or "未命名"
    if author:
        etree.SubElement(meta, f"{{{DC_NS}}}creator").text = author
    etree.SubElement(meta, f"{{{DC_NS}}}language").text = language or "zh"
    etree.SubElement(meta, f"{{{DC_NS}}}publisher").text = "transbook"
    m = etree.SubElement(meta, f"{{{OPF_NS}}}meta", property="dcterms:modified")
    m.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = etree.SubElement(pkg, f"{{{OPF_NS}}}manifest")
    etree.SubElement(manifest, f"{{{OPF_NS}}}item", id="nav", href="nav.xhtml",
                     attrib={"media-type": "application/xhtml+xml", "properties": "nav"})
    etree.SubElement(manifest, f"{{{OPF_NS}}}item", id="css", href="style.css",
                     attrib={"media-type": "text/css"})
    for i, img in enumerate(images, start=1):
        ext = posixpath.splitext(img)[1].lower()
        etree.SubElement(manifest, f"{{{OPF_NS}}}item", id=f"img{i:04d}", href=img,
                         attrib={"media-type": MEDIA.get(ext, "application/octet-stream")})
    for ch in chapters:
        etree.SubElement(manifest, f"{{{OPF_NS}}}item", id=ch.id, href=ch.filename,
                         attrib={"media-type": "application/xhtml+xml"})

    spine = etree.SubElement(pkg, f"{{{OPF_NS}}}spine")
    for ch in chapters:
        etree.SubElement(spine, f"{{{OPF_NS}}}itemref", idref=ch.id)
    return etree.tostring(pkg, xml_declaration=True, encoding="utf-8",
                          doctype='<!DOCTYPE html>')


def write_epub(out_path: str | Path, *, title: str, author: str, language: str,
               chapters: list[Chapter], css: str, nav: bytes,
               images_dir: Path | None = None, identifier: str | None = None) -> Path:
    """写出 EPUB 文件。返回路径。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    image_files: list[tuple[str, Path]] = []
    if images_dir and images_dir.is_dir():
        for p in sorted(images_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in MEDIA:
                image_files.append((f"images/{p.name}", p))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype 必须第一个写入且不压缩
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/style.css", css)
        z.writestr("OEBPS/nav.xhtml", nav)
        for arcname, path in image_files:
            z.write(path, f"OEBPS/{arcname}", compress_type=zipfile.ZIP_STORED)
        for ch in chapters:
            z.writestr(f"OEBPS/{ch.filename}", ch.content)
        z.writestr("OEBPS/content.opf",
                   _opf(title, author, language or "zh",
                        identifier or f"urn:uuid:{uuid.uuid4()}",
                        chapters, [a for a, _ in image_files]))
    return out
