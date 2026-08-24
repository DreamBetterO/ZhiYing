"""视频链接源获取内核（V5.0 SourcePort 具体实现）。

职责（执行计划 P1）：
- preflight(url)：输入清洗/形态识别 -> URL 规范化 -> yt-dlp ``-J`` 预检 -> 按 §4.2
  分类映射 ``SOURCE_*`` 错误码，产出 SourceCandidate。
- acquire(candidate, target)：yt-dlp exe 完整下载（格式自动降级链、断点续传、
  可取消、两段进度）-> 下载后 ffprobe 时长一致性校验（防截断，呼应
  ``audio_extract_incomplete`` 质量门）。
- check_tools()：yt-dlp/ffmpeg/ffprobe 工具链探测。

集成形态（D5 决策）：独立 ``tools/yt-dlp.exe``，subprocess 调用，进度用
``--newline`` + 行解析；不引入 Python 依赖。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from .runtime import find_tool
from .utils import TaskCancelled, background_process_kwargs, terminate_process

# 错误码族（与 docs/diagnostics/problem-index.yaml 登记一致）
URL_INVALID = "URL_INVALID"
SOURCE_UNSUPPORTED_SITE = "SOURCE_UNSUPPORTED_SITE"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
SOURCE_NOT_VIDEO = "SOURCE_NOT_VIDEO"
SOURCE_LOGIN_REQUIRED = "SOURCE_LOGIN_REQUIRED"
SOURCE_REGION_LOCKED = "SOURCE_REGION_LOCKED"
SOURCE_DRM = "SOURCE_DRM"
SOURCE_LIVE_UNSUPPORTED = "SOURCE_LIVE_UNSUPPORTED"
DOWNLOAD_TIMEOUT = "DOWNLOAD_TIMEOUT"
DOWNLOAD_TOO_LARGE = "DOWNLOAD_TOO_LARGE"
DOWNLOAD_INCOMPLETE = "DOWNLOAD_INCOMPLETE"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"

SOURCE_ERROR_CODES = frozenset({
    URL_INVALID, SOURCE_UNSUPPORTED_SITE, SOURCE_UNAVAILABLE, SOURCE_NOT_VIDEO,
    SOURCE_LOGIN_REQUIRED, SOURCE_REGION_LOCKED, SOURCE_DRM,
    SOURCE_LIVE_UNSUPPORTED, DOWNLOAD_TIMEOUT, DOWNLOAD_TOO_LARGE,
    DOWNLOAD_INCOMPLETE, DOWNLOAD_FAILED,
})

DEFAULT_FORMAT = "bv*[height<=1080]+ba/b"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = "10"
FRAGMENT_RETRIES = "10"
SOCKET_TIMEOUT = "30"

_BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_PATTERN = re.compile(r"(?:^|[\s/])(av\d+)")
_URL_PATTERN = re.compile(r"(https?://[^\s<>\"'）】]+)")


class SourceError(RuntimeError):
    """源获取失败；code 属于 SOURCE_ERROR_CODES，message 为用户可读中文文案。"""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def extract_url_or_id(text: str) -> str:
    """从任意用户输入中提取 URL 或 B 站 BV/av 号；提取不到返回空串。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = _URL_PATTERN.search(raw)
    if match:
        return match.group(1).rstrip(".,;:!?）)]}")
    bv = _BV_PATTERN.search(raw)
    if bv:
        return f"https://www.bilibili.com/video/{bv.group(1)}"
    av = _AV_PATTERN.search(raw)
    if av:
        return f"https://www.bilibili.com/video/{av.group(1)}"
    return ""


