"""Tests for Pydantic v2 spec schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from amil_utils.spec_schema import (
    ApprovalLevelSpec,
    ApprovalSpec,
    ConstraintSpec,
    CronJobSpec,
    FieldSpec,
    ModelSpec,
    ModuleSpec,
    ReportSpec,
    SecurityACLSpec,
    SecurityBlockSpec,
    VALID_FIELD_TYPES,
    WebhookSpec,
    format_validation_errors,
    validate_spec,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# TestValidateSpec
# ---------------------------------------------------------------------------
class TestValidateSpec:
    """Test validate_spec() entry point."""

    def test_valid_spec_v1(self):
        """Valid spec_v1.json returns ModuleSpec instance."""
        raw = _load_fixture("spec_v1.json")
        result = validate_spec(raw)
        assert isinstance(result, ModuleSpec)
        assert result.module_name == "uni_fee"

    def test_valid_spec_v2(self):
        """Valid spec_v2.json returns ModuleSpec instance."""
        raw = _load_fixture("spec_v2.json")
        result = validate_spec(raw)
        assert isinstance(result, ModuleSpec)
        assert result.module_name == "uni_fee"

    def test_missing_module_name(self):
        """Missing required field 'module_name' raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_spec({"models": []})
        errors = exc_info.value.errors()
        locs = [tuple(e["loc"]) for e in errors]
        assert ("module_name",) in locs

    def test_optional_defaults(self):
        """Optional fields get correct defaults."""
        result = validate_spec({"module_name": "test_mod"})
        assert result.odoo_version == "19.0"
        assert result.license == "LGPL-3"
        assert result.depends == ["base"]
        assert result.application is True
        assert result.category == "Uncategorized"
        assert result.models == []
        assert result.cron_jobs == []
        assert result.reports == []


# ---------------------------------------------------------------------------
# TestFieldTypeValidation
# ---------------------------------------------------------------------------
class TestFieldTypeValidation:
    """Test @field_validator for field types."""

    def test_valid_types_accepted(self):
        """All 16 valid Odoo field types are accepted."""
        for ftype in sorted(VALID_FIELD_TYPES):
            field = FieldSpec(name="test_field", type=ftype)
            assert field.type == ftype

    def test_invalid_type_rejected(self):
        """Invalid field type 'Strig' raises ValidationError with valid types."""
        with pytest.raises(ValidationError) as exc_info:
            FieldSpec(name="bad_field", type="Strig")
        error_msg = str(exc_info.value)
        assert "Strig" in error_msg
        # Should mention at least some valid types
        assert "Char" in error_msg or "valid" in error_msg.lower()

    def test_valid_types_count(self):
        """There are exactly 16 valid field types."""
        assert len(VALID_FIELD_TYPES) == 16


# ---------------------------------------------------------------------------
# TestExtraAllow
# ---------------------------------------------------------------------------
class TestExtraAllow:
    """Test extra='allow' preserves unknown keys."""

    def test_unknown_keys_preserved(self):
        """Unknown extra keys are preserved through validate -> model_dump."""
        raw = {
            "module_name": "test_mod",
            "custom_key": "custom_value",
            "another_extra": 42,
        }
        result = validate_spec(raw)
        dumped = result.model_dump()
        assert dumped["custom_key"] == "custom_value"
        assert dumped["another_extra"] == 42

    def test_roundtrip_fidelity(self):
        """model_dump() output matches original spec dict for known keys."""
        raw = _load_fixture("spec_v1.json")
        result = validate_spec(raw)
        dumped = result.model_dump()
        assert dumped["module_name"] == raw["module_name"]
        assert dumped["odoo_version"] == raw["odoo_version"]
        assert dumped["version"] == raw["version"]
        assert dumped["depends"] == raw["depends"]
        assert len(dumped["models"]) == len(raw["models"])
        assert len(dumped["cron_jobs"]) == len(raw["cron_jobs"])
        assert len(dumped["reports"]) == len(raw["reports"])


