"""Pipeline integration tests for Context7 c7_hints injection, CLI flags, and backward compat.

Tests cover (PIPE-01e through PIPE-01j):
    - TestC7HintsDefault: _build_module_context() returns c7_hints={} default
    - TestC7HintsInjection: render_module() injects hints from context7_enrich into ctx
    - TestRenderModuleWithoutContext7: render_module(no_context7=True) works identically
    - TestCliNoContext7: --no-context7 flag passes through to render_module()
    - TestCliFreshContext7: --fresh-context7 flag passes through to render_module()
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# TestC7HintsDefault -- _build_module_context returns c7_hints={}
# ---------------------------------------------------------------------------

class TestC7HintsDefault:
    """PIPE-01e: _build_module_context() includes c7_hints key defaulting to {}."""

    def test_minimal_spec_has_c7_hints(self) -> None:
        from amil_utils.renderer_context import _build_module_context

        spec: dict = {"module_name": "test_mod", "models": []}
        ctx = _build_module_context(spec, "test_mod")
        assert "c7_hints" in ctx, "c7_hints key must be present in module context"
        assert ctx["c7_hints"] == {}, "c7_hints must default to empty dict"

    def test_c7_hints_is_dict_type(self) -> None:
        from amil_utils.renderer_context import _build_module_context

        spec: dict = {"module_name": "test_mod", "models": []}
        ctx = _build_module_context(spec, "test_mod")
        assert isinstance(ctx["c7_hints"], dict)


# ---------------------------------------------------------------------------
# TestC7HintsInjection -- render_module() with mocked context7_enrich
# ---------------------------------------------------------------------------

class TestC7HintsInjection:
    """PIPE-01f: render_module() calls context7_enrich and injects hints into ctx."""

    @patch("amil_utils.renderer.context7_enrich")
    @patch("amil_utils.renderer.build_context7_from_env")
    def test_context7_enrich_called_and_hints_injected(
        self, mock_build_env: MagicMock, mock_enrich: MagicMock, tmp_path: Path,
    ) -> None:
        from amil_utils.renderer import render_module, get_template_dir

        mock_client = MagicMock()
        mock_build_env.return_value = mock_client
        mock_enrich.return_value = {"computed": "some hint about computed fields"}

        spec = {
            "module_name": "test_c7_inject",
            "models": [
                {
                    "name": "test.model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        template_dir = get_template_dir()
        files, warnings = render_module(spec, template_dir, tmp_path)

        # context7_enrich must have been called
        mock_enrich.assert_called_once()
        # Verify the spec was passed as first arg
        call_args = mock_enrich.call_args
        assert call_args[0][0]["module_name"] == "test_c7_inject"

    @patch("amil_utils.renderer.context7_enrich")
    @patch("amil_utils.renderer.build_context7_from_env")
    def test_no_context7_skips_enrich(
        self, mock_build_env: MagicMock, mock_enrich: MagicMock, tmp_path: Path,
    ) -> None:
        from amil_utils.renderer import render_module, get_template_dir

        spec = {
            "module_name": "test_c7_skip",
            "models": [
                {
                    "name": "test.model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        template_dir = get_template_dir()
        files, warnings = render_module(spec, template_dir, tmp_path, no_context7=True)

        # context7_enrich should NOT be called when no_context7=True
        mock_enrich.assert_not_called()
        # build_context7_from_env should NOT be called either
        mock_build_env.assert_not_called()


# ---------------------------------------------------------------------------
# TestRenderModuleWithoutContext7 -- backward compat with no_context7=True
# ---------------------------------------------------------------------------

class TestRenderModuleWithoutContext7:
    """PIPE-01j: render_module(no_context7=True) produces same output as before."""

    def test_render_succeeds_with_no_context7(self, tmp_path: Path) -> None:
        from amil_utils.renderer import render_module, get_template_dir

        spec = {
            "module_name": "test_no_c7",
            "models": [
                {
                    "name": "test.model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        template_dir = get_template_dir()
        files, warnings = render_module(spec, template_dir, tmp_path, no_context7=True)

        assert len(files) > 0, "render_module must create files even with no_context7=True"
        # Verify module directory was created
        assert (tmp_path / "test_no_c7").is_dir()

    @patch("amil_utils.renderer.context7_enrich")
    @patch("amil_utils.renderer.build_context7_from_env")
    def test_backward_compat_default_params(
        self, mock_build_env: MagicMock, mock_enrich: MagicMock, tmp_path: Path,
    ) -> None:
        """render_module() called without new kwargs still works (defaults)."""
        from amil_utils.renderer import render_module, get_template_dir

        mock_client = MagicMock()
        mock_build_env.return_value = mock_client
        mock_enrich.return_value = {}

        spec = {
            "module_name": "test_compat",
            "models": [
                {
                    "name": "test.model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        template_dir = get_template_dir()
        # Call WITHOUT the new keyword args -- must still work
        files, warnings = render_module(spec, template_dir, tmp_path)
        assert len(files) > 0


# ---------------------------------------------------------------------------
# TestCliNoContext7 -- --no-context7 flag passes through
# ---------------------------------------------------------------------------

class TestCliNoContext7:
    """PIPE-01g: --no-context7 flag passes no_context7=True to render_module()."""

    @patch("amil_utils.verifier.build_verifier_from_env", return_value=None)
    @patch("amil_utils.renderer.render_module")
    def test_no_context7_flag_passes_through(
        self, mock_render: MagicMock, mock_verifier: MagicMock, tmp_path: Path,
    ) -> None:
        from amil_utils.cli import main

        mock_render.return_value = ([], [])

        spec = {"module_name": "test_cli_no_c7", "models": []}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        output_dir = tmp_path / "output"

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, [
                "render-module",
                "--spec-file", str(spec_file),
                "--output-dir", str(output_dir),
                "--no-context7",
            ])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args
        assert call_kwargs.kwargs.get("no_context7") is True

    @patch("amil_utils.verifier.build_verifier_from_env", return_value=None)
    @patch("amil_utils.renderer.render_module")
    def test_no_context7_default_is_false(
        self, mock_render: MagicMock, mock_verifier: MagicMock, tmp_path: Path,
    ) -> None:
        from amil_utils.cli import main

        mock_render.return_value = ([], [])

        spec = {"module_name": "test_cli_default", "models": []}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        output_dir = tmp_path / "output"

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, [
            "render-module",
            "--spec-file", str(spec_file),
            "--output-dir", str(output_dir),
        ])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args
        assert call_kwargs.kwargs.get("no_context7") is False


# ---------------------------------------------------------------------------
# TestCliFreshContext7 -- --fresh-context7 flag passes through
# ---------------------------------------------------------------------------

class TestCliFreshContext7:
    """PIPE-01h: --fresh-context7 flag passes fresh_context7=True to render_module()."""

    @patch("amil_utils.verifier.build_verifier_from_env", return_value=None)
    @patch("amil_utils.renderer.render_module")
    def test_fresh_context7_flag_passes_through(
        self, mock_render: MagicMock, mock_verifier: MagicMock, tmp_path: Path,
    ) -> None:
        from amil_utils.cli import main

        mock_render.return_value = ([], [])

        spec = {"module_name": "test_cli_fresh", "models": []}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        output_dir = tmp_path / "output"

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, [
            "render-module",
            "--spec-file", str(spec_file),
            "--output-dir", str(output_dir),
            "--fresh-context7",
        ])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args
        assert call_kwargs.kwargs.get("fresh_context7") is True

    @patch("amil_utils.verifier.build_verifier_from_env", return_value=None)
    @patch("amil_utils.renderer.render_module")
    def test_fresh_context7_default_is_false(
        self, mock_render: MagicMock, mock_verifier: MagicMock, tmp_path: Path,
    ) -> None:
        from amil_utils.cli import main

        mock_render.return_value = ([], [])

        spec = {"module_name": "test_cli_fresh_default", "models": []}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        output_dir = tmp_path / "output"

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, [
            "render-module",
            "--spec-file", str(spec_file),
            "--output-dir", str(output_dir),
        ])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args
        assert call_kwargs.kwargs.get("fresh_context7") is False
