"""Unit tests for amil_utils.manifest module.

Tests cover: Pydantic models (StageResult, ArtifactEntry, GenerationManifest),
SHA256 helpers, manifest persistence (save/load), and GenerationSession.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# TestStageResult
# ---------------------------------------------------------------------------


class TestStageResult:
    """StageResult Pydantic model tests."""

    def test_round_trip_complete(self):
        """StageResult with status='complete' round-trips through dump/validate."""
        from amil_utils.manifest import StageResult

        original = StageResult(status="complete", duration_ms=42)
        data = original.model_dump()
        restored = StageResult.model_validate(data)
        assert restored.status == "complete"
        assert restored.duration_ms == 42

    def test_default_status_pending(self):
        """Default status is 'pending'."""
        from amil_utils.manifest import StageResult

        result = StageResult()
        assert result.status == "pending"
        assert result.duration_ms == 0

    def test_invalid_status_rejected(self):
        """Literal validation rejects invalid status like 'bogus'."""
        from amil_utils.manifest import StageResult

        with pytest.raises(ValidationError):
            StageResult(status="bogus")

    def test_optional_fields(self):
        """Reason and error are optional (None by default)."""
        from amil_utils.manifest import StageResult

        result = StageResult(status="failed", error="Something broke")
        assert result.error == "Something broke"
        assert result.reason is None

    def test_artifacts_default_empty(self):
        """Artifacts list defaults to empty."""
        from amil_utils.manifest import StageResult

        result = StageResult()
        assert result.artifacts == []

    def test_artifacts_stores_paths(self):
        """Artifacts list stores relative paths."""
        from amil_utils.manifest import StageResult

        result = StageResult(status="complete", artifacts=["models/foo.py", "views/bar.xml"])
        assert len(result.artifacts) == 2
        assert "models/foo.py" in result.artifacts


# ---------------------------------------------------------------------------
# TestArtifactEntry
# ---------------------------------------------------------------------------


class TestArtifactEntry:
    """ArtifactEntry Pydantic model tests."""

    def test_round_trip(self):
        """ArtifactEntry round-trips through dump/validate."""
        from amil_utils.manifest import ArtifactEntry

        entry = ArtifactEntry(path="models/foo.py", sha256="abc123")
        data = entry.model_dump()
        restored = ArtifactEntry.model_validate(data)
        assert restored.path == "models/foo.py"
        assert restored.sha256 == "abc123"

    def test_both_fields_required(self):
        """Both path and sha256 are required -- ValidationError on missing."""
        from amil_utils.manifest import ArtifactEntry

        with pytest.raises(ValidationError):
            ArtifactEntry(path="models/foo.py")

        with pytest.raises(ValidationError):
            ArtifactEntry(sha256="abc123")

        with pytest.raises(ValidationError):
            ArtifactEntry()


# ---------------------------------------------------------------------------
# TestGenerationManifest
# ---------------------------------------------------------------------------


class TestGenerationManifest:
    """GenerationManifest Pydantic model tests."""

    def test_full_manifest_round_trip(self):
        """Full manifest with nested models round-trips through dump/validate."""
        from amil_utils.manifest import (
            ArtifactEntry,
            ArtifactInfo,
            GenerationManifest,
            PreprocessingInfo,
            StageResult,
            ValidationInfo,
        )

        manifest = GenerationManifest(
            module="my_module",
            spec_sha256="deadbeef",
            generated_at="2026-03-08T12:00:00Z",
            generator_version="0.1.0",
            preprocessing=PreprocessingInfo(
                preprocessors_run=["normalize:10", "validate:20"],
                duration_ms=50,
            ),
            stages={
                "models": StageResult(status="complete", duration_ms=100),
                "views": StageResult(status="skipped", reason="no views defined"),
            },
            artifacts=ArtifactInfo(
                files=[ArtifactEntry(path="models/foo.py", sha256="abc123")],
                total_files=1,
                total_lines=42,
            ),
            validation=ValidationInfo(semantic_errors=0, semantic_warnings=1, duration_ms=10),
            models_registered=["my.model"],
        )

        data = manifest.model_dump()
        restored = GenerationManifest.model_validate(data)
        assert restored.module == "my_module"
        assert restored.stages["models"].status == "complete"
        assert restored.artifacts.files[0].path == "models/foo.py"
        assert restored.validation.semantic_warnings == 1
        assert restored.models_registered == ["my.model"]

    def test_exclude_none_omits_none_fields(self):
        """exclude_none=True omits None fields from dump."""
        from amil_utils.manifest import GenerationManifest

        manifest = GenerationManifest(
            module="test",
            spec_sha256="abc",
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
        )
        data = manifest.model_dump(exclude_none=True)
        assert "validation" not in data

    def test_protected_namespaces_set(self):
        """All models use ConfigDict(protected_namespaces=())."""
        from amil_utils.manifest import (
            ArtifactEntry,
            ArtifactInfo,
            GenerationManifest,
            PreprocessingInfo,
            StageResult,
            ValidationInfo,
        )

        for model_cls in [
            StageResult,
            ArtifactEntry,
            PreprocessingInfo,
            ArtifactInfo,
            ValidationInfo,
            GenerationManifest,
        ]:
            config = model_cls.model_config
            assert config.get("protected_namespaces") == (), (
                f"{model_cls.__name__} missing protected_namespaces=()"
            )


# ---------------------------------------------------------------------------
# TestSHA256
# ---------------------------------------------------------------------------


class TestSHA256:
    """SHA256 helper tests."""

    def test_compute_file_sha256(self, tmp_path: Path):
        """compute_file_sha256 returns expected hex digest for known content."""
        from amil_utils.manifest import compute_file_sha256

        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello world")
        # Known SHA256 of b"hello world"
        expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert compute_file_sha256(test_file) == expected

    def test_compute_spec_sha256_canonical(self):
        """compute_spec_sha256 returns same hash regardless of key order or whitespace."""
        from amil_utils.manifest import compute_spec_sha256

        spec_a = {"module": "test", "version": "1.0", "models": []}
        spec_b = {"version": "1.0", "models": [], "module": "test"}

        hash_a = compute_spec_sha256(spec_a)
        hash_b = compute_spec_sha256(spec_b)
        assert hash_a == hash_b
        assert len(hash_a) == 64  # SHA256 hex digest length

    def test_compute_spec_sha256_deterministic(self):
        """Same spec dict produces same hash on repeated calls."""
        from amil_utils.manifest import compute_spec_sha256

        spec = {"module": "my_module", "models": [{"name": "res.partner"}]}
        assert compute_spec_sha256(spec) == compute_spec_sha256(spec)


# ---------------------------------------------------------------------------
# TestManifestPersistence
# ---------------------------------------------------------------------------


class TestManifestPersistence:
    """save_manifest / load_manifest tests."""

    def test_save_and_load_round_trip(self, tmp_path: Path):
        """save_manifest writes file; load_manifest reads back identical manifest."""
        from amil_utils.manifest import (
            GenerationManifest,
            MANIFEST_FILENAME,
            load_manifest,
            save_manifest,
        )

        original = GenerationManifest(
            module="test_module",
            spec_sha256="abcdef",
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
            models_registered=["test.model"],
        )

        written_path = save_manifest(original, tmp_path)
        assert written_path == tmp_path / MANIFEST_FILENAME
        assert written_path.exists()

        loaded = load_manifest(tmp_path)
        assert loaded is not None
        assert loaded.module == original.module
        assert loaded.spec_sha256 == original.spec_sha256
        assert loaded.models_registered == ["test.model"]

    def test_load_manifest_missing_file(self, tmp_path: Path):
        """load_manifest returns None for missing file."""
        from amil_utils.manifest import load_manifest

        assert load_manifest(tmp_path) is None

    def test_load_manifest_corrupt_json(self, tmp_path: Path, caplog):
        """load_manifest returns None for corrupt JSON (logs warning)."""
        from amil_utils.manifest import MANIFEST_FILENAME, load_manifest

        corrupt_file = tmp_path / MANIFEST_FILENAME
        corrupt_file.write_text("{not valid json", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="amil.manifest"):
            result = load_manifest(tmp_path)

        assert result is None
        assert any("Failed to parse" in r.message or "corrupt" in r.message.lower() or "Invalid" in r.message for r in caplog.records)

    def test_save_manifest_excludes_none(self, tmp_path: Path):
        """save_manifest uses exclude_none=True so None fields are omitted."""
        from amil_utils.manifest import (
            GenerationManifest,
            MANIFEST_FILENAME,
            save_manifest,
        )

        manifest = GenerationManifest(
            module="test",
            spec_sha256="abc",
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
        )
        save_manifest(manifest, tmp_path)
        data = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert "validation" not in data


# ---------------------------------------------------------------------------
# TestGenerationSession
# ---------------------------------------------------------------------------


class TestGenerationSession:
    """GenerationSession dataclass tests."""

    def test_record_stage_stores_result(self):
        """record_stage stores StageResult accessible via to_manifest."""
        from amil_utils.manifest import GenerationSession, StageResult

        session = GenerationSession(module_name="test", spec_sha256="abc")
        session.record_stage("models", StageResult(status="complete", duration_ms=100))

        manifest = session.to_manifest(generated_at="2026-01-01T00:00:00Z")
        assert "models" in manifest.stages
        assert manifest.stages["models"].status == "complete"

    def test_is_stage_complete_true_only_for_complete(self):
        """is_stage_complete returns True only for status='complete'."""
        from amil_utils.manifest import GenerationSession, StageResult

        session = GenerationSession(module_name="test", spec_sha256="abc")
        session.record_stage("models", StageResult(status="complete"))
        session.record_stage("views", StageResult(status="skipped"))
        session.record_stage("security", StageResult(status="failed"))

        assert session.is_stage_complete("models") is True
        assert session.is_stage_complete("views") is False
        assert session.is_stage_complete("security") is False
        assert session.is_stage_complete("nonexistent") is False

    def test_to_manifest_produces_valid_manifest(self):
        """to_manifest() produces valid GenerationManifest with all recorded stages."""
        from amil_utils.manifest import (
            ArtifactInfo,
            GenerationManifest,
            GenerationSession,
            PreprocessingInfo,
            StageResult,
        )

        session = GenerationSession(
            module_name="my_module",
            spec_sha256="deadbeef",
            generator_version="0.1.0",
        )
        session.record_stage("models", StageResult(status="complete", duration_ms=100))
        session.record_stage("views", StageResult(status="complete", duration_ms=50))

        manifest = session.to_manifest(
            generated_at="2026-03-08T12:00:00Z",
            preprocessing=PreprocessingInfo(preprocessors_run=["normalize:10"]),
            artifacts=ArtifactInfo(total_files=5, total_lines=100),
            models_registered=["my.model"],
        )

        assert isinstance(manifest, GenerationManifest)
        assert manifest.module == "my_module"
        assert manifest.spec_sha256 == "deadbeef"
        assert len(manifest.stages) == 2
        assert manifest.preprocessing.preprocessors_run == ["normalize:10"]
        assert manifest.artifacts.total_files == 5
        assert manifest.models_registered == ["my.model"]

    def test_duplicate_record_stage_overwrites(self):
        """Duplicate record_stage for same stage overwrites previous."""
        from amil_utils.manifest import GenerationSession, StageResult

        session = GenerationSession(module_name="test", spec_sha256="abc")
        session.record_stage("models", StageResult(status="pending"))
        session.record_stage("models", StageResult(status="complete", duration_ms=200))

        manifest = session.to_manifest(generated_at="2026-01-01T00:00:00Z")
        assert manifest.stages["models"].status == "complete"
        assert manifest.stages["models"].duration_ms == 200

    def test_to_manifest_default_generated_at(self):
        """to_manifest() generates ISO 8601 UTC timestamp if not provided."""
        from amil_utils.manifest import GenerationSession

        session = GenerationSession(module_name="test", spec_sha256="abc")
        manifest = session.to_manifest()
        # Should be an ISO 8601 string
        assert "T" in manifest.generated_at
        assert manifest.generated_at.endswith("Z") or "+" in manifest.generated_at


# ---------------------------------------------------------------------------
# TestRenderModuleManifest (Integration)
# ---------------------------------------------------------------------------


class TestRenderModuleManifest:
    """render_module() with ManifestHook produces .amil-manifest.json."""

    def test_render_module_with_manifest_hook_produces_manifest(self, tmp_path, monkeypatch):
        """render_module() with ManifestHook writes .amil-manifest.json."""
        from unittest.mock import MagicMock

        from amil_utils.hooks import ManifestHook
        from amil_utils.manifest import MANIFEST_FILENAME, load_manifest
        from amil_utils.renderer import STAGE_NAMES, render_module

        module_name = "test_mod"
        spec = {"module_name": module_name, "models": [], "odoo_version": "17.0"}
        module_dir = tmp_path / module_name

        # Mock all stage renderers to return Result.ok([])
        from amil_utils.validation.types import Result

        for stage in STAGE_NAMES:
            fn_name = f"render_{stage}"
            mock_fn = MagicMock(return_value=Result(success=True, data=[]))
            monkeypatch.setattr(f"amil_utils.renderer.{fn_name}", mock_fn)

        # Mock validate_spec, _validate_no_cycles, run_preprocessors, context7
        monkeypatch.setattr("amil_utils.renderer.validate_spec", lambda s: MagicMock(model_dump=lambda **kw: spec))
        monkeypatch.setattr("amil_utils.renderer._validate_no_cycles", lambda s: None)
        monkeypatch.setattr("amil_utils.renderer.run_preprocessors", lambda s: s)
        monkeypatch.setattr("amil_utils.renderer.build_context7_from_env", lambda: MagicMock())
        monkeypatch.setattr("amil_utils.renderer.context7_enrich", lambda *a, **kw: {})

        module_dir.mkdir(parents=True, exist_ok=True)
        hooks = [ManifestHook(module_path=module_dir)]

        files, warnings = render_module(
            spec, tmp_path / "templates", tmp_path,
            no_context7=True,
            hooks=hooks,
        )

        manifest = load_manifest(module_dir)
        assert manifest is not None
        assert manifest.module == module_name
        # All 11 stage names should be in manifest.stages
        for name in STAGE_NAMES:
            assert name in manifest.stages

    def test_stage_names_constant_has_all_14_stages(self):
        """STAGE_NAMES constant lists all 15 stages (Phase F16: +website)."""
        from amil_utils.renderer import STAGE_NAMES

        assert len(STAGE_NAMES) == 15
        expected = [
            "manifest", "models", "extensions", "views", "security", "mail_templates",
            "wizards", "tests", "static", "cron", "reports", "controllers", "portal",
            "website", "bulk",
        ]
        assert STAGE_NAMES == expected


# ---------------------------------------------------------------------------
# TestResumeFromStage (Integration)
# ---------------------------------------------------------------------------


class TestResumeFromStage:
    """render_module(resume_from=manifest) skips completed stages with intact artifacts."""

    def test_resume_skips_completed_stages(self, tmp_path, monkeypatch):
        """Resume skips stages marked complete with intact artifacts."""
        from unittest.mock import MagicMock, call

        from amil_utils.manifest import (
            ArtifactEntry,
            ArtifactInfo,
            GenerationManifest,
            StageResult,
            compute_spec_sha256,
        )
        from amil_utils.renderer import STAGE_NAMES, render_module
        from amil_utils.validation.types import Result

        module_name = "test_mod"
        spec = {"module_name": module_name, "models": [], "odoo_version": "17.0"}
        spec_sha = compute_spec_sha256(spec)
        module_dir = tmp_path / module_name
        module_dir.mkdir(parents=True)

        # Create artifact files for completed stages
        (module_dir / "manifest_file.py").write_text("# manifest", encoding="utf-8")
        (module_dir / "models_file.py").write_text("# models", encoding="utf-8")

        from amil_utils.manifest import compute_file_sha256

        manifest_sha = compute_file_sha256(module_dir / "manifest_file.py")
        models_sha = compute_file_sha256(module_dir / "models_file.py")

        # Build a resume manifest with manifest + models complete
        old_manifest = GenerationManifest(
            module=module_name,
            spec_sha256=spec_sha,
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
            stages={
                "manifest": StageResult(status="complete", duration_ms=10, artifacts=["manifest_file.py"]),
                "models": StageResult(status="complete", duration_ms=20, artifacts=["models_file.py"]),
            },
            artifacts=ArtifactInfo(
                files=[
                    ArtifactEntry(path="manifest_file.py", sha256=manifest_sha),
                    ArtifactEntry(path="models_file.py", sha256=models_sha),
                ],
                total_files=2,
                total_lines=2,
            ),
        )

        # Mock stage renderers, track which ones are called
        called_stages = []
        for stage in STAGE_NAMES:
            fn_name = f"render_{stage}"
            def make_mock(s=stage):
                def _mock(*args, **kwargs):
                    called_stages.append(s)
                    return Result(success=True, data=[])
                return _mock
            monkeypatch.setattr(f"amil_utils.renderer.{fn_name}", make_mock())

        # Mock infrastructure
        monkeypatch.setattr("amil_utils.renderer.validate_spec", lambda s: MagicMock(model_dump=lambda **kw: spec))
        monkeypatch.setattr("amil_utils.renderer._validate_no_cycles", lambda s: None)
        monkeypatch.setattr("amil_utils.renderer.run_preprocessors", lambda s: s)
        monkeypatch.setattr("amil_utils.renderer.build_context7_from_env", lambda: MagicMock())
        monkeypatch.setattr("amil_utils.renderer.context7_enrich", lambda *a, **kw: {})

        files, warnings = render_module(
            spec, tmp_path / "templates", tmp_path,
            no_context7=True,
            resume_from=old_manifest,
        )

        # manifest and models should NOT have been called (skipped)
        assert "manifest" not in called_stages
        assert "models" not in called_stages
        # remaining 9 stages should have been called
        remaining = [s for s in STAGE_NAMES if s not in ("manifest", "models")]
        for s in remaining:
            assert s in called_stages


# ---------------------------------------------------------------------------
# TestResumeSpecChanged (Integration)
# ---------------------------------------------------------------------------


class TestResumeSpecChanged:
    """resume_from with different spec_sha256 triggers full re-run."""

    def test_spec_change_triggers_full_rerun(self, tmp_path, monkeypatch):
        """Changed spec_sha256 causes all stages to run (no skipping)."""
        from unittest.mock import MagicMock

        from amil_utils.manifest import GenerationManifest, StageResult
        from amil_utils.renderer import STAGE_NAMES, render_module
        from amil_utils.validation.types import Result

        module_name = "test_mod"
        spec = {"module_name": module_name, "models": [], "odoo_version": "17.0"}
        module_dir = tmp_path / module_name
        module_dir.mkdir(parents=True)

        # Old manifest with DIFFERENT spec_sha256
        old_manifest = GenerationManifest(
            module=module_name,
            spec_sha256="DIFFERENT_SHA256_VALUE",
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
            stages={
                "manifest": StageResult(status="complete"),
                "models": StageResult(status="complete"),
            },
        )

        called_stages = []
        for stage in STAGE_NAMES:
            fn_name = f"render_{stage}"
            def make_mock(s=stage):
                def _mock(*args, **kwargs):
                    called_stages.append(s)
                    return Result(success=True, data=[])
                return _mock
            monkeypatch.setattr(f"amil_utils.renderer.{fn_name}", make_mock())

        monkeypatch.setattr("amil_utils.renderer.validate_spec", lambda s: MagicMock(model_dump=lambda **kw: spec))
        monkeypatch.setattr("amil_utils.renderer._validate_no_cycles", lambda s: None)
        monkeypatch.setattr("amil_utils.renderer.run_preprocessors", lambda s: s)
        monkeypatch.setattr("amil_utils.renderer.build_context7_from_env", lambda: MagicMock())
        monkeypatch.setattr("amil_utils.renderer.context7_enrich", lambda *a, **kw: {})

        files, warnings = render_module(
            spec, tmp_path / "templates", tmp_path,
            no_context7=True,
            resume_from=old_manifest,
        )

        # ALL 15 stages should have been called (full re-run, Phase F16: +website)
        assert len(called_stages) == 15
        assert called_stages == STAGE_NAMES


# ---------------------------------------------------------------------------
# TestResumeIntegrityCheck (Integration)
# ---------------------------------------------------------------------------


class TestResumeIntegrityCheck:
    """resume_from with modified artifact file re-runs that stage."""

    def test_tampered_artifact_reruns_stage(self, tmp_path, monkeypatch):
        """A completed stage with modified artifact SHA256 re-runs."""
        from unittest.mock import MagicMock

        from amil_utils.manifest import (
            ArtifactEntry,
            ArtifactInfo,
            GenerationManifest,
            StageResult,
            compute_file_sha256,
            compute_spec_sha256,
        )
        from amil_utils.renderer import STAGE_NAMES, render_module
        from amil_utils.validation.types import Result

        module_name = "test_mod"
        spec = {"module_name": module_name, "models": [], "odoo_version": "17.0"}
        spec_sha = compute_spec_sha256(spec)
        module_dir = tmp_path / module_name
        module_dir.mkdir(parents=True)

        # Create artifact file for manifest stage -- then tamper it
        (module_dir / "manifest_file.py").write_text("# original", encoding="utf-8")
        original_sha = compute_file_sha256(module_dir / "manifest_file.py")
        # Now tamper the file
        (module_dir / "manifest_file.py").write_text("# TAMPERED content", encoding="utf-8")

        old_manifest = GenerationManifest(
            module=module_name,
            spec_sha256=spec_sha,
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
            stages={
                "manifest": StageResult(status="complete", duration_ms=10, artifacts=["manifest_file.py"]),
            },
            artifacts=ArtifactInfo(
                files=[ArtifactEntry(path="manifest_file.py", sha256=original_sha)],
                total_files=1,
                total_lines=1,
            ),
        )

        called_stages = []
        for stage in STAGE_NAMES:
            fn_name = f"render_{stage}"
            def make_mock(s=stage):
                def _mock(*args, **kwargs):
                    called_stages.append(s)
                    return Result(success=True, data=[])
                return _mock
            monkeypatch.setattr(f"amil_utils.renderer.{fn_name}", make_mock())

        monkeypatch.setattr("amil_utils.renderer.validate_spec", lambda s: MagicMock(model_dump=lambda **kw: spec))
        monkeypatch.setattr("amil_utils.renderer._validate_no_cycles", lambda s: None)
        monkeypatch.setattr("amil_utils.renderer.run_preprocessors", lambda s: s)
        monkeypatch.setattr("amil_utils.renderer.build_context7_from_env", lambda: MagicMock())
        monkeypatch.setattr("amil_utils.renderer.context7_enrich", lambda *a, **kw: {})

        files, warnings = render_module(
            spec, tmp_path / "templates", tmp_path,
            no_context7=True,
            resume_from=old_manifest,
        )

        # manifest stage SHOULD have been re-run (artifact tampered)
        assert "manifest" in called_stages


# ---------------------------------------------------------------------------
# TestCLIResume (Integration)
# ---------------------------------------------------------------------------


class TestCLIResume:
    """CLI render-module with --resume flag loads manifest and passes to render_module."""

    def test_resume_flag_loads_manifest(self, tmp_path, monkeypatch):
        """CLI render-module with --resume loads existing manifest as resume_from."""
        from unittest.mock import MagicMock, patch

        from click.testing import CliRunner

        from amil_utils.cli import main
        from amil_utils.manifest import GenerationManifest, save_manifest

        module_name = "test_resume_mod"
        spec = {"module_name": module_name, "models": [], "odoo_version": "17.0"}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")

        module_dir = tmp_path / "output" / module_name
        module_dir.mkdir(parents=True)

        # Save a manifest in the module dir
        manifest = GenerationManifest(
            module=module_name,
            spec_sha256="abc123",
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
        )
        save_manifest(manifest, module_dir)

        # Mock render_module at its source to capture args
        captured_kwargs = {}
        def mock_render_module(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return ([], [])

        with patch("amil_utils.renderer.render_module", mock_render_module):
            runner = CliRunner()
            result = runner.invoke(main, [
                "render-module",
                "--spec-file", str(spec_file),
                "--output-dir", str(tmp_path / "output"),
                "--no-context7",
                "--resume",
            ])

        # resume_from should be a GenerationManifest (not None)
        assert "resume_from" in captured_kwargs
        assert captured_kwargs["resume_from"] is not None

    def test_no_resume_flag_passes_none(self, tmp_path, monkeypatch):
        """CLI render-module without --resume passes resume_from=None."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from amil_utils.cli import main

        module_name = "test_no_resume"
        spec = {"module_name": module_name, "models": [], "odoo_version": "17.0"}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")

        captured_kwargs = {}
        def mock_render_module(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return ([], [])

        with patch("amil_utils.renderer.render_module", mock_render_module):
            runner = CliRunner()
            result = runner.invoke(main, [
                "render-module",
                "--spec-file", str(spec_file),
                "--output-dir", str(tmp_path / "output"),
                "--no-context7",
            ])

        # resume_from should be None when --resume not passed
        assert captured_kwargs.get("resume_from") is None


# ---------------------------------------------------------------------------
# TestShowStateManifest (Integration)
# ---------------------------------------------------------------------------


class TestShowStateManifest:
    """show-state reads .amil-manifest.json first, falls back to old format."""

    def test_show_state_reads_manifest(self, tmp_path):
        """show-state on dir with .amil-manifest.json displays manifest summary."""
        from click.testing import CliRunner

        from amil_utils.cli import main
        from amil_utils.manifest import (
            ArtifactInfo,
            GenerationManifest,
            StageResult,
            save_manifest,
        )

        manifest = GenerationManifest(
            module="my_test_module",
            spec_sha256="abc123def456",
            generated_at="2026-03-08T12:00:00Z",
            generator_version="0.1.0",
            stages={
                "manifest": StageResult(status="complete", duration_ms=10),
                "models": StageResult(status="complete", duration_ms=50),
            },
            artifacts=ArtifactInfo(total_files=5, total_lines=200),
        )
        save_manifest(manifest, tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["show-state", str(tmp_path)])

        assert result.exit_code == 0
        assert "my_test_module" in result.output
        assert "[OK]" in result.output
        assert "5" in result.output  # total files

    def test_show_state_json_output(self, tmp_path):
        """show-state --json outputs raw manifest JSON."""
        from click.testing import CliRunner

        from amil_utils.cli import main
        from amil_utils.manifest import GenerationManifest, save_manifest

        manifest = GenerationManifest(
            module="json_test",
            spec_sha256="abc",
            generated_at="2026-01-01T00:00:00Z",
            generator_version="0.1.0",
        )
        save_manifest(manifest, tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["show-state", "--json", str(tmp_path)])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["module"] == "json_test"

    def test_show_state_legacy_state_file_message(self, tmp_path):
        """show-state with only old .amil-state.json shows legacy message."""
        from click.testing import CliRunner

        from amil_utils.cli import main

        # Write an old-format state file
        state_data = {
            "module_name": "old_mod",
            "artifacts": [],
            "generated_at": "2026-01-01",
        }
        state_file = tmp_path / ".amil-state.json"
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["show-state", str(tmp_path)])

        assert result.exit_code == 0
        assert "Legacy state file found" in result.output