def normalize_url(raw: str) -> str:
    """规范化 URL：补协议、去空白与尾部标点。短链/跟踪参数交由 yt-dlp 处理。"""
    candidate = str(raw or "").strip()
    if not candidate:
        return ""
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = "https://" + candidate
    candidate = candidate.rstrip(".,;:!?）)]}。，；：！？")
    parsed = urlparse(candidate)
    if not parsed.hostname or "." not in parsed.hostname:
        return ""
    return candidate


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _match_deny(host: str, deny_sites: Sequence[str]) -> str | None:
    for site in deny_sites:
        site = str(site or "").strip().lower()
        if not site:
            continue
        if host == site or host.endswith("." + site):
            return site
    return None


def classify_ytdlp_error(stderr_text: str) -> tuple[str, str]:
    """把 yt-dlp 预检/下载 stderr 信号分类映射到 SOURCE_* 错误码（探索方案 §8.2）。"""
    text = stderr_text or ""
    lowered = text.lower()
    if "unsupported url" in lowered:
        return SOURCE_UNSUPPORTED_SITE, "未识别到视频内容，请粘贴视频页面链接或 BV 号"
    if "not available in your country" in lowered or "geo-restricted" in lowered or "region" in lowered:
        return SOURCE_REGION_LOCKED, "该内容受地区/版权限制，当前网络无法访问"
    if "http error 404" in lowered or "video unavailable" in lowered or "not found" in lowered:
        return SOURCE_UNAVAILABLE, "链接失效或视频已下架，请检查链接是否正确"
    if "premium member" in lowered or "become a member" in lowered or "login" in lowered or "sign in" in lowered:
        return SOURCE_LOGIN_REQUIRED, "该视频需要登录或会员身份才能获取，当前未提供登录态"
    if "drm" in lowered:
        return SOURCE_DRM, "该内容含 DRM/付费保护，无法下载"
    if "http error 412" in lowered or "precondition failed" in lowered or "429" in lowered or "too many requests" in lowered:
        return DOWNLOAD_TIMEOUT, "站点临时风控或请求过于频繁，请稍后重试"
    if "timed out" in lowered or "connection" in lowered or "couldn't connect" in lowered or "resolve" in lowered:
        return DOWNLOAD_TIMEOUT, "无法连接视频站点，请检查网络后重试"
    return SOURCE_UNAVAILABLE, "链接失效或视频已下架，请检查链接是否正确"


def parse_progress_line(line: str) -> Mapping[str, Any] | None:
    """解析 yt-dlp ``--newline`` 进度行（下载两段：download / merge）。"""
    text = line.strip()
    if not text:
        return None
    if "[download]" in text:
        percent_match = re.search(r"(\d+(?:\.\d+)?)%", text)
        size_match = re.search(r"of\s+~?([\d.]+)\s*(K|M|G)iB", text)
        speed_match = re.search(r"at\s+([\d.]+)\s*(K|M|G)iB/s", text)
        if not percent_match:
            return None
        unit = {"K": 1024, "M": 1024**2, "G": 1024**3}
        total_bytes = None
        if size_match:
            total_bytes = int(float(size_match.group(1)) * unit[size_match.group(2)])
        speed_bytes = None
        if speed_match:
            speed_bytes = int(float(speed_match.group(1)) * unit[speed_match.group(2)])
        return {
            "phase": "download",
            "percent": float(percent_match.group(1)),
            "total_bytes": total_bytes,
            "speed_bytes": speed_bytes,
        }
    if "[Merger]" in text or "[ExtractAudio]" in text or "[VideoConvertor]" in text:
        return {"phase": "merge", "percent": None, "total_bytes": None, "speed_bytes": None}
    return None


def resolve_downloaded_file(target: Path) -> Path | None:
    """yt-dlp ``-o`` 模板可能追加扩展名；按目标文件名前缀扫描实际产物。"""
    expected = [target, target.with_suffix(".mp4"), target.with_suffix(".mkv")]
    for candidate in expected:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    if target.parent.is_dir():
        matches = sorted(
            (path for path in target.parent.glob(target.name + ".*") if path.is_file()),
            key=lambda path: path.stat().st_size,
            reverse=True,
        )
        if matches:
            return matches[0]
    return None


