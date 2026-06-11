"""Tests for ERP CLI groups: decomposition and spec.

These are wiring-layer tests — the library functions are already tested
in their own module test files. Here we verify Click integration works
end-to-end (args → JSON output, exit codes, file writes).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from amil_utils.orchestrator.cli import orch_group


# ─── Shared fixture helpers ────────────────────────────────────────────────────


def _make_research(tmp_path: Path) -> Path:
    """Create .planning/research with the canonical 4-file fixture shape."""
    planning = tmp_path / ".planning"
    planning.mkdir(exist_ok=True)
    research = planning / "research"
    research.mkdir()

    boundaries = {
        "modules": [
            {
                "name": "hr_core",
                "description": "Core HR",
                "models": ["hr.employee", "hr.department"],
                "base_depends": ["base", "mail"],
                "estimated_complexity": "medium",
            },
            {
                "name": "hr_leave",
                "description": "Leave management",
                "models": ["hr.leave", "hr.leave.type"],
                "base_depends": ["base"],
                "estimated_complexity": "low",
            },
            {
                "name": "hr_payroll",
                "description": "Payroll",
                "models": ["hr.payslip"],
                "base_depends": ["account"],
                "estimated_complexity": "high",
            },
        ],
    }
    (research / "module-boundaries.json").write_text(json.dumps(boundaries))

    oca = {
        "findings": [
            {
                "odoo_module": "hr_leave",
                "recommendation": "fork_extend",
                "oca_module": "hr_holidays",
            },
        ],
    }
    (research / "oca-analysis.json").write_text(json.dumps(oca))

    dep_map = {
        "dependencies": [
            {"module": "hr_core", "depends_on": []},
            {"module": "hr_leave", "depends_on": ["hr_core"]},
            {"module": "hr_payroll", "depends_on": ["hr_core", "hr_leave"]},
        ],
    }
    (research / "dependency-map.json").write_text(json.dumps(dep_map))

    chains = {
        "chains": [
            {
                "name": "leave_to_payroll",
                "description": "Leave deductions",
                "steps": ["hr_leave.compute_days", "hr_payroll.compute_deduction"],
                "cross_module": True,
            },
        ],
    }
    (research / "computation-chains.json").write_text(json.dumps(chains))

    return research


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ─── decomposition merge + format ─────────────────────────────────────────────


class TestDecompositionMerge:
    def test_merge_exits_0_and_emits_module_count(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        _make_research(tmp_path)
        result = runner.invoke(
            orch_group,
            ["decomposition", "merge", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["module_count"] == 3

    def test_merge_writes_decomposition_json(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        _make_research(tmp_path)
        runner.invoke(
            orch_group,
            ["decomposition", "merge", "--cwd", str(tmp_path)],
        )
        decomp_path = tmp_path / ".planning" / "research" / "decomposition.json"
        assert decomp_path.exists()
        data = json.loads(decomp_path.read_text())
        assert "modules" in data
        assert len(data["modules"]) == 3


class TestDecompositionFormat:
    def test_format_prints_table_with_modules_and_tiers(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        _make_research(tmp_path)
        # merge first to produce decomposition.json
        runner.invoke(
            orch_group,
            ["decomposition", "merge", "--cwd", str(tmp_path)],
        )
        result = runner.invoke(
            orch_group,
            ["decomposition", "format", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "hr_core" in result.output
        assert "TIER" in result.output or "tier" in result.output.lower()

    def test_format_missing_decomposition_exits_1_with_error_on_stderr(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """format with no decomposition.json must exit 1 with ERROR on stderr."""
        result = runner.invoke(
            orch_group,
            ["decomposition", "format", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 1
        # CliRunner mixes stderr into output by default; check combined output
        assert "ERROR" in result.output


# ─── decomposition roadmap ────────────────────────────────────────────────────


class TestDecompositionRoadmap:
    def test_roadmap_writes_planning_roadmap_md(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        _make_research(tmp_path)
        runner.invoke(
            orch_group,
            ["decomposition", "merge", "--cwd", str(tmp_path)],
        )
        result = runner.invoke(
            orch_group,
            ["decomposition", "roadmap", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        roadmap_path = tmp_path / ".planning" / "ROADMAP.md"
        assert roadmap_path.exists()
        content = roadmap_path.read_text()
        assert content.startswith("# ERP Module Roadmap")
        assert "hr_core" in content
        assert "hr_leave" in content
        assert "hr_payroll" in content

    def test_roadmap_missing_decomposition_exits_0_with_error_json(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """roadmap with no decomposition.json must exit 0 with JSON error payload."""
        result = runner.invoke(
            orch_group,
            ["decomposition", "roadmap", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "error" in payload


# ─── decomposition init-modules ───────────────────────────────────────────────


class TestDecompositionInitModules:
    def test_init_modules_missing_decomposition_exits_0_with_error_json(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """init-modules with no decomposition.json must exit 0 with JSON error payload."""
        result = runner.invoke(
            orch_group,
            ["decomposition", "init-modules", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "error" in payload

    def test_init_modules_creates_entries_in_order(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        _make_research(tmp_path)
        # Ensure .planning exists for module_status_init
        (tmp_path / ".planning").mkdir(exist_ok=True)
        runner.invoke(
            orch_group,
            ["decomposition", "merge", "--cwd", str(tmp_path)],
        )
        result = runner.invoke(
            orch_group,
            ["decomposition", "init-modules", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "initialized" in payload
        initialized = payload["initialized"]
        assert "hr_core" in initialized
        assert "hr_leave" in initialized
        assert "hr_payroll" in initialized
        # generation order: hr_core < hr_leave < hr_payroll
        assert initialized.index("hr_core") < initialized.index("hr_leave")
        assert initialized.index("hr_leave") < initialized.index("hr_payroll")

    def test_init_modules_writes_module_status_json(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        _make_research(tmp_path)
        (tmp_path / ".planning").mkdir(exist_ok=True)
        runner.invoke(
            orch_group,
            ["decomposition", "merge", "--cwd", str(tmp_path)],
        )
        runner.invoke(
            orch_group,
            ["decomposition", "init-modules", "--cwd", str(tmp_path)],
        )
        status_path = tmp_path / ".planning" / "module_status.json"
        assert status_path.exists()
        data = json.loads(status_path.read_text())
        assert "hr_core" in data["modules"]
        assert "hr_leave" in data["modules"]
        assert "hr_payroll" in data["modules"]


# ─── spec check-keys ──────────────────────────────────────────────────────────


class TestSpecCheckKeys:
    def _write_minimal_spec(self, tmp_path: Path) -> Path:
        spec = {"module_name": "hr_core"}
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec))
        return spec_path

    def _write_full_spec(self, tmp_path: Path) -> Path:
        spec = {
            "module_name": "hr_core",
            "module_title": "HR Core",
            "odoo_version": "17.0",
            "depends": ["base", "mail"],
            "models": [{"name": "hr.employee", "fields": []}],
            "business_rules": [],
            "computation_chains": [],
            "workflow": [],
            "view_hints": [],
            "reports": [],
            "notifications": [],
            "cron_jobs": [],
            "security": {"roles": ["manager"]},
            "portal": False,
            "controllers": [],
        }
        spec_path = tmp_path / "spec_full.json"
        spec_path.write_text(json.dumps(spec))
        return spec_path

    def test_minimal_flag_valid_spec_exits_0(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        spec_path = self._write_minimal_spec(tmp_path)
        result = runner.invoke(
            orch_group,
            ["spec", "check-keys", str(spec_path), "--minimal", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["valid"] is True
        assert "model_count" in payload

    def test_full_mode_missing_keys_exits_1(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # minimal spec is missing all full required keys except module_name
        spec_path = self._write_minimal_spec(tmp_path)
        result = runner.invoke(
            orch_group,
            ["spec", "check-keys", str(spec_path), "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["valid"] is False
        assert "models" in payload["missing"]

    def test_full_mode_complete_spec_exits_0(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        spec_path = self._write_full_spec(tmp_path)
        result = runner.invoke(
            orch_group,
            ["spec", "check-keys", str(spec_path), "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["valid"] is True


# ─── spec score-all ────────────────────────────────────────────────────────────


class TestSpecScoreAll:
    def _seed_planned_module(self, tmp_path: Path) -> None:
        """Seed .planning/research/decomposition.json with one planned module."""
        _make_research(tmp_path)
        # We call merge via the library directly to avoid CLI coupling in setup
        from amil_utils.orchestrator.decomposition import merge_decomposition

        research_dir = tmp_path / ".planning" / "research"
        merge_decomposition(str(research_dir), str(tmp_path))

    def test_score_all_exits_0_and_has_keys(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        self._seed_planned_module(tmp_path)
        result = runner.invoke(
            orch_group,
            ["spec", "score-all", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "scores" in payload
        assert "batches" in payload
        assert "summary" in payload

    def test_score_all_scores_contains_module_names(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        self._seed_planned_module(tmp_path)
        result = runner.invoke(
            orch_group,
            ["spec", "score-all", "--cwd", str(tmp_path)],
        )
        payload = json.loads(result.output)
        scores = payload["scores"]
        assert "hr_core" in scores
        assert "hr_leave" in scores
        assert "hr_payroll" in scores

    def test_score_all_includes_cross_module_points(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Modules with no unresolved comodel refs must receive cross-module bonus (+10).

        We verify CLI output matches the library directly: call score_all_modules on
        the same normalised fixture and compare — guaranteeing CLI == library behaviour.
        """
        import json as _json
        from pathlib import Path as _Path

        from amil_utils.orchestrator.spec_completeness import score_all_modules

        self._seed_planned_module(tmp_path)

        # Build the same normalised decomp the CLI will produce
        decomp_path = tmp_path / ".planning" / "research" / "decomposition.json"
        decomp = _json.loads(decomp_path.read_text(encoding="utf-8"))

        from amil_utils.orchestrator.cli_erp_commands import _normalise_for_scoring

        normalised_modules = [_normalise_for_scoring(m) for m in decomp.get("modules", [])]
        normalised_decomp = {**decomp, "modules": normalised_modules}
        expected_scores = score_all_modules(normalised_decomp)

        result = runner.invoke(
            orch_group,
            ["spec", "score-all", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.output)
        cli_scores = payload["scores"]

        # CLI output must exactly match the library for every module
        for mod_name, expected in expected_scores.items():
            assert cli_scores[mod_name]["score"] == expected["score"], (
                f"{mod_name}: CLI score {cli_scores[mod_name]['score']} "
                f"!= library score {expected['score']}"
            )

        # Fixture modules have no fields → no comodel refs → cross-module bonus earned.
        # The cross-module bonus (+10) is only awarded when all_module_names is non-empty,
        # so this assertion would fail if score_all_modules were called with [] instead.
        all_module_names = [m["name"] for m in normalised_modules]
        assert len(all_module_names) > 1, "Need >1 module for cross-module scoring to trigger"
        for mod_name in all_module_names:
            assert cli_scores[mod_name]["cross_module_issues"] == [], (
                f"{mod_name} unexpectedly has cross_module_issues: "
                f"{cli_scores[mod_name]['cross_module_issues']}"
            )