# ---------------------------------------------------------------------------
# TestCrossRefValidators
# ---------------------------------------------------------------------------
class TestCrossRefValidators:
    """Test cross-reference model validators on ModuleSpec."""

    def test_approval_role_not_in_security_roles(self):
        """Approval role not in security.roles raises ValidationError."""
        raw = {
            "module_name": "test_mod",
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char"}],
                    "security": {
                        "roles": ["viewer", "editor"],
                        "acl": {},
                    },
                    "approval": {
                        "levels": [
                            {"name": "submitted", "role": "nonexistent_role"},
                        ],
                    },
                },
            ],
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_spec(raw)
        error_msg = str(exc_info.value)
        assert "nonexistent_role" in error_msg

    def test_audit_exclude_field_not_in_model(self):
        """audit_exclude field not in model fields raises ValidationError."""
        raw = {
            "module_name": "test_mod",
            "models": [
                {
                    "name": "test.model",
                    "fields": [
                        {"name": "name", "type": "Char"},
                        {"name": "value", "type": "Float"},
                    ],
                    "audit": True,
                    "audit_exclude": ["nonexistent_field"],
                },
            ],
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_spec(raw)
        error_msg = str(exc_info.value)
        assert "nonexistent_field" in error_msg


# ---------------------------------------------------------------------------
# TestFormatErrors
# ---------------------------------------------------------------------------
class TestFormatErrors:
    """Test format_validation_errors() output."""

    def test_format_single_error(self):
        """format_validation_errors() with single error is human-readable."""
        try:
            validate_spec({"module_name": "bad_mod", "models": [
                {"name": "m", "fields": [{"name": "f", "type": "Strig"}]}
            ]})
            pytest.fail("Should have raised ValidationError")
        except ValidationError as e:
            output = format_validation_errors(e, "bad_mod")
            assert "Spec validation failed for bad_mod:" in output
            assert "Strig" in output

    def test_format_multiple_errors(self):
        """format_validation_errors() with multiple errors lists all paths."""
        try:
            ModuleSpec(
                module_name="bad_mod",
                models=[
                    {
                        "name": "m1",
                        "fields": [
                            {"name": "f1", "type": "Strig"},
                            {"name": "f2", "type": "Integre"},
                        ],
                    }
                ],
            )
            pytest.fail("Should have raised ValidationError")
        except ValidationError as e:
            output = format_validation_errors(e, "bad_mod")
            assert "Spec validation failed for bad_mod:" in output
            assert "Strig" in output
            assert "Integre" in output


# ---------------------------------------------------------------------------
# TestFixtureCompat
# ---------------------------------------------------------------------------
class TestFixtureCompat:
    """Test that real fixture files validate without modification."""

    def test_spec_v1_validates(self):
        """spec_v1.json validates via validate_spec()."""
        raw = _load_fixture("spec_v1.json")
        result = validate_spec(raw)
        assert result.module_name == "uni_fee"
        assert len(result.models) == 2
        assert result.models[0].name == "fee.invoice"
        assert len(result.models[0].fields) == 8

    def test_spec_v2_validates(self):
        """spec_v2.json validates via validate_spec()."""
        raw = _load_fixture("spec_v2.json")
        result = validate_spec(raw)
        assert result.module_name == "uni_fee"
        assert len(result.models) == 3
        assert result.models[2].name == "fee.penalty"


# ---------------------------------------------------------------------------
# TestSubModels
# ---------------------------------------------------------------------------
class TestSubModels:
    """Test individual sub-model specs."""

    def test_security_acl(self):
        """SecurityACLSpec correctly parses boolean CRUD permissions."""
        acl = SecurityACLSpec(create=False, read=True, write=False, unlink=False)
        assert acl.create is False
        assert acl.read is True
        assert acl.write is False
        assert acl.unlink is False

    def test_security_acl_defaults(self):
        """SecurityACLSpec defaults all permissions to True."""
        acl = SecurityACLSpec()
        assert acl.create is True
        assert acl.read is True
        assert acl.write is True
        assert acl.unlink is True

    def test_approval_spec(self):
        """ApprovalSpec with levels validates correctly."""
        approval = ApprovalSpec(
            levels=[
                ApprovalLevelSpec(name="submitted", role="editor"),
                ApprovalLevelSpec(name="approved", role="manager"),
            ],
            on_reject="draft",
        )
        assert len(approval.levels) == 2
        assert approval.levels[0].name == "submitted"
        assert approval.levels[0].role == "editor"
        assert approval.on_reject == "draft"

    def test_cron_job_defaults(self):
        """CronJobSpec with defaults validates correctly."""
        cron = CronJobSpec(name="test_cron", method="_cron_test")
        assert cron.interval_number == 1
        assert cron.interval_type == "days"
        assert cron.model == ""

    def test_report_spec(self):
        """ReportSpec validates correctly, xml_id defaults to empty string."""
        report = ReportSpec(name="test_report")
        assert report.xml_id == ""
        assert report.report_type == "qweb-pdf"
        assert report.model == ""
        assert report.template == ""

    def test_constraint_spec(self):
        """ConstraintSpec validates with defaults."""
        constraint = ConstraintSpec(name="check_positive", type="check")
        assert constraint.expression == ""
        assert constraint.message == ""

    def test_webhook_spec(self):
        """WebhookSpec validates with defaults."""
        webhook = WebhookSpec()
        assert webhook.watched_fields == []
        assert webhook.on_create is False
        assert webhook.on_write == []
        assert webhook.on_unlink is False


# ---------------------------------------------------------------------------
# TestExportSchema (CLI integration)
# ---------------------------------------------------------------------------
class TestExportSchema:
    """Test export-schema CLI command outputs valid JSON Schema."""

    def test_export_schema_stdout(self):
        """export-schema outputs valid JSON Schema to stdout."""
        from click.testing import CliRunner
        from amil_utils.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["export-schema"])
        assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
        schema = json.loads(result.output)
        assert "properties" in schema
        assert "type" in schema
        assert schema["type"] == "object"
        assert "module_name" in schema["properties"]

    def test_export_schema_contains_defs(self):
        """export-schema JSON contains $defs with nested model schemas."""
        from click.testing import CliRunner
        from amil_utils.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["export-schema"])
        assert result.exit_code == 0
        schema = json.loads(result.output)
        assert "$defs" in schema
        # Should include sub-model definitions
        defs = schema["$defs"]
        assert "FieldSpec" in defs
        assert "ModelSpec" in defs


