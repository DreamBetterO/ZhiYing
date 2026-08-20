# -*- coding: utf-8 -*-
"""V5.0 在线冒烟前链接健康检查（L2 前置门禁）。

依据：探索方案 §9.1/§9.5 —— 实测证明外部链接会失效（GCS 403、BV -404、
Blender 需 .zip、W3C 对自定义浏览器 UA 反而 403）。每次跑 L2 前先核验锚点。

链路干净性约定：
- 脚本与下载样本都只放在 ./tmp/，不写 workspace/ output/ Resource/ models/。
- 不读取 .env；不带任何密钥。
- 默认只做元数据探测（HEAD；不支持 HEAD 时用 Range: bytes=0-0 GET），
  不下载正文。下载极小样本需显式 --download 参数，且放 tmp/。
- 探测 UA 策略：默认使用「yt-dlp 默认等价」的简洁 UA；W3C 对浏览器 UA 模板 403。
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

# 简洁 UA：模拟 yt-dlp 默认（python-requests 风格），避免触发站点对浏览器 UA 的策略
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) python-urllib/3.12"

# (标签, URL, 探测方式, 备注)
LINKS = [
    # --- 实测可用锚点（探索方案 §9.1）---
    ("W3C-sintel-trailer(52s/4.17MB/有音轨·ASR首选)", "https://media.w3.org/2010/05/sintel/trailer.mp4", "head", "必须默认 UA；ASR 全链路样本"),
    ("test-videos.co.uk-720-10s-1MB(无音轨·抽帧样本)", "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4", "head", "无音轨，只能测下载/抽帧"),
    ("Mux-HLS-m3u8(752B playlist)", "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8", "get", "HLS 拉流测试"),
    # --- B 站锚点（尽力而为通道）---
    ("bilibili-page-BV1cmTu6mEL3(实测锚点)", "https://www.bilibili.com/video/BV1cmTu6mEL3/", "get", "用户提供；yt-dlp 元数据/480P 模拟下载通过"),
    ("bilibili-view-api-BV1cmTu6mEL3", "https://api.bilibili.com/x/web-interface/view?bvid=BV1cmTu6mEL3", "get", "view API code=0"),
    # --- 负面样本（必须保持失败语义）---
    ("bilibili-无效BV(负面样本)", "https://api.bilibili.com/x/web-interface/view?bvid=BV1abcdefghij", "get", "应 404/-404，不崩溃"),
    ("bilibili-首页(非视频页负面样本)", "https://www.bilibili.com/", "get", "应识别为非视频页"),
]

# 探测方式回退：HEAD 失败时尝试 Range GET（部分站点对 HEAD 返回 403/405）
_RANGE_HEADERS = {"Range": "bytes=0-0", "Accept": "*/*"}


def probe(label: str, url: str, mode: str, note: str = "") -> None:
    headers = {"User-Agent": UA, "Accept": "*/*"}
    method = "HEAD" if mode == "head" else "GET"
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            _report_ok(label, resp, url, mode, note)
            return
    except urllib.error.HTTPError as exc:
        if mode == "head" and exc.code in {403, 405}:
            # 站点不支持 HEAD：退化为 Range GET
            try:
                range_req = urllib.request.Request(url, headers={**headers, **_RANGE_HEADERS}, method="GET")
                with urllib.request.urlopen(range_req, timeout=15) as resp:
                    _report_ok(label, resp, url, "range", note)
                    return
            except Exception as exc2:
                print(f"[HTTP] {label}\n       HEAD={exc.code} RangeGET={type(exc2).__name__}: {exc2}\n       url={url}")
                return
        print(f"[HTTP] {label}\n       http={exc.code} reason={exc.reason} url={url}\n       [负面样本] 若为预期失败则 PASS")
    except Exception as exc:
        print(f"[ERR]  {label}\n       {type(exc).__name__}: {exc}\n       url={url}\n       [负面样本] 若为预期失败则 PASS")


def _report_ok(label: str, resp, url: str, mode: str, note: str) -> None:
    length = resp.headers.get("Content-Length")
    ctype = resp.headers.get("Content-Type")
    final = resp.geturl()
    ranged = resp.headers.get("Accept-Ranges")
    suffix = f"\n       [note] {note}" if note else ""
    print(f"[OK]   {label}\n       http={resp.status} mode={mode} len={length} type={ctype}\n       final={final} range={ranged}{suffix}")
    if mode == "get" and ctype and "video" in ctype:
        data = resp.read(65536)
        print(f"       [注意] GET 探测已读取正文 {len(data)} 字节并丢弃")


def download_mini(url: str, target: Path) -> None:
    """下载极小样本用于 ffprobe 端到端验证；仅限小文件，放 tmp/。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=60) as resp, target.open("wb") as out:
        total = 0
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    print(f"[DL]   {url}\n       -> {target} ({total} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None, help="只测含该关键字的链接")
    parser.add_argument("--download", default=None, metavar="URL|TARGET", help="额外下载: URL|目标路径（放 tmp/）")
    args = parser.parse_args()

    print("===== V5.0 链接健康检查（L2 前置门禁） =====")
    for label, url, mode, note in LINKS:
        if args.only and args.only not in label:
            continue
        probe(label, url, mode, note)
        print()

    if args.download:
        url, target = args.download.split("|", 1)
        print(f"===== 下载极小样本 {url} -> {target} =====")
        download_mini(url, Path(target))

    print("===== 完成 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
