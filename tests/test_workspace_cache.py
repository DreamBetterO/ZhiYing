from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from zhiying.execution.artifacts import ArtifactId, ArtifactRef
from zhiying.execution.artifacts import WorkspaceLayout, file_digest
from zhiying.execution.cache import (
    CacheDecision, CacheReason, CacheRecord, FileWorkspaceCache, LegacyAdoptingWorkspaceCache,
)
from zhiying.execution.context import (
    ProcessingContext, ProcessingOptions, RunPolicy, RuntimeServices, VideoSource,
)
from zhiying.execution.contracts import FingerprintMaterial, StepOutcome, StepSpec, StepStatus


class WorkspaceCacheContractTests(unittest.TestCase):
    def test_cache_decision_uses_stable_reason_codes(self) -> None:
        self.assertEqual(
            {reason.value for reason in CacheReason},
            {
                "CACHE_HIT", "NO_RECORD", "FORCED", "STEP_VERSION_CHANGED",
                "CONTRACT_CHANGED", "UPSTREAM_CHANGED", "CONFIG_CHANGED",
                "CAPABILITY_INSUFFICIENT", "OUTPUT_MISSING", "OUTPUT_CORRUPT",
                "PREVIOUS_RUN_INCOMPLETE", "LEGACY_ADOPTED",
            },
        )
        with self.assertRaises(ValueError):
            CacheDecision(True, CacheReason.NO_RECORD)

    def test_cache_record_is_immutable_and_contains_no_runtime_service_field(self) -> None:
        artifact = ArtifactId("document.v2", ("knowledge/document.json",))
        ref = ArtifactRef(artifact, Path("document.json"), digest="sha256:fixture")
        record = CacheRecord(
            step_id="document.assemble",
            implementation_version=1,
            contract_version="document-v2",
            cache_key="sha256:key",
            input_components={"document.schema_version": 2},
            outputs=(ref,),
            requested_capability="offline",
            produced_capability="offline",
            status="succeeded",
            run_id="run-id",
            created_at="2026-08-12T00:00:00+08:00",
        )
        self.assertEqual(record.schema_version, 1)
        self.assertFalse(hasattr(record, "api_key"))
        self.assertFalse(hasattr(record, "runtime_services"))
        with self.assertRaises(TypeError):
            record.input_components["document.schema_version"] = 1


