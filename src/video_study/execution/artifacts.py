from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ProcessingContext
    from .contracts import StepOutcome, StepSpec


@dataclass(frozen=True, order=True)
class ArtifactId:
    name: str
    relative_paths: tuple[str, ...]
    storage_root: str = "workspace"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ArtifactId.name 不能为空")
        if self.storage_root not in {"workspace", "output"}:
            raise ValueError(f"未知 Artifact storage_root：{self.storage_root}")
        paths = tuple(dict.fromkeys(path.replace("\\", "/") for path in self.relative_paths))
        if not paths:
            raise ValueError("ArtifactId 必须声明至少一个相对路径")
        for path in paths:
            pure = PurePosixPath(path)
            if pure.is_absolute() or PureWindowsPath(path).is_absolute() or ".." in pure.parts:
                raise ValueError(f"Artifact 路径必须位于 Workspace 内：{path}")
        object.__setattr__(self, "relative_paths", paths)


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: ArtifactId
    path: Path
    digest: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class WorkspaceLayout:
    root: Path
    video_id: str
    output_root: Path | None = None

    def __post_init__(self) -> None:
        if not self.video_id.strip() or self.video_id in {".", ".."}:
            raise ValueError("video_id 不是安全的 Workspace 名称")
        object.__setattr__(self, "root", self.root.expanduser().resolve())
        if self.output_root is not None:
            object.__setattr__(self, "output_root", self.output_root.expanduser().resolve())

    @property
    def video_root(self) -> Path:
        return self.root / self.video_id

    @property
    def state_dir(self) -> Path:
        return self.video_root / "state"

    def artifact_paths(self, artifact_id: ArtifactId) -> tuple[Path, ...]:
        if artifact_id.storage_root == "output":
            if self.output_root is None:
                raise ValueError("当前 WorkspaceLayout 未配置 output_root")
            base = self.output_root / self.video_id
        else:
            base = self.video_root
        return tuple(base / Path(path) for path in artifact_id.relative_paths)

    def staging_dir(self, run_id: str, step_id: str) -> Path:
        safe_step = step_id.replace("/", "_").replace("\\", "_")
        return self.state_dir / "staging" / run_id / safe_step


class ArtifactStore(Protocol):
    def staging_dir(self, context: "ProcessingContext", step_id: str) -> Path: ...

    def validate(self, context: "ProcessingContext", artifact: ArtifactRef) -> bool: ...

    def commit(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        outcome: "StepOutcome",
        staging_dir: Path,
    ) -> tuple[ArtifactRef, ...]: ...

    def cleanup_staging(self, context: "ProcessingContext", step_id: str) -> None: ...


SOURCE_MANIFEST = ArtifactId("source.manifest", ("manifest.json",))
AUDIO_FLAC = ArtifactId("audio.flac", ("audio/audio.flac",))
TRANSCRIPT_RAW = ArtifactId("transcript.raw", ("transcript/raw.json",))
TRANSCRIPT_NORMALIZED = ArtifactId("transcript.normalized", ("transcript/transcript.json",))
TRANSCRIPT_SRT = ArtifactId("transcript.srt", ("transcript/transcript.srt",))
FRAMES_CANDIDATES = ArtifactId("frames.candidates", ("images/candidates.json", "images/candidates"))
FRAMES_SELECTED = ArtifactId("frames.selected", ("images/keyframes.json", "images/selected"))
KNOWLEDGE_PLAN = ArtifactId("knowledge.plan", ("knowledge/lesson-plan.json",))
VISUAL_JOBS = ArtifactId("visual.jobs", ("knowledge/visual-jobs/index.json",))
VISUAL_EVIDENCE = ArtifactId("visual.evidence", ("knowledge/visual-evidence.json",))
FRAMES_SEMANTICS = ArtifactId("frames.semantics", ("knowledge/frame-semantics.json",))
KNOWLEDGE_COURSE_IR = ArtifactId("knowledge.course_ir", ("knowledge/course-ir.json",))
KNOWLEDGE_UNITS = ArtifactId("knowledge.units", ("knowledge/knowledge-units.json",))
KNOWLEDGE_SELFCHECK = ArtifactId("knowledge.selfcheck", ("knowledge/selfcheck.json",))
DOCUMENT_V2 = ArtifactId("document.v2", ("knowledge/document.json",))

