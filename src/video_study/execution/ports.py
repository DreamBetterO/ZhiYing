from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


class ProcessPort(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cancel_check: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
        cwd: Path | None = None,
    ) -> Any: ...


class MediaPort(Protocol):
    def probe(self, video: Path) -> Mapping[str, Any]: ...
    def extract_audio(self, video: Path, output: Path, *, cancel_check: Callable[[], bool]) -> Path: ...
    def extract_frame_candidates(
        self,
        video: Path,
        output_dir: Path,
        options: Mapping[str, Any],
        *,
        cancel_check: Callable[[], bool],
    ) -> Mapping[str, Any]: ...


class SourcePort(Protocol):
    """视频链接源获取端口：预检（爬虫式查找+判定）与完整下载（含完整性校验）。"""

    def preflight(
        self,
        url: str,
        *,
        options: Mapping[str, Any] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]: ...

    def acquire(
        self,
        candidate: Mapping[str, Any],
        target: Path,
        *,
        options: Mapping[str, Any] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]: ...


class SpeechPort(Protocol):
    def probe_capability(self) -> Mapping[str, Any]: ...
    def decode(
        self,
        audio: Path,
        output: Path,
        options: Mapping[str, Any],
        *,
        cancel_check: Callable[[], bool],
        progress: Callable[[float], None] | None = None,
    ) -> Mapping[str, Any]: ...


class VisionSession(Protocol):
    def compare(self, job: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def detail(self, job: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def close(self) -> None: ...


class VisionPort(Protocol):
    def preflight(self) -> Mapping[str, Any]: ...
    def open_session(self, options: Mapping[str, Any]) -> VisionSession: ...


class CloudJsonPort(Protocol):
    def request_json(
        self,
        payload: Mapping[str, Any],
        *,
        validator: Callable[[Mapping[str, Any]], Any],
        stage: str,
        cancel_check: Callable[[], bool],
    ) -> Mapping[str, Any]: ...

    def request_json_with_info(
        self,
        payload: Mapping[str, Any],
        *,
        validator: Callable[[Mapping[str, Any]], Any],
        stage: str,
        cancel_check: Callable[[], bool],
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]: ...


class DocumentPort(Protocol):
    def render_markdown(self, document: Mapping[str, Any], output: Path) -> Path: ...
    def render_word(self, document_json: Path, output: Path, *, cancel_check: Callable[[], bool]) -> Path: ...
    def render_pdf(
        self,
        document: Mapping[str, Any],
        word: Path,
        output: Path,
        *,
        cancel_check: Callable[[], bool],
    ) -> str: ...
