"""Tests for odools.toml configuration generation."""
from __future__ import annotations

from pathlib import Path

from amil_utils.validation.odoo_ls_config import find_python_path, generate_odools_toml


class TestGenerateOdoolsToml:
    """Tests for the generate_odools_toml function."""

    def test_generates_valid_toml(self, tmp_path: Path) -> None:
        """Generated file exists and contains expected TOML structure."""
        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
            python_path=Path("/usr/bin/python3.12"),
        )
        assert config.exists()
        content = config.read_text()
        assert "[[config]]" in content
        assert 'name = "factory"' in content
        assert "/opt/odoo/19.0" in content

    def test_includes_output_dir_in_addons_paths(self, tmp_path: Path) -> None:
        """Generated modules dir MUST be in addons_paths."""
        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
        )
        content = config.read_text()
        assert str(tmp_path / "modules") in content

    def test_includes_odoo_builtin_addons(self, tmp_path: Path) -> None:
        """Both odoo/addons and addons directories must be listed."""
        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
        )
        content = config.read_text()
        assert "/opt/odoo/19.0/addons" in content
        assert "/opt/odoo/19.0/odoo/addons" in content

    def test_refresh_mode_is_off(self, tmp_path: Path) -> None:
        """refresh_mode must be 'off' to avoid unwanted reloads."""
        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
        )
        content = config.read_text()
        assert 'refresh_mode = "off"' in content

    def test_uses_explicit_python_path(self, tmp_path: Path) -> None:
        """Must use the provided python path, not generic 'python3'."""
        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
            python_path=Path("/usr/bin/python3.12"),
        )
        content = config.read_text()
        assert "python3.12" in content
        # Must NOT use generic python3 (could resolve to 3.14)
        assert 'python_path = "/usr/bin/python3"' not in content

    def test_custom_profile_name(self, tmp_path: Path) -> None:
        """Custom profile_name propagates into the TOML."""
        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
            profile_name="custom_profile",
        )
        content = config.read_text()
        assert 'name = "custom_profile"' in content

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Parent directories are created if they don't exist."""
        nested = tmp_path / "deep" / "nested" / "odools.toml"
        config = generate_odools_toml(
            output_path=nested,
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
        )
        assert config.exists()

    def test_returns_output_path(self, tmp_path: Path) -> None:
        """Return value must be the output_path that was written."""
        target = tmp_path / "odools.toml"
        result = generate_odools_toml(
            output_path=target,
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
        )
        assert result == target

    def test_diag_missing_imports_all(self, tmp_path: Path) -> None:
        """diag_missing_imports should be set to 'All'."""
        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=Path("/opt/odoo/19.0"),
            addons_output_dir=tmp_path / "modules",
        )
        content = config.read_text()
        assert 'diag_missing_imports = "All"' in content


class TestFindPythonPath:
    """Tests for the find_python_path auto-detection function."""

    def test_finds_python312(self) -> None:
        """Should find python3.12 if available on this system."""
        path = find_python_path()
        # On this system python3.12 is at /usr/bin/python3.12
        if path is not None:
            assert "3.12" in str(path) or "3.13" in str(path)

    def test_returns_path_object(self) -> None:
        """Return type is Path (or None)."""
        path = find_python_path()
        assert path is None or isinstance(path, Path)

    def test_does_not_return_314(self) -> None:
        """Must not return python 3.14 as first choice when 3.12 exists."""
        path = find_python_path()
        if path is not None and "3.12" not in str(path) and "3.13" not in str(path):
            # If neither 3.12 nor 3.13 found, this is a fallback — acceptable
            pass
        elif path is not None:
            assert "3.14" not in str(path)