STANDARD_ARTIFACTS: Mapping[str, ArtifactId] = MappingProxyType({
    artifact.name: artifact for artifact in (
        SOURCE_MANIFEST, AUDIO_FLAC, TRANSCRIPT_RAW, TRANSCRIPT_NORMALIZED,
        TRANSCRIPT_SRT, FRAMES_CANDIDATES, FRAMES_SELECTED, KNOWLEDGE_PLAN,
        VISUAL_JOBS, VISUAL_EVIDENCE, FRAMES_SEMANTICS, KNOWLEDGE_COURSE_IR,
        KNOWLEDGE_UNITS, KNOWLEDGE_SELFCHECK, DOCUMENT_V2,
    )
})


def canonical_json_bytes(value: Any) -> bytes:
    def plain(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): plain(child) for key, child in item.items()}
        if isinstance(item, (tuple, list, set, frozenset)):
            return [plain(child) for child in item]
        if isinstance(item, Path):
            return str(item)
        return item
    return json.dumps(
        plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def artifact_file_digest(artifact_id: ArtifactId, path: Path) -> str:
    if artifact_id == SOURCE_MANIFEST:
        value = json.loads(path.read_text(encoding="utf-8"))
        return canonical_json_hash({
            key: value.get(key)
            for key in (
                "schema_version", "video_id", "source_path", "fingerprint",
                "duration_seconds", "size_bytes", "probe",
            )
        })
    return file_digest(path)


class JsonArtifactValidator:
    def __init__(self, required_keys: Iterable[str] = ()) -> None:
        self.required_keys = tuple(required_keys)

    def __call__(self, paths: tuple[Path, ...]) -> None:
        if len(paths) != 1 or not paths[0].is_file():
            raise ValueError("JSON Artifact 文件缺失")
        value = json.loads(paths[0].read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON Artifact 顶层必须是对象")
        missing = [key for key in self.required_keys if key not in value]
        if missing:
            raise ValueError(f"JSON Artifact 缺少字段：{', '.join(missing)}")


class ExistingArtifactValidator:
    def __call__(self, paths: tuple[Path, ...]) -> None:
        for path in paths:
            if not path.exists():
                raise ValueError(f"Artifact 输出缺失：{path.name}")
            if path.is_file() and path.stat().st_size == 0:
                raise ValueError(f"Artifact 输出为空：{path.name}")


class FrameIndexValidator:
    def __call__(self, paths: tuple[Path, ...]) -> None:
        ExistingArtifactValidator()(paths)
        value = json.loads(paths[0].read_text(encoding="utf-8"))
        rows = value.get("frames")
        if not isinstance(rows, list):
            raise ValueError("帧 Artifact 缺少 frames 清单")
        for row in rows:
            image_path = Path(str(row.get("path", "")))
            staged_image = paths[1] / image_path.name if len(paths) > 1 else image_path
            candidate = image_path if image_path.is_file() else staged_image
            if not candidate.is_file() or candidate.stat().st_size == 0:
                raise ValueError(f"帧 Artifact 引用图片缺失：{image_path.name}")


class CandidateIndexValidator:
    def __call__(self, paths: tuple[Path, ...]) -> None:
        ExistingArtifactValidator()(paths)
        value = json.loads(paths[0].read_text(encoding="utf-8"))
        rows = value.get("candidates")
        if not isinstance(rows, list) or not rows:
            raise ValueError("候选帧 Artifact 缺少 candidates 清单")
        for row in rows:
            image = paths[1] / str(row.get("file", ""))
            if not image.is_file() or image.stat().st_size == 0:
                raise ValueError(f"候选帧 Artifact 引用图片缺失：{image.name}")


class DocumentV2Validator:
    def __call__(self, paths: tuple[Path, ...]) -> None:
        JsonArtifactValidator(("schema_version", "sections"))(paths)
        value = json.loads(paths[0].read_text(encoding="utf-8"))
        validate_document_v2(value)


def validate_document_v2(value: Mapping[str, Any]) -> None:
    if int(value.get("schema_version", 0)) != 2:
        raise ValueError("Canonical Document 必须是 schema v2")
    for section in value.get("sections", []):
        for point in section.get("knowledge_points", []):
            if not isinstance(point.get("content_blocks"), list):
                raise ValueError("Document v2 知识点缺少 content_blocks")
            legacy = {
                "explanation", "details", "steps", "examples", "conditions",
                "pitfalls", "editorial_note", "review_tip", "source_url",
                "source_label", "source_links", "source_segment_ids",
                "start_seconds", "end_seconds",
            }.intersection(point)
            if legacy:
                raise ValueError(f"Document v2 含旧正文/来源副本：{', '.join(sorted(legacy))}")
            if not isinstance(point.get("source_refs"), dict):
                raise ValueError("Document v2 知识点缺少 source_refs")


def read_document_v2(path: Path) -> dict[str, Any]:
    """读取 Canonical Document；v1 仅在内存中单向迁移。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Document 顶层必须是对象")
    if int(value.get("schema_version", 1) or 1) != 2:
        from ..knowledge.adapter import v1_to_v2
        value = v1_to_v2(value)
    validate_document_v2(value)
    return value


ARTIFACT_VALIDATORS: Mapping[str, Callable[[tuple[Path, ...]], None]] = MappingProxyType({
    SOURCE_MANIFEST.name: JsonArtifactValidator(("video_id", "source_path", "fingerprint")),
    TRANSCRIPT_RAW.name: JsonArtifactValidator(("segments",)),
    TRANSCRIPT_NORMALIZED.name: JsonArtifactValidator(("segments",)),
    FRAMES_CANDIDATES.name: CandidateIndexValidator(),
    FRAMES_SELECTED.name: FrameIndexValidator(),
    KNOWLEDGE_PLAN.name: JsonArtifactValidator(),
    VISUAL_JOBS.name: JsonArtifactValidator(),
    VISUAL_EVIDENCE.name: JsonArtifactValidator(),
    FRAMES_SEMANTICS.name: JsonArtifactValidator(),
    KNOWLEDGE_COURSE_IR.name: JsonArtifactValidator(),
    KNOWLEDGE_UNITS.name: JsonArtifactValidator(),
    KNOWLEDGE_SELFCHECK.name: JsonArtifactValidator(),
    DOCUMENT_V2.name: DocumentV2Validator(),
})


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(canonical_json_bytes(value) + b"\n")
        json.loads(temporary.read_text(encoding="utf-8"))
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt >= 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


class FileArtifactStore:
    def __init__(
        self,
        validators: Mapping[str, Callable[[tuple[Path, ...]], None]] | None = None,
    ) -> None:
        self.validators = dict(ARTIFACT_VALIDATORS)
        self.validators.update(validators or {})

    def staging_dir(self, context: "ProcessingContext", step_id: str) -> Path:
        staging = context.workspace.staging_dir(context.run_id, step_id)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        return staging

    def write_document_v2(self, path: Path, document: Mapping[str, Any]) -> Path:
        """原子写入独立的 Canonical Document，供聚合等用例复用。"""
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(canonical_json_bytes(document) + b"\n")
            DocumentV2Validator()((temporary,))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def validate(self, context: "ProcessingContext", artifact: ArtifactRef) -> bool:
        paths = context.workspace.artifact_paths(artifact.artifact_id)
        try:
            self._validator(artifact.artifact_id)(paths)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return True

    def commit(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        outcome: "StepOutcome",
        staging_dir: Path,
    ) -> tuple[ArtifactRef, ...]:
        staged_by_id = {ref.artifact_id: ref for ref in outcome.artifacts}
        if set(staged_by_id) != set(spec.outputs):
            raise ValueError("StepOutcome 输出与 StepSpec 声明不一致")
        pending: list[tuple[ArtifactId, tuple[Path, ...], tuple[Path, ...]]] = []
        for artifact_id in spec.outputs:
            staged = tuple(staging_dir / Path(path) for path in artifact_id.relative_paths)
            self._validator(artifact_id)(staged)
            targets = context.workspace.artifact_paths(artifact_id)
            pending.append((artifact_id, staged, targets))

        backups = context.workspace.state_dir / "staging" / context.run_id / ".backup" / spec.step_id
        moved_backups: list[tuple[Path, Path]] = []
        moved_targets: list[Path] = []
        try:
            for artifact_id, _staged, targets in pending:
                for target in targets:
                    if not target.exists():
                        continue
                    if artifact_id.storage_root == "output":
                        output_root = context.workspace.output_root
                        if output_root is None:
                            raise ValueError("输出 Artifact 缺少 output_root")
                        base = output_root / context.workspace.video_id
                    else:
                        base = context.workspace.video_root
                    relative = target.relative_to(base)
                    backup = backups / artifact_id.storage_root / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(backup)
                    moved_backups.append((backup, target))
            for _artifact_id, staged, targets in pending:
                for source, target in zip(staged, targets):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.replace(target)
                    moved_targets.append(target)
            refs = []
            for artifact_id, _staged, targets in pending:
                self._validator(artifact_id)(targets)
                primary = targets[0]
                digest = artifact_file_digest(artifact_id, primary) if primary.is_file() else canonical_json_hash(
                    sorted(str(item.relative_to(primary)) for item in primary.rglob("*") if item.is_file())
                )
                metadata = {}
                if primary.is_file():
                    stat = primary.stat()
                    metadata = {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
                refs.append(ArtifactRef(artifact_id, primary, digest=digest, metadata=metadata))
            return tuple(refs)
        except Exception:
            for target in reversed(moved_targets):
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            for backup, target in reversed(moved_backups):
                if backup.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    backup.replace(target)
            raise
        finally:
            shutil.rmtree(backups, ignore_errors=True)

    def cleanup_staging(self, context: "ProcessingContext", step_id: str) -> None:
        shutil.rmtree(context.workspace.staging_dir(context.run_id, step_id), ignore_errors=True)

    def _validator(self, artifact_id: ArtifactId) -> Callable[[tuple[Path, ...]], None]:
        return self.validators.get(artifact_id.name, ExistingArtifactValidator())


@dataclass(frozen=True)
class WorkspaceEntry:
    layout: WorkspaceLayout
    manifest: Mapping[str, Any]

    @property
    def manifest_path(self) -> Path:
        return self.layout.artifact_paths(SOURCE_MANIFEST)[0]

    @property
    def document_path(self) -> Path:
        return self.layout.artifact_paths(DOCUMENT_V2)[0]


class WorkspaceCatalog:
    def __init__(self, workspace_root: Path, *, project_root: Path | None = None) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self.project_root = project_root.expanduser().resolve() if project_root else None

    def entries(self) -> tuple[WorkspaceEntry, ...]:
        if not self.workspace_root.is_dir():
            return ()
        entries = []
        for child in self.workspace_root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            layout = WorkspaceLayout(self.workspace_root, child.name)
            manifest_path = layout.artifact_paths(SOURCE_MANIFEST)[0]
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict) or manifest.get("video_id") != child.name:
                    continue
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            entries.append(WorkspaceEntry(layout, MappingProxyType(manifest)))
        return tuple(entries)

    def find_by_video_id(self, video_id: str) -> WorkspaceEntry | None:
        return next((entry for entry in self.entries() if entry.manifest.get("video_id") == video_id), None)

    def find_by_source(self, source: Path) -> WorkspaceEntry | None:
        expected = str(source.expanduser().resolve()).casefold()
        for entry in self.entries():
            try:
                actual = str(Path(str(entry.manifest["source_path"])).resolve()).casefold()
            except (KeyError, OSError, ValueError):
                continue
            if actual == expected:
                return entry
        return None

    def document_for_manifest(self, manifest_path: Path) -> Path:
        resolved = manifest_path.expanduser().resolve()
        if resolved.parent.parent != self.workspace_root:
            raise ValueError("Manifest 不属于当前 Workspace")
        layout = WorkspaceLayout(self.workspace_root, resolved.parent.name)
        if layout.artifact_paths(SOURCE_MANIFEST)[0] != resolved:
            raise ValueError("路径不是标准 Workspace Manifest")
        return layout.artifact_paths(DOCUMENT_V2)[0]

    def delete_video(self, source: Path, output_root: Path | None = None) -> bool:
        entry = self.find_by_source(source)
        if not entry:
            return False
        self._safe_remove(entry.layout.video_root, self.workspace_root)
        if output_root is not None:
            resolved_output = output_root.expanduser().resolve()
            target = resolved_output / str(entry.manifest["video_id"])
            if target.exists():
                self._safe_remove(target, resolved_output)
        return True

    def clear(self) -> int:
        self._assert_safe_root(self.workspace_root)
        if not self.workspace_root.exists():
            return 0
        if not self.workspace_root.is_dir() or self.workspace_root.is_symlink():
            raise ValueError(f"工作区路径不是安全目录：{self.workspace_root}")
        removed = 0
        for child in tuple(self.workspace_root.iterdir()):
            self._safe_remove(child, self.workspace_root)
            removed += 1
        return removed

    def acquire_lease(
        self,
        layout: WorkspaceLayout,
        run_id: str,
        *,
        stale_after_seconds: float = 6 * 60 * 60,
        pid: int | None = None,
    ) -> "WorkspaceLease":
        return WorkspaceLease.acquire(
            layout,
            run_id,
            stale_after_seconds=stale_after_seconds,
            pid=pid,
        )

    def _safe_remove(self, target: Path, root: Path) -> None:
        resolved_root = root.resolve()
        if target.is_symlink():
            target.unlink(missing_ok=True)
            return
        resolved = target.resolve()
        self._assert_safe_root(resolved_root)
        if resolved == resolved_root or resolved_root not in resolved.parents:
            raise ValueError(f"拒绝清理不安全路径：{resolved}")
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)

    def _assert_safe_root(self, root: Path) -> None:
        protected = {Path(root.anchor).resolve(), Path.home().resolve()}
        if self.project_root:
            protected.add(self.project_root)
        if root in protected:
            raise ValueError(f"拒绝清理不安全的工作区路径：{root}")


@dataclass
class WorkspaceLease:
    path: Path
    run_id: str
    pid: int
    acquired: bool = True

    @classmethod
    def acquire(
        cls,
        layout: WorkspaceLayout,
        run_id: str,
        *,
        stale_after_seconds: float,
        pid: int | None,
    ) -> "WorkspaceLease":
        lease_path = layout.state_dir / "workspace.lease.json"
        lease_path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        if lease_path.exists():
            current: Mapping[str, Any] = {}
            try:
                current = json.loads(lease_path.read_text(encoding="utf-8"))
                created = float(current["created_unix"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                created = 0.0
            if now - created <= stale_after_seconds:
                raise RuntimeError(f"Workspace 正由 run {current.get('run_id', 'unknown')} 使用")
            lease_path.unlink(missing_ok=True)
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "pid": int(os.getpid() if pid is None else pid),
            "created_unix": now,
        }
        temporary = lease_path.with_name(f".{lease_path.name}.{payload['pid']}.tmp")
        temporary.write_bytes(canonical_json_bytes(payload) + b"\n")
        try:
            with lease_path.open("xb") as stream:
                stream.write(temporary.read_bytes())
        except FileExistsError as exc:
            raise RuntimeError("Workspace lease 获取竞争失败") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return cls(lease_path, run_id, payload["pid"])

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("run_id") == self.run_id and int(payload.get("pid", -1)) == self.pid:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self.acquired = False

    def __enter__(self) -> "WorkspaceLease":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


class LegacyArtifactAdapter:
    def __init__(self, catalog: WorkspaceCatalog) -> None:
        self.catalog = catalog

    def discover(self, source: Path) -> Mapping[ArtifactId, ArtifactRef]:
        entry = self.catalog.find_by_source(source)
        if not entry:
            return MappingProxyType({})
        refs: dict[ArtifactId, ArtifactRef] = {}
        manifest_path = entry.manifest_path
        refs[SOURCE_MANIFEST] = ArtifactRef(
            SOURCE_MANIFEST, manifest_path, artifact_file_digest(SOURCE_MANIFEST, manifest_path), {"legacy_adopted": True},
        )
        candidates = (
            (AUDIO_FLAC, entry.layout.video_root / "audio" / "audio.flac"),
            (TRANSCRIPT_NORMALIZED, entry.layout.video_root / "transcript" / "transcript.json"),
            (TRANSCRIPT_SRT, entry.layout.video_root / "transcript" / "transcript.srt"),
            (FRAMES_SELECTED, entry.layout.video_root / "images" / "keyframes.json"),
            (KNOWLEDGE_PLAN, entry.layout.video_root / "knowledge" / "lesson-plan.json"),
            (VISUAL_EVIDENCE, entry.layout.video_root / "knowledge" / "visual-evidence.json"),
            (FRAMES_SEMANTICS, entry.layout.video_root / "knowledge" / "frame-semantics.json"),
            (KNOWLEDGE_COURSE_IR, entry.layout.video_root / "knowledge" / "course-ir.json"),
            (KNOWLEDGE_UNITS, entry.layout.video_root / "knowledge" / "knowledge-units.json"),
            (KNOWLEDGE_SELFCHECK, entry.layout.video_root / "knowledge" / "selfcheck.json"),
            (DOCUMENT_V2, entry.layout.video_root / "knowledge" / "document.json"),
        )
        for artifact_id, path in candidates:
            if not path.is_file() or path.stat().st_size == 0:
                continue
            try:
                if path.suffix.lower() == ".json":
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if artifact_id == FRAMES_SELECTED and not all(
                        Path(str(row.get("path", ""))).is_file()
                        for row in value.get("frames", [])
                    ):
                        continue
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            refs[artifact_id] = ArtifactRef(
                artifact_id, path, file_digest(path), {"legacy_adopted": True},
            )
        transcript = refs.get(TRANSCRIPT_NORMALIZED)
        if transcript:
            value = json.loads(transcript.path.read_text(encoding="utf-8"))
            if value.get("raw_text") or any(segment.get("raw_text") for segment in value.get("segments", [])):
                refs[TRANSCRIPT_RAW] = ArtifactRef(
                    TRANSCRIPT_RAW,
                    transcript.path,
                    transcript.digest,
                    {"legacy_adopted": True, "reconstructable": True},
                )
        return MappingProxyType(refs)

    def read_document_v2(self, source: Path) -> Mapping[str, Any] | None:
        entry = self.catalog.find_by_source(source)
        if not entry or not entry.document_path.is_file():
            return None
        try:
            document = json.loads(entry.document_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if int(document.get("schema_version", 1) or 1) == 2:
            return MappingProxyType(document)
        from ..knowledge.adapter import v1_to_v2
        return MappingProxyType(v1_to_v2(document))

    def read_transcript_raw(self, source: Path) -> Mapping[str, Any] | None:
        refs = self.discover(source)
        transcript = refs.get(TRANSCRIPT_NORMALIZED)
        if not transcript:
            return None
        try:
            normalized = json.loads(transcript.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        segments = []
        reconstructable = False
        for row in normalized.get("segments", []):
            raw_text = row.get("raw_text")
            if raw_text is not None:
                reconstructable = True
            segments.append({
                "segment_id": row.get("segment_id"),
                "start_seconds": row.get("start_seconds"),
                "end_seconds": row.get("end_seconds"),
                "text": raw_text if raw_text is not None else row.get("text", ""),
            })
        if not reconstructable and normalized.get("raw_text") is None:
            return None
        return MappingProxyType({
            "schema_version": 1,
            "engine": normalized.get("engine"),
            "raw_text": normalized.get("raw_text", ""),
            "segments": segments,
        })
