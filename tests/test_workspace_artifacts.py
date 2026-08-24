from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from zhiying.execution.artifacts import (
    ArtifactId,
    ArtifactRef,
    DOCUMENT_V2,
    FRAMES_SELECTED,
    LegacyArtifactAdapter,
    FileArtifactStore,
    SOURCE_MANIFEST,
    STANDARD_ARTIFACTS,
    WorkspaceCatalog,
    WorkspaceLayout,
    canonical_json_hash,
)
from zhiying.execution.context import (
    ProcessingContext,
    ProcessingOptions,
    RunPolicy,
    RuntimeServices,
    VideoSource,
)
from zhiying.execution.contracts import StepOutcome, StepSpec, StepStatus


class WorkspaceArtifactContractTests(unittest.TestCase):
    def test_layout_is_the_single_source_of_workspace_paths(self) -> None:
        with TemporaryDirectory() as directory:
            layout = WorkspaceLayout(Path(directory) / "workspace", "video-id")
            artifact = ArtifactId(
                "frames.candidates",
                ("images/candidates.json", "images/candidates"),
            )
            self.assertEqual(
                layout.artifact_paths(artifact),
                (
                    layout.video_root / "images" / "candidates.json",
                    layout.video_root / "images" / "candidates",
                ),
            )
            self.assertEqual(
                layout.staging_dir("run-id", "frames.candidates"),
                layout.video_root / "state" / "staging" / "run-id" / "frames.candidates",
            )

    def test_output_artifact_is_resolved_by_layout(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = WorkspaceLayout(root / "workspace", "video-id", root / "output")
            artifact = ArtifactId("render.bundle", ("lesson.md", "lesson.pdf"), "output")
            self.assertEqual(
                layout.artifact_paths(artifact),
                (root / "output" / "video-id" / "lesson.md", root / "output" / "video-id" / "lesson.pdf"),
            )

    def test_artifact_paths_cannot_escape_workspace(self) -> None:
        for invalid in ("../secret", "/absolute/path", "C:/absolute/path", "state/../../secret"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ArtifactId("invalid", (invalid,))

    def test_artifact_ref_metadata_is_immutable(self) -> None:
        artifact = ArtifactId("document.v2", ("knowledge/document.json",))
        metadata = {"schema_version": 2}
        ref = ArtifactRef(artifact, Path("document.json"), metadata=metadata)
        metadata["schema_version"] = 1
        self.assertEqual(ref.metadata["schema_version"], 2)
        with self.assertRaises(TypeError):
            ref.metadata["schema_version"] = 1

    def test_standard_artifact_registry_and_json_hash_are_stable(self) -> None:
        self.assertEqual(
            set(STANDARD_ARTIFACTS),
            {
                "source.manifest", "audio.flac", "transcript.raw",
                "transcript.normalized", "transcript.srt", "frames.candidates",
                "frames.selected", "knowledge.plan", "visual.jobs", "visual.evidence",
                "frames.semantics", "knowledge.course_ir", "knowledge.units",
                "knowledge.selfcheck", "editorial.policy", "evidence.corrections",
                "document.blueprint", "editorial.session", "document.chapter_v31", "document.v3",
                "document.validation",
            },
        )
        self.assertEqual(canonical_json_hash({"b": 2, "a": 1}), canonical_json_hash({"a": 1, "b": 2}))


class FileArtifactStoreTests(unittest.TestCase):
    def _context(self, root: Path) -> ProcessingContext:
        return ProcessingContext(
            "run-id",
            VideoSource(root / "lesson.mp4", "video-id", "sha256:source", 1.0, 1),
            WorkspaceLayout(root / "workspace", "video-id"),
            ProcessingOptions(), RunPolicy(), RuntimeServices(),
        )

    def test_atomic_commit_replaces_valid_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            context = self._context(Path(directory))
            artifact = ArtifactId("fixture.json", ("knowledge/fixture.json",))
            store = FileArtifactStore()
            target = context.workspace.artifact_paths(artifact)[0]
            target.parent.mkdir(parents=True)
            target.write_text('{"version":"old"}', encoding="utf-8")
            staging = store.staging_dir(context, "fixture.step")
            staged = staging / "knowledge" / "fixture.json"
            staged.parent.mkdir(parents=True)
            staged.write_text('{"version":"new"}', encoding="utf-8")
            spec = StepSpec("fixture.step", 1, outputs=(artifact,), error_code_prefix="FIXTURE")
            outcome = StepOutcome(
                "fixture.step", "run-id", StepStatus.SUCCEEDED,
                artifacts=(ArtifactRef(artifact, staged),),
            )
            refs = store.commit(context, spec, outcome, staging)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": "new"})
            self.assertTrue(refs[0].digest.startswith("sha256:"))
            self.assertEqual(refs[0].metadata["size_bytes"], target.stat().st_size)

    def test_failed_commit_restores_all_previous_artifacts(self) -> None:
        class FailOnCommittedValidation:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self, paths) -> None:
                self.calls += 1
                if self.calls == 2:
                    raise ValueError("simulated post-move validation failure")

        with TemporaryDirectory() as directory:
            context = self._context(Path(directory))
            first = ArtifactId("first", ("knowledge/first.json",))
            second = ArtifactId("second", ("knowledge/second.json",))
            validator = FailOnCommittedValidation()
            store = FileArtifactStore({"second": validator})
            targets = [context.workspace.artifact_paths(item)[0] for item in (first, second)]
            for index, target in enumerate(targets, start=1):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f'{{"old":{index}}}', encoding="utf-8")
            staging = store.staging_dir(context, "fixture.step")
            refs = []
            for index, artifact in enumerate((first, second), start=1):
                staged = staging / artifact.relative_paths[0]
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_text(f'{{"new":{index}}}', encoding="utf-8")
                refs.append(ArtifactRef(artifact, staged))
            spec = StepSpec("fixture.step", 1, outputs=(first, second), error_code_prefix="FIXTURE")
            outcome = StepOutcome("fixture.step", "run-id", StepStatus.SUCCEEDED, artifacts=tuple(refs))
            with self.assertRaisesRegex(ValueError, "simulated"):
                store.commit(context, spec, outcome, staging)
            self.assertEqual(json.loads(targets[0].read_text(encoding="utf-8")), {"old": 1})
            self.assertEqual(json.loads(targets[1].read_text(encoding="utf-8")), {"old": 2})

    def test_document_v2_and_frame_references_are_validated(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = self._context(root)
            store = FileArtifactStore()
            document_path = context.workspace.artifact_paths(DOCUMENT_V2)[0]
            document_path.parent.mkdir(parents=True)
            document_path.write_text('{"schema_version":1,"sections":[]}', encoding="utf-8")
            self.assertFalse(store.validate(context, ArtifactRef(DOCUMENT_V2, document_path)))
            document_path.write_text('{"schema_version":2,"sections":[]}', encoding="utf-8")
            self.assertTrue(store.validate(context, ArtifactRef(DOCUMENT_V2, document_path)))
            index, selected = context.workspace.artifact_paths(FRAMES_SELECTED)
            selected.mkdir(parents=True)
            index.write_text(json.dumps({"frames": [{"path": str(selected / "missing.jpg")}]}), encoding="utf-8")
            self.assertFalse(store.validate(context, ArtifactRef(FRAMES_SELECTED, index)))


class WorkspaceCatalogTests(unittest.TestCase):
    def _legacy_workspace(self, root: Path, *, document_version: int = 1) -> tuple[Path, Path, WorkspaceCatalog]:
        video = root / "lesson.mp4"
        video.write_bytes(b"video")
        work = root / "workspace" / "video-id"
        (work / "transcript").mkdir(parents=True)
        (work / "knowledge").mkdir()
        (work / "images" / "selected").mkdir(parents=True)
        (work / "manifest.json").write_text(json.dumps({
            "video_id": "video-id", "source_path": str(video), "fingerprint": "fixture",
        }), encoding="utf-8")
        (work / "transcript" / "transcript.json").write_text(json.dumps({
            "engine": "fake", "segments": [{
                "segment_id": "s1", "start_seconds": 0, "end_seconds": 1,
                "text": "corrected", "raw_text": "raw",
            }],
        }), encoding="utf-8")
        if document_version == 1:
            document = {"schema_version": 1, "sections": [{"knowledge_points": [{
                "statement": "知识点", "explanation": "正文", "source_segment_ids": ["s1"],
                "start_seconds": 0, "end_seconds": 1, "source_url": "video-study://play/video-id?t=0",
            }]}]}
        else:
            document = {"schema_version": 2, "sections": []}
        (work / "knowledge" / "document.json").write_text(json.dumps(document), encoding="utf-8")
        (work / "images" / "keyframes.json").write_text(json.dumps({
            "frames": [{"path": str(work / "images" / "selected" / "missing.jpg")}],
        }), encoding="utf-8")
        return video, work, WorkspaceCatalog(root / "workspace", project_root=root)

    def test_catalog_ignores_corrupt_manifest_and_finds_valid_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video, work, catalog = self._legacy_workspace(root)
            corrupt = root / "workspace" / "broken"
            corrupt.mkdir()
            (corrupt / "manifest.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(catalog.find_by_source(video).layout.video_root, work)
            self.assertIsNone(catalog.find_by_video_id("broken"))

    def test_legacy_adapter_migrates_document_in_memory_and_reconstructs_raw(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video, work, catalog = self._legacy_workspace(root)
            adapter = LegacyArtifactAdapter(catalog)
            original = (work / "knowledge" / "document.json").read_text(encoding="utf-8")
            migrated = adapter.read_document_v2(video)
            raw = adapter.read_transcript_raw(video)
            refs = adapter.discover(video)
            self.assertEqual(migrated["schema_version"], 2)
            self.assertIn("content_blocks", migrated["sections"][0]["knowledge_points"][0])
            self.assertEqual(raw["segments"][0]["text"], "raw")
            self.assertNotIn(FRAMES_SELECTED, refs)
            self.assertEqual((work / "knowledge" / "document.json").read_text(encoding="utf-8"), original)

    def test_active_lease_is_rejected_and_stale_lease_is_reclaimed(self) -> None:
        import os as _os
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = WorkspaceLayout(root / "workspace", "video-id")
            catalog = WorkspaceCatalog(root / "workspace", project_root=root)
            lease = catalog.acquire_lease(layout, "run-1", pid=_os.getpid())
            with self.assertRaisesRegex(RuntimeError, "正由 run run-1"):
                catalog.acquire_lease(layout, "run-2", pid=202)
            lease.release()
            lease_path = layout.state_dir / "workspace.lease.json"
            lease_path.write_text(json.dumps({"run_id": "old", "pid": 1, "created_unix": 0}), encoding="utf-8")
            reclaimed = catalog.acquire_lease(layout, "run-2", stale_after_seconds=1, pid=202)
            self.assertTrue(reclaimed.acquired)
            reclaimed.release()

    def test_safe_clear_rejects_protected_roots_and_does_not_follow_symlink(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                WorkspaceCatalog(root, project_root=root).clear()
            outside = root / "outside"
            outside.mkdir()
            marker = outside / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            workspace = root / "project" / "workspace"
            workspace.mkdir(parents=True)
            link = workspace / "outside-link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("当前 Windows 环境不允许创建测试符号链接")
            catalog = WorkspaceCatalog(workspace, project_root=root / "project")
            self.assertEqual(catalog.clear(), 1)
            self.assertTrue(marker.is_file())

    def test_delete_video_removes_only_workspace_and_output_not_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video, work, catalog = self._legacy_workspace(root)
            output = root / "output" / "video-id"
            output.mkdir(parents=True)
            (output / "lesson.pdf").write_bytes(b"pdf")
            self.assertTrue(catalog.delete_video(video, root / "output"))
            self.assertTrue(video.is_file())
            self.assertFalse(work.exists())
            self.assertFalse(output.exists())

    def test_delete_url_source_keeps_downloaded_file_but_removes_derivatives(self) -> None:
        """TC-014（D4）：链接源清除所选缓存保留下载文件（source/），只清派生产物。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            video_id = "BV1cmTu6mEL3"
            layout = WorkspaceLayout(workspace, video_id)
            source_dir = layout.video_root / "source"
            source_dir.mkdir(parents=True)
            downloaded = source_dir / "测试视频.mp4"
            downloaded.write_bytes(b"video-bytes")
            (layout.video_root / "transcript").mkdir()
            (layout.video_root / "transcript" / "transcript.json").write_text("{}", encoding="utf-8")
            (layout.video_root / "knowledge").mkdir()
            (layout.video_root / "knowledge" / "document.json").write_text("{}", encoding="utf-8")
            manifest = layout.artifact_paths(SOURCE_MANIFEST)[0]
            manifest.write_text(json.dumps({
                "schema_version": 1, "video_id": video_id, "title": "测试视频",
                "source_path": str(downloaded), "fingerprint": "abc",
                "duration_seconds": 616.0, "size_bytes": 12,
                "source_url": "https://www.bilibili.com/video/BV1cmTu6mEL3",
                "stages": {},
            }, ensure_ascii=False), encoding="utf-8")
            output = root / "output" / video_id
            output.mkdir(parents=True)
            (output / "lesson.pdf").write_bytes(b"pdf")
            catalog = WorkspaceCatalog(workspace, project_root=root)

            self.assertTrue(catalog.delete_video(downloaded, root / "output"))

            self.assertTrue(downloaded.is_file())                      # D4：下载文件保留
            self.assertFalse((layout.video_root / "transcript").exists())
            self.assertFalse((layout.video_root / "knowledge").exists())
            self.assertFalse((layout.video_root / "manifest.json").exists())
            self.assertFalse(output.exists())
            self.assertTrue(source_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
