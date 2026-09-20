#!/usr/bin/env python3
"""本地模型吞吐与 OpenAI 兼容性实测（M0 实测 #3/#4）。

两件事：
  ① 用 llama-cli 跑一次真实"日译中"请求，解析 llama.cpp 自报的耗时 →
     得到 **提示处理速度（pp）** 与 **生成速度（tg, tok/s）**，并对比不同 -ngl（GPU 层数）
  ② 用 llama-server 起 OpenAI 兼容端点，POST /v1/chat/completions →
     验证项目 Provider 抽象将来能直接对接，并检查中文输出质量

用法：
  python tools/local_bench.py --model <gguf> [--ngl 99,20] [--tokens 128]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

ENGINE_DIR = pathlib.Path(r"Z:\AgentHub\engines\llama.cpp")
PROMPT = (
    "以下の日本語を自然な中国語に翻訳してください。訳文だけを出力してください。\n\n"
    "原文：夢の城に帰り着いたアルデバランを包んだのは、筆舌に尽くし難い感情だった。\n\n訳文："
)
TIMING = re.compile(r"(prompt eval|eval) time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*(?:runs|tokens).*?([\d.]+)\s*tokens per second", re.S)


def find_exe(name: str) -> pathlib.Path | None:
    if not ENGINE_DIR.exists():
        return None
    for p in ENGINE_DIR.rglob(name):
        if p.is_file():
            return p
    return None


def vram() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return "n/a"


def run_bench(model: pathlib.Path, ngl: int, tokens: int, ctx: int) -> dict:
    """用 llama-bench 测吞吐（比 llama-cli 可靠：clai 新版会进交互模式）。

    返回 pp/tg 两个速度。
    """
    exe = find_exe("llama-bench.exe")
    if not exe:
        return {"error": "未找到 llama-bench.exe"}
    cmd = [str(exe), "-m", str(model), "-ngl", str(ngl), "-p", "128", "-n", str(tokens), "-r", "2"]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": "超时"}
    wall = time.perf_counter() - t0
    rows = [ln for ln in (proc.stdout or "").splitlines() if re.search(r"\|\s*(pp|tg)\d+", ln)]
    pp = tg = None
    for ln in rows:
        cells = [c.strip() for c in ln.split("|") if c.strip()]
        if len(cells) < 6:
            continue
        try:
            val = float(cells[-1].split("±")[0].strip())
        except ValueError:
            continue
        if cells[-2].startswith("pp"):
            pp = val
        elif cells[-2].startswith("tg"):
            tg = val
    backend = "Vulkan(GPU)" if ngl > 0 else "CPU"
    return {"ngl": ngl, "wall": wall, "pp": pp, "tg": tg, "vram": vram(), "backend": backend}


def run_server(model: pathlib.Path, ngl: int, port: int = 18080, ctx: int = 2048) -> dict:
    exe = find_exe("llama-server.exe")
    if not exe:
        return {"error": "未找到 llama-server.exe"}
    proc = subprocess.Popen(
        [str(exe), "-m", str(model), "-ngl", str(ngl), "-c", str(ctx), "--port", str(port), "-np", "1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        else:
            return {"error": "服务未就绪（180 秒）"}

        payload = {
            "model": model.stem,
            "messages": [
                {"role": "system", "content": "你是日译中翻译引擎，只输出译文。"},
                {"role": "user", "content": "原文：夢の城に帰り着いた。 訳文："},
            ],
            "max_tokens": 64, "temperature": 0.2,
            # 关键：Qwen3 是思考型模型，不关掉思考会耗尽 token 且 content 为空
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8"))
        dt = time.perf_counter() - t0
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {"ok": True, "sec": dt, "content": content[:120], "usage": usage,
                "tok_per_s": round(usage.get("completion_tokens", 0) / dt, 1) if dt else None}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ngl", default="99", help="逗号分隔，如 99,24（24=部分卸载）")
    ap.add_argument("--tokens", type=int, default=128)
    ap.add_argument("--ctx", type=int, default=2048)
    ap.add_argument("--skip-server", action="store_true")
    args = ap.parse_args()

    model = pathlib.Path(args.model)
    print(f"模型: {model}  ({model.stat().st_size / 1024**3:.2f} GB)" if model.exists() else f"模型不存在: {model}")
    print(f"引擎目录: {ENGINE_DIR}  存在={ENGINE_DIR.exists()}")
    for n in ("llama-cli.exe", "llama-server.exe"):
        print(f"  {n}: {find_exe(n)}")

    print("\n【吞吐实测】")
    print(f"{'ngl':>5}{'墙钟(秒)':>10}{'提示处理(tok/s)':>18}{'生成(tok/s)':>14}  后端")
    for ngl in (int(x) for x in args.ngl.split(",")):
        r = run_bench(model, ngl, args.tokens, args.ctx)
        if "error" in r:
            print(f"{ngl:>5}{'FAIL':>10}  {r['error']}")
            continue
        pp = f"{r['pp']:.1f}" if r["pp"] else "—"
        tg = f"{r['tg']:.2f}" if r["tg"] else "—"
        print(f"{ngl:>5}{r['wall']:>10.1f}{pp:>18}{tg:>14}  {r.get('backend','')}")

    if not args.skip_server:
        print("\n【OpenAI 兼容端点实测】")
        s = run_server(model, int(args.ngl.split(",")[0]))
        if s.get("error"):
            print("  FAIL:", s["error"])
        else:
            print(f"  延迟 {s['sec']:.1f}s ｜ {s['tok_per_s']} tok/s ｜ usage={s['usage']}")
            print(f"  输出: {s['content']!r}")


if __name__ == "__main__":
    sys.exit(main())