def _format_duration_check(expected: float, actual: float) -> bool:
    """时长一致性阈值：偏差 >2% 或 >3s 判为下载截断（探索方案 §5.1 第 5 步）。"""
    if expected <= 0:
        return True
    return abs(actual - expected) <= max(3.0, expected * 0.02)


class YtDlpSourceAdapter:
    """SourcePort 具体实现：封装 tools/yt-dlp.exe。

    ``runner``（可选）注入预检/下载进程执行器；``probe``（可选）注入 ffprobe。
    离线测试通过注入 fake runner/probe 完成 L0，不触碰真实网络。
    """

    def __init__(
        self,
        *,
        tool: str | None = None,
        probe: Callable[[Path], Mapping[str, Any]] | None = None,
        runner: Any = None,
    ) -> None:
        self.tool = tool or find_tool("yt-dlp") or "yt-dlp"
        self._probe_impl = probe
        self._runner = runner

    # ---- 工具链 ----

    def check_tools(self) -> Mapping[str, Any]:
        from .media import probe_video

        result: dict[str, Any] = {
            "yt_dlp": bool(find_tool("yt-dlp")),
            "ffmpeg": bool(find_tool("ffmpeg")),
            "ffprobe": bool(find_tool("ffprobe")),
        }
        if result["yt_dlp"]:
            try:
                version = subprocess.run(
                    [self.tool, "--version"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=10, check=True, **background_process_kwargs(),
                ).stdout.strip()
                result["yt_dlp_version"] = version.splitlines()[0] if version else "unknown"
            except (OSError, subprocess.SubprocessError):
                result["yt_dlp_version"] = None
        result["_probe_ready"] = callable(probe_video) if self._probe_impl is None else True
        return result

    # ---- 预检 ----

    def preflight(
        self,
        url: str,
        *,
        options: Mapping[str, Any] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        options = dict(options or {})
        raw = extract_url_or_id(url)
        if not raw:
            raise SourceError(URL_INVALID, "未识别到有效的视频链接，请粘贴视频页面链接、直链或 BV 号")
        normalized = normalize_url(raw)
        if not normalized:
            raise SourceError(URL_INVALID, "链接格式无效，请检查后重试")
        host = host_of(normalized)
        if "douyin" in host:
            raise SourceError(
                SOURCE_UNSUPPORTED_SITE, "暂不支持抖音链接（平台接口签名限制，V5.0 首版不提供）",
                details={"host": host},
            )
        denied = _match_deny(host, options.get("deny_sites") or [])
        if denied:
            raise SourceError(
                SOURCE_UNSUPPORTED_SITE, f"该站点（{denied}）已被配置为不允许下载",
                details={"denied_site": denied},
            )

        command = self._preflight_command(normalized, options)
        try:
            result = self._run(command, timeout_seconds=float(
                options.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            ), cancel_check=cancel_check)
        except TaskCancelled:
            raise
        except subprocess.TimeoutExpired as exc:
            raise SourceError(DOWNLOAD_TIMEOUT, "预检超时，无法连接视频站点，请检查网络后重试", details={"url": normalized}) from exc
        except SourceError:
            raise
        except Exception as exc:
            code, message = classify_ytdlp_error(str(exc))
            raise SourceError(code, message, details={"url": normalized}) from exc

        try:
            info = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SourceError(SOURCE_UNAVAILABLE, "预检响应无法解析，请稍后重试", details={"url": normalized}) from exc
        if not isinstance(info, dict) or not info:
            raise SourceError(SOURCE_UNAVAILABLE, "未获取到视频信息，链接可能已失效", details={"url": normalized})

        if bool(info.get("is_live")) or str(info.get("live_status", "") or "").lower() in {"is_live", "is_upcoming"}:
            raise SourceError(SOURCE_LIVE_UNSUPPORTED, "暂不支持直播内容", details={"url": normalized})

        formats = info.get("formats") or []
        drm = bool(info.get("_has_drm")) or any(bool(item.get("_has_drm")) for item in formats)
        if drm or not formats and not info.get("url"):
            raise SourceError(SOURCE_DRM, "该内容含 DRM/付费保护，无法下载", details={"url": normalized})

        size_bytes = _estimate_size(info)
        max_bytes = options.get("max_bytes")
        if max_bytes and size_bytes and size_bytes > int(max_bytes):
            raise SourceError(
                DOWNLOAD_TOO_LARGE, f"预估体积过大（{size_bytes / (1024 ** 3):.2f} GiB），已超过单视频上限",
                details={"size_bytes": size_bytes, "max_bytes": int(max_bytes)},
            )

        return {
            "url": normalized,
            "video_id": str(info.get("id") or _stable_id(normalized)),
            "title": str(info.get("title") or Path(normalized).name or "视频"),
            "duration_seconds": float(info.get("duration") or 0.0),
            "size_bytes": size_bytes,
            "extractor": str(info.get("extractor_key") or ""),
            "is_live": False,
            "drm": False,
            "best_format": _best_format_summary(info),
            "webpage_url": str(info.get("webpage_url") or normalized),
        }

    # ---- 下载 ----

    def acquire(
        self,
        candidate: Mapping[str, Any],
        target: Path,
        *,
        options: Mapping[str, Any] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]:
        options = dict(options or {})
        url = str(candidate.get("url") or "")
        if not url:
            raise SourceError(URL_INVALID, "候选来源缺少有效链接")
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        command = self._acquire_command(url, target, options)
        try:
            self._run_download(command, cancel_check=cancel_check, progress=progress)
        except TaskCancelled:
            raise
        except subprocess.TimeoutExpired as exc:
            raise SourceError(DOWNLOAD_TIMEOUT, "下载超时，请检查网络后重试", details={"url": url}) from exc
        except SourceError:
            raise
        except Exception as exc:
            code, message = classify_ytdlp_error(str(exc))
            raise SourceError(code, message, details={"url": url}) from exc

        downloaded = resolve_downloaded_file(target)
        if downloaded is None:
            raise SourceError(DOWNLOAD_FAILED, "下载未产生有效文件，请重试", details={"url": url})

        actual_duration = 0.0
        try:
            probe = self._probe(downloaded)
            actual_duration = max(0.0, float(probe.get("format", {}).get("duration", 0.0) or 0.0))
        except Exception as exc:
            raise SourceError(DOWNLOAD_FAILED, "下载完成但无法读取本地文件，请重试", details={"url": url}) from exc

        expected_duration = float(candidate.get("duration_seconds") or 0.0)
        if not _format_duration_check(expected_duration, actual_duration):
            raise SourceError(
                DOWNLOAD_INCOMPLETE,
                "下载文件不完整（时长与源视频不一致），已停止处理以避免生成截断产物",
                details={
                    "url": url, "expected_duration_seconds": expected_duration,
                    "actual_duration_seconds": actual_duration,
                },
            )

        return {
            "path": str(downloaded),
            "url": url,
            "video_id": str(candidate.get("video_id") or ""),
            "title": str(candidate.get("title") or downloaded.stem),
            "duration_seconds": actual_duration,
            "size_bytes": downloaded.stat().st_size,
            "extractor": str(candidate.get("extractor") or ""),
            "format": str(options.get("format") or DEFAULT_FORMAT),
        }

    # ---- 内部 ----

    def _preflight_command(self, url: str, options: Mapping[str, Any]) -> list[str]:
        command = [self.tool, "--dump-json", "--no-warnings", "--no-playlist"]
        cookies = str(options.get("cookies_file") or "").strip()
        if cookies:
            command += ["--cookies", cookies]
        user_agent = str(options.get("user_agent") or "").strip()
        if user_agent:
            command += ["--user-agent", user_agent]
        command.append(url)
        return command

    def _acquire_command(self, url: str, target: Path, options: Mapping[str, Any]) -> list[str]:
        fmt = str(options.get("format") or DEFAULT_FORMAT)
        command = [
            self.tool, "-f", fmt,
            "--merge-output-format", "mp4",
            "--continue",
            "--retries", str(options.get("retries", MAX_RETRIES)),
            "--fragment-retries", str(options.get("fragment_retries", FRAGMENT_RETRIES)),
            "--socket-timeout", str(options.get("socket_timeout", SOCKET_TIMEOUT)),
            "--no-warnings",
            "--newline",
        ]
        max_bytes = options.get("max_bytes")
        if max_bytes:
            command += ["--max-filesize", str(int(max_bytes))]
        cookies = str(options.get("cookies_file") or "").strip()
        if cookies:
            command += ["--cookies", cookies]
        user_agent = str(options.get("user_agent") or "").strip()
        if user_agent:
            command += ["--user-agent", user_agent]
        command += ["-o", str(target), url]
        return command

    def _run(self, command: Sequence[str], *, timeout_seconds: float, cancel_check: Callable[[], bool] | None) -> subprocess.CompletedProcess[str]:
        if self._runner is not None:
            result = self._runner.run(command, timeout_seconds=timeout_seconds, cancel_check=cancel_check)
            completed = subprocess.CompletedProcess(
                list(command), int(result.returncode), str(result.stdout or ""), str(result.stderr or ""),
            )
            if completed.returncode:
                raise RuntimeError(str(result.stderr or "").strip() or f"exit code {completed.returncode}")
            return completed
        process = subprocess.Popen(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **background_process_kwargs(),
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            terminate_process(process)
            raise
        if cancel_check and cancel_check():
            raise TaskCancelled("任务已由用户取消")
        if process.returncode:
            raise RuntimeError(stderr.strip() or f"exit code {process.returncode}")
        return subprocess.CompletedProcess(list(command), 0, stdout, stderr)

    def _run_download(
        self,
        command: Sequence[str],
        *,
        cancel_check: Callable[[], bool] | None,
        progress: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        if self._runner is not None:
            tail = self._runner.run_download(
                command, cancel_check=cancel_check, progress=progress,
            )
            if tail:
                raise RuntimeError(tail)
            return
        process = subprocess.Popen(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", **background_process_kwargs(),
        )
        tail: list[str] = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if cancel_check and cancel_check():
                    terminate_process(process)
                    raise TaskCancelled("任务已由用户取消")
                tail.append(line)
                del tail[:-200]
                event = parse_progress_line(line)
                if event and progress:
                    progress(event)
            returncode = process.wait()
        finally:
            if process.poll() is None:
                terminate_process(process)
        if returncode:
            raise RuntimeError("".join(tail).strip() or f"exit code {returncode}")

    def _probe(self, path: Path) -> Mapping[str, Any]:
        if self._probe_impl is not None:
            return self._probe_impl(path)
        from .media import probe_video
        return probe_video(path)


def _estimate_size(info: Mapping[str, Any]) -> int | None:
    for key in ("filesize_approx", "filesize"):
        value = info.get(key)
        if value:
            return int(value)
    formats = info.get("formats") or []
    if formats:
        values = [int(item[key]) for item in formats for key in ("filesize_approx", "filesize") if item.get(key)]
        if values:
            return max(values)
    return None


def _stable_id(url: str) -> str:
    import hashlib
    return "url-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _best_format_summary(info: Mapping[str, Any]) -> Mapping[str, Any]:
    formats = info.get("formats") or []
    best: dict[str, Any] = {"format_id": None, "height": None, "fps": None, "ext": None}
    for item in formats:
        height = item.get("height") or 0
        if height and int(height) > int(best["height"] or 0):
            best = {
                "format_id": item.get("format_id"), "height": int(height),
                "fps": item.get("fps"), "ext": item.get("ext"),
            }
    return best