class TestExportSchemaFile:
    """Test export-schema --output writes JSON Schema to file."""

    def test_export_schema_to_file(self, tmp_path):
        """export-schema --output writes valid JSON Schema to specified file."""
        from click.testing import CliRunner
        from amil_utils.cli import main

        out_file = tmp_path / "schema.json"
        runner = CliRunner()
        result = runner.invoke(main, ["export-schema", "--output", str(out_file)])
        assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
        assert out_file.exists()
        schema = json.loads(out_file.read_text(encoding="utf-8"))
        assert "properties" in schema
        assert "module_name" in schema["properties"]
        assert "$defs" in schema


# ---------------------------------------------------------------------------
# TestRendererIntegration (validate_spec wired into render_module)
# ---------------------------------------------------------------------------
class TestRendererIntegration:
    """Test that validate_spec() is called during render_module()."""

    def test_invalid_spec_raises_validation_error(self):
        """render_module() raises ValidationError on invalid field type."""
        from amil_utils.renderer import get_template_dir, render_module

        invalid_spec = {
            "module_name": "test_invalid",
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "x", "type": "InvalidType"}],
                }
            ],
        }
        with pytest.raises(ValidationError):
            render_module(invalid_spec, get_template_dir(), Path("/tmp/test_out"))

    def test_valid_spec_passes_validation(self):
        """render_module() does not raise ValidationError for a valid minimal spec."""
        import tempfile

        from amil_utils.renderer import get_template_dir, render_module

        valid_spec = {
            "module_name": "test_valid",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, warnings = render_module(valid_spec, get_template_dir(), Path(d), no_context7=True)
            assert len(files) > 0


# ─── Integration schema tests (amil alignment) ──────────────────────────


class TestOdooGsdSchemaAlignment:
    """Tests for fields added to support amil spec output."""

    def test_workflow_field_accepted(self):
        """ModuleSpec accepts workflow list with transitions."""
        spec = ModuleSpec(
            module_name="test",
            workflow=[
                {
                    "model": "test.model",
                    "states": ["draft", "confirmed", "done"],
                    "transitions": [
                        {"from": "draft", "to": "confirmed", "action": "action_confirm", "conditions": ""},
                    ],
                }
            ],
        )
        assert len(spec.workflow) == 1
        assert spec.workflow[0].model == "test.model"
        assert spec.workflow[0].states == ["draft", "confirmed", "done"]
        assert len(spec.workflow[0].transitions) == 1
        assert spec.workflow[0].transitions[0].from_state == "draft"
        assert spec.workflow[0].transitions[0].to_state == "confirmed"

    def test_workflow_empty_default(self):
        """ModuleSpec defaults workflow to empty list."""
        spec = ModuleSpec(module_name="test")
        assert spec.workflow == []

    def test_business_rules_field_accepted(self):
        """ModuleSpec accepts business_rules as list of strings."""
        spec = ModuleSpec(
            module_name="test",
            business_rules=[
                "A fee structure must have at least one line",
                "Late penalty applies after grace period",
            ],
        )
        assert len(spec.business_rules) == 2
        assert "fee structure" in spec.business_rules[0]

    def test_business_rules_empty_default(self):
        """ModuleSpec defaults business_rules to empty list."""
        spec = ModuleSpec(module_name="test")
        assert spec.business_rules == []

    def test_view_hints_field_accepted(self):
        """ModuleSpec accepts view_hints list."""
        spec = ModuleSpec(
            module_name="test",
            view_hints=[
                {
                    "model": "test.model",
                    "view_type": "form",
                    "key_fields": ["name", "state"],
                    "notes": "Use notebook for details",
                }
            ],
        )
        assert len(spec.view_hints) == 1
        assert spec.view_hints[0].model == "test.model"
        assert spec.view_hints[0].key_fields == ["name", "state"]

    def test_view_hints_empty_default(self):
        """ModuleSpec defaults view_hints to empty list."""
        spec = ModuleSpec(module_name="test")
        assert spec.view_hints == []

    def test_full_gsd_spec_roundtrip(self):
        """A spec matching amil's full output is accepted and round-trips."""
        gsd_output = {
            "module_name": "uni_fee",
            "module_title": "Uni Fee",
            "odoo_version": "17.0",
            "version": "17.0.1.0.0",
            "summary": "University fee management",
            "author": "Test",
            "website": "",
            "license": "LGPL-3",
            "category": "Education",
            "application": True,
            "depends": ["base", "mail"],
            "models": [],
            "business_rules": ["Rule 1"],
            "computation_chains": [],
            "workflow": [
                {
                    "model": "uni.fee.invoice",
                    "states": ["draft", "paid"],
                    "transitions": [{"from": "draft", "to": "paid", "action": "action_pay", "conditions": ""}],
                }
            ],
            "view_hints": [
                {"model": "uni.fee.invoice", "view_type": "form", "key_fields": ["name"], "notes": ""}
            ],
            "reports": [],
            "notifications": [],
            "cron_jobs": [],
            "security": {
                "roles": ["manager", "user"],
                "acl": {
                    "manager": {"create": True, "read": True, "write": True, "unlink": True},
                    "user": {"create": True, "read": True, "write": True, "unlink": False},
                },
                "defaults": {"manager": "full", "user": "standard"},
            },
            "portal": None,
            "controllers": [],
        }
        spec = ModuleSpec(**gsd_output)
        dumped = spec.model_dump(by_alias=True)
        assert dumped["module_name"] == "uni_fee"
        assert len(dumped["workflow"]) == 1
        assert len(dumped["business_rules"]) == 1
        assert len(dumped["view_hints"]) == 1

    def test_workflow_transition_alias_serialization(self):
        """model_dump(by_alias=True) produces 'from'/'to' keys, not 'from_state'/'to_state'."""
        spec = ModuleSpec(
            module_name="test",
            workflow=[
                {
                    "model": "test.model",
                    "states": ["draft", "done"],
                    "transitions": [
                        {"from": "draft", "to": "done", "action": "act", "conditions": ""},
                    ],
                }
            ],
        )
        dumped = spec.model_dump(by_alias=True)
        transition = dumped["workflow"][0]["transitions"][0]
        assert "from" in transition, "by_alias=True should produce 'from' key"
        assert "to" in transition, "by_alias=True should produce 'to' key"
        assert transition["from"] == "draft"
        assert transition["to"] == "done"
        # Verify Python field names are NOT present when by_alias=True
        assert "from_state" not in transition
        assert "to_state" not in transition

    def test_workflow_transition_python_name_construction(self):
        """populate_by_name=True allows constructing via Python attribute names."""
        from amil_utils.spec_schema import WorkflowTransitionSpec

        t = WorkflowTransitionSpec(from_state="draft", to_state="confirmed", action="act")
        assert t.from_state == "draft"
        assert t.to_state == "confirmed"

    def test_workflow_transition_default_serialization(self):
        """model_dump() without by_alias uses Python field names."""
        spec = ModuleSpec(
            module_name="test",
            workflow=[
                {
                    "model": "test.model",
                    "states": ["draft", "done"],
                    "transitions": [
                        {"from": "draft", "to": "done", "action": "act", "conditions": ""},
                    ],
                }
            ],
        )
        dumped = spec.model_dump()
        transition = dumped["workflow"][0]["transitions"][0]
        assert "from_state" in transition, "default dump should use Python field names"
        assert "to_state" in transition


# ---------------------------------------------------------------------------
# TestExpectedExamples
# ---------------------------------------------------------------------------
class TestExpectedExamples:
    """Tests for expected_examples field on ModelSpec."""

    def test_model_spec_accepts_expected_examples(self):
        """ModelSpec should accept expected_examples field."""
        model = ModelSpec(
            name="sale.custom",
            expected_examples=[
                {"create": {"quantity": 5, "unit_price": 10.0}, "expect": {"total_amount": 50.0}},
            ],
        )
        assert len(model.expected_examples) == 1
        assert model.expected_examples[0]["expect"]["total_amount"] == 50.0

    def test_model_spec_expected_examples_defaults_empty(self):
        """expected_examples should default to empty list."""
        model = ModelSpec(name="test.model")
        assert model.expected_examples == []

    def test_model_spec_multiple_examples(self):
        """ModelSpec should accept multiple expected_examples entries."""
        model = ModelSpec(
            name="sale.custom",
            expected_examples=[
                {"create": {"quantity": 5, "unit_price": 10.0}, "expect": {"total_amount": 50.0}},
                {"create": {"quantity": 0, "unit_price": 10.0}, "expect": {"total_amount": 0.0}},
                {"create": {"quantity": 3, "unit_price": 7.5}, "expect": {"total_amount": 22.5}},
            ],
        )
        assert len(model.expected_examples) == 3
        assert model.expected_examples[2]["expect"]["total_amount"] == 22.5

    def test_model_spec_expected_examples_roundtrip(self):
        """expected_examples should survive model_dump() roundtrip."""
        examples = [
            {"create": {"name": "Test"}, "expect": {"display_name": "Test"}},
        ]
        model = ModelSpec(name="test.model", expected_examples=examples)
        dumped = model.model_dump()
        assert dumped["expected_examples"] == examples

    def test_model_spec_expected_examples_in_module_spec(self):
        """expected_examples on a model within ModuleSpec validates correctly."""
        spec = ModuleSpec(
            module_name="test_mod",
            models=[
                {
                    "name": "test.order",
                    "fields": [{"name": "name", "type": "Char"}],
                    "expected_examples": [
                        {"create": {"name": "Order1"}, "expect": {"state": "draft"}},
                    ],
                },
            ],
        )
        assert len(spec.models[0].expected_examples) == 1
        assert spec.models[0].expected_examples[0]["create"]["name"] == "Order1"


# ---------------------------------------------------------------------------
# Multi-company support
# ---------------------------------------------------------------------------


class TestMultiCompany:
    """Tests for the multi_company field on ModuleSpec."""

    def test_module_spec_accepts_multi_company(self):
        """multi_company=True is accepted by ModuleSpec."""
        spec = ModuleSpec(module_name="test", multi_company=True)
        assert spec.multi_company is True

    def test_module_spec_multi_company_defaults_false(self):
        """multi_company defaults to False when not specified."""
        spec = ModuleSpec(module_name="test")
        assert spec.multi_company is False

    def test_multi_company_round_trips_through_validate_spec(self):
        """validate_spec preserves multi_company=True."""
        raw = {"module_name": "test_mc", "multi_company": True}
        result = validate_spec(raw)
        assert result.multi_company is True

    def test_multi_company_false_round_trips(self):
        """validate_spec preserves multi_company=False."""
        raw = {"module_name": "test_mc", "multi_company": False}
        result = validate_spec(raw)
        assert result.multi_company is False