class FileWorkspaceCacheTests(unittest.TestCase):
    def test_legacy_render_is_not_adopted_for_new_document_input(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = ProcessingContext(
                "run-id",
                VideoSource(root / "lesson.mp4", "video-id", "sha256:source", 1.0, 1),
                WorkspaceLayout(root / "workspace", "video-id", root / "output"),
                ProcessingOptions(), RunPolicy(), RuntimeServices(),
            )
            document_id = ArtifactId("document.v2", ("knowledge/document.json",))
            document_path = context.workspace.artifact_paths(document_id)[0]
            document_path.parent.mkdir(parents=True)
            document_path.write_text('{"schema_version":2}', encoding="utf-8")
            document_ref = ArtifactRef(document_id, document_path, file_digest(document_path))
            render_id = ArtifactId(
                "render.bundle", ("lesson.md", "lesson.docx", "lesson.pdf"), "output",
            )
            for path in context.workspace.artifact_paths(render_id):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("old", encoding="utf-8")
            spec = StepSpec(
                "render.bundle", 1, dependencies=("document.assemble",),
                inputs=(document_id,), outputs=(render_id,), error_code_prefix="RENDER",
            )
            adapter = MagicMock()
            store = MagicMock()
            store.validate.return_value = True
            cache = LegacyAdoptingWorkspaceCache(FileWorkspaceCache(), adapter, store)
            decision = cache.decide(
                context, spec, FingerprintMaterial({"upstream.document.v2": document_ref.digest}),
                {document_id: document_ref},
            )
            self.assertFalse(decision.hit)
            self.assertEqual(decision.reason, CacheReason.NO_RECORD)
            adapter.discover.assert_not_called()

    def _context(self, root: Path, *, cloud: bool = False, force: bool = False) -> ProcessingContext:
        return ProcessingContext(
            "run-id",
            VideoSource(root / "lesson.mp4", "video-id", "sha256:source", 1.0, 1),
            WorkspaceLayout(root / "workspace", "video-id"),
            ProcessingOptions(),
            RunPolicy(cloud_authorized=cloud, force_steps=frozenset({"fixture.step"}) if force else frozenset()),
            RuntimeServices(),
        )

    def _record(self, root: Path):
        context = self._context(root)
        artifact = ArtifactId("fixture.output", ("knowledge/output.json",))
        output = context.workspace.artifact_paths(artifact)[0]
        output.parent.mkdir(parents=True)
        output.write_text('{"ok":true}', encoding="utf-8")
        stat = output.stat()
        ref = ArtifactRef(artifact, output, file_digest(output), {
            "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        })
        spec = StepSpec(
            "fixture.step", 1, outputs=(artifact,), contract_version="fixture-v1",
            error_code_prefix="FIXTURE", capabilities=("offline", "cloud"),
        )
        fingerprint = FingerprintMaterial({"config.value": 1, "upstream.source": "sha256:source"})
        outcome = StepOutcome(
            "fixture.step", "run-id", StepStatus.SUCCEEDED,
            capability="offline", artifacts=(ref,),
        )
        cache = FileWorkspaceCache()
        cache.record(context, spec, fingerprint, outcome)
        return context, artifact, output, spec, fingerprint, cache

    def test_record_round_trip_and_unchanged_metadata_avoids_rehash(self) -> None:
        with TemporaryDirectory() as directory:
            context, _artifact, _output, spec, fingerprint, cache = self._record(Path(directory))
            with patch("zhiying.execution.cache.file_digest", side_effect=AssertionError("must not hash")):
                decision = cache.decide(context, spec, fingerprint, {})
            self.assertTrue(decision.hit)
            self.assertEqual(decision.reason, CacheReason.CACHE_HIT)

    def test_changed_metadata_rehashes_and_detects_corruption(self) -> None:
        with TemporaryDirectory() as directory:
            context, artifact, output, spec, fingerprint, cache = self._record(Path(directory))
            output.write_text('{"ok":false}', encoding="utf-8")
            decision = cache.decide(context, spec, fingerprint, {})
            self.assertFalse(decision.hit)
            self.assertEqual(decision.reason, CacheReason.OUTPUT_CORRUPT)
            self.assertEqual(decision.changed_components, (artifact.name,))

    def test_stable_miss_reasons_cover_force_config_upstream_capability_and_missing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context, artifact, output, spec, fingerprint, cache = self._record(root)
            self.assertEqual(
                cache.decide(self._context(root, force=True), spec, fingerprint, {}).reason,
                CacheReason.FORCED,
            )
            changed_config = FingerprintMaterial({"config.value": 2, "upstream.source": "sha256:source"})
            self.assertEqual(cache.decide(context, spec, changed_config, {}).reason, CacheReason.CONFIG_CHANGED)
            changed_upstream = FingerprintMaterial({"config.value": 1, "upstream.source": "sha256:changed"})
            self.assertEqual(cache.decide(context, spec, changed_upstream, {}).reason, CacheReason.UPSTREAM_CHANGED)
            self.assertEqual(
                cache.decide(self._context(root, cloud=True), spec, fingerprint, {}).reason,
                CacheReason.CAPABILITY_INSUFFICIENT,
            )
            output.unlink()
            self.assertEqual(cache.decide(context, spec, fingerprint, {}).reason, CacheReason.OUTPUT_MISSING)

    def test_structured_cloud_result_satisfies_cloud_cache_request(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _context, _artifact, _output, spec, fingerprint, cache = self._record(root)
            record_path = cache.record_path(self._context(root), spec.step_id)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["produced_capability"] = "structured_only"
            record_path.write_text(json.dumps(record), encoding="utf-8")

            decision = cache.decide(self._context(root, cloud=True), spec, fingerprint, {})

            self.assertTrue(decision.hit)
            self.assertEqual(decision.produced_capability, "structured_only")

    def test_degraded_structured_result_does_not_satisfy_cloud_cache_request(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _context, _artifact, _output, spec, fingerprint, cache = self._record(root)
            record_path = cache.record_path(self._context(root), spec.step_id)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["produced_capability"] = "structured_only"
            record["status"] = "degraded"
            record_path.write_text(json.dumps(record), encoding="utf-8")

            decision = cache.decide(self._context(root, cloud=True), spec, fingerprint, {})

            self.assertFalse(decision.hit)
            self.assertEqual(decision.reason, CacheReason.CAPABILITY_INSUFFICIENT)

    def test_corrupt_record_is_previous_run_incomplete(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = self._context(root)
            spec = StepSpec("fixture.step", 1, error_code_prefix="FIXTURE")
            cache = FileWorkspaceCache()
            path = cache.record_path(context, spec.step_id)
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            decision = cache.decide(context, spec, FingerprintMaterial({}), {})
            self.assertEqual(decision.reason, CacheReason.PREVIOUS_RUN_INCOMPLETE)

    def test_legacy_adoption_writes_success_record_and_returns_adopted_hit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context, _artifact, _output, spec, fingerprint, cache = self._record(root)
            existing = cache.decide(context, spec, fingerprint, {}).artifacts
            cache.record_path(context, spec.step_id).unlink()
            cache.adopt(context, spec, fingerprint, existing)
            decision = cache.decide(context, spec, fingerprint, {})
            self.assertTrue(decision.hit)
            self.assertEqual(decision.reason, CacheReason.LEGACY_ADOPTED)


if __name__ == "__main__":
    unittest.main()
