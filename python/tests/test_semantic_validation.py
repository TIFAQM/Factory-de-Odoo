"""Tests for semantic validation module (E1-E6 errors, W1-W4 warnings)."""

from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest

from amil_utils.validation.semantic import (
    SemanticValidationResult,
    ValidationIssue,
    print_validation_report,
    semantic_validate,
    semantic_validate_full,
    semantic_validate_patterns,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _make_valid_module(root: Path, module_name: str = "test_module") -> Path:
    """Scaffold a minimal valid module at *root/module_name*."""
    mod = root / module_name
    _write(mod / "__manifest__.py", """\
        {
            'name': 'Test Module',
            'version': '17.0.1.0.0',
            'depends': ['base'],
            'data': [
                'security/ir.model.access.csv',
                'views/partner_views.xml',
            ],
        }
    """)
    _write(mod / "__init__.py", "from . import models\n")
    _write(mod / "models" / "__init__.py", "from . import partner\n")
    _write(mod / "models" / "partner.py", """\
        from odoo import api, fields, models

        class ResPartnerExt(models.Model):
            _name = 'res.partner.ext'
            _description = 'Partner Extension'

            name = fields.Char(string='Name')
            code = fields.Char(string='Code')
            amount = fields.Float(string='Amount')
    """)
    _write(mod / "security" / "ir.model.access.csv", """\
        id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
        access_partner_ext,partner.ext,model_res_partner_ext,base.group_user,1,1,1,1
    """)
    _write(mod / "views" / "partner_views.xml", """\
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="view_partner_ext_form" model="ir.ui.view">
                <field name="name">partner.ext.form</field>
                <field name="model">res.partner.ext</field>
                <field name="arch" type="xml">
                    <form>
                        <field name="name"/>
                        <field name="code"/>
                        <field name="amount"/>
                    </form>
                </field>
            </record>
        </odoo>
    """)
    return mod


# ===========================================================================
# E1: Python Syntax
# ===========================================================================


class TestE1PythonSyntax:
    """E1: ast.parse() catches Python syntax errors."""

    def test_valid_python_no_issues(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        e1 = [i for i in result.errors if i.code == "E1"]
        assert e1 == []

    def test_syntax_error_reported(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "broken.py", "def foo(\n")
        result = semantic_validate(mod)
        e1 = [i for i in result.errors if i.code == "E1"]
        assert len(e1) == 1
        assert "broken.py" in e1[0].file
        assert e1[0].severity == "error"


# ===========================================================================
# E2: XML Well-Formedness
# ===========================================================================


class TestE2XmlWellFormedness:
    """E2: xml.etree catches malformed XML."""

    def test_valid_xml_no_issues(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        e2 = [i for i in result.errors if i.code == "E2"]
        assert e2 == []

    def test_malformed_xml_reported(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "views" / "bad.xml", "<odoo><record></odoo>")
        result = semantic_validate(mod)
        e2 = [i for i in result.errors if i.code == "E2"]
        assert len(e2) == 1
        assert "bad.xml" in e2[0].file
        assert e2[0].severity == "error"


# ===========================================================================
# E3: View Field References
# ===========================================================================


class TestE3FieldReferences:
    """E3: view field refs that don't exist on model are errors."""

    def test_valid_field_refs_no_issues(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        e3 = [i for i in result.errors if i.code == "E3"]
        assert e3 == []

    def test_missing_field_ref_error_with_suggestion(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        # Reference 'amont' which doesn't exist but is close to 'amount'
        _write(mod / "views" / "partner_views.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_partner_ext_form" model="ir.ui.view">
                    <field name="name">partner.ext.form</field>
                    <field name="model">res.partner.ext</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="amont"/>
                        </form>
                    </field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        e3 = [i for i in result.errors if i.code == "E3"]
        assert len(e3) == 1
        assert "amont" in e3[0].message
        assert e3[0].suggestion is not None
        assert "amount" in e3[0].suggestion

    def test_inherited_fields_recognized(self, tmp_path: Path) -> None:
        """Fields from _inherit (e.g., mail.thread -> message_ids) are valid."""
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "partner.py", """\
            from odoo import fields, models

            class ResPartnerExt(models.Model):
                _name = 'res.partner.ext'
                _inherit = ['mail.thread']
                _description = 'Partner Extension'

                name = fields.Char(string='Name')
        """)
        _write(mod / "views" / "partner_views.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_partner_ext_form" model="ir.ui.view">
                    <field name="name">partner.ext.form</field>
                    <field name="model">res.partner.ext</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="name"/>
                            <field name="message_ids"/>
                        </form>
                    </field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        e3 = [i for i in result.errors if i.code == "E3"]
        assert e3 == []

    def test_view_metadata_fields_not_checked(self, tmp_path: Path) -> None:
        """Top-level view fields (name, model, arch, priority) are NOT model fields."""
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        # 'name', 'model', 'arch' appear as <field name="..."> but outside <form>
        e3 = [i for i in result.errors if i.code == "E3"]
        assert e3 == []


# ===========================================================================
# E4: ACL References
# ===========================================================================


class TestE4AclReferences:
    """E4: ACL CSV entries referencing non-existent model XML IDs."""

    def test_valid_acl_no_issues(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        e4 = [i for i in result.errors if i.code == "E4"]
        assert e4 == []

    def test_unknown_model_ref_error(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "security" / "ir.model.access.csv", """\
            id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
            access_bad,bad.access,model_nonexistent_model,base.group_user,1,1,1,1
        """)
        result = semantic_validate(mod)
        e4 = [i for i in result.errors if i.code == "E4"]
        assert len(e4) == 1
        assert "nonexistent" in e4[0].message.lower() or "model_nonexistent_model" in e4[0].message

    def test_module_prefixed_group_handled(self, tmp_path: Path) -> None:
        """group_id:id with module prefix (e.g., base.group_user) works."""
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        e4 = [i for i in result.errors if i.code == "E4"]
        assert e4 == []


# ===========================================================================
# E5: XML ID Uniqueness
# ===========================================================================


class TestE5XmlIdUniqueness:
    """E5: Duplicate XML IDs across data files."""

    def test_unique_ids_no_issues(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        e5 = [i for i in result.errors if i.code == "E5"]
        assert e5 == []

    def test_duplicate_id_error(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "views" / "extra_views.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_partner_ext_form" model="ir.ui.view">
                    <field name="name">duplicate</field>
                    <field name="model">res.partner.ext</field>
                    <field name="arch" type="xml">
                        <form><field name="name"/></form>
                    </field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        e5 = [i for i in result.errors if i.code == "E5"]
        assert len(e5) >= 1
        assert "view_partner_ext_form" in e5[0].message


# ===========================================================================
# E6: Manifest Depends
# ===========================================================================


class TestE6ManifestDepends:
    """E6: Missing manifest depends for cross-module imports."""

    def test_complete_depends_no_issues(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        e6 = [i for i in result.errors if i.code == "E6"]
        assert e6 == []

    def test_missing_import_dep_error(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "partner.py", """\
            from odoo import fields, models
            from odoo.addons.sale import something

            class ResPartnerExt(models.Model):
                _name = 'res.partner.ext'
                _description = 'Partner Extension'
                name = fields.Char()
        """)
        result = semantic_validate(mod)
        e6 = [i for i in result.errors if i.code == "E6"]
        assert len(e6) == 1
        assert "sale" in e6[0].message

    def test_xml_ref_dep_checked(self, tmp_path: Path) -> None:
        """XML ref="" attributes referencing external modules need depends."""
        mod = _make_valid_module(tmp_path)
        _write(mod / "views" / "partner_views.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_partner_ext_form" model="ir.ui.view">
                    <field name="name">partner.ext.form</field>
                    <field name="model">res.partner.ext</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="name"/>
                        </form>
                    </field>
                    <field name="inherit_id" ref="sale.view_order_form"/>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        e6 = [i for i in result.errors if i.code == "E6"]
        assert len(e6) == 1
        assert "sale" in e6[0].message


# ===========================================================================
# W1: Comodel References
# ===========================================================================


class TestW1Comodel:
    """W1: comodel_name checked against registry and known models."""

    def test_known_comodel_no_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "partner.py", """\
            from odoo import fields, models

            class ResPartnerExt(models.Model):
                _name = 'res.partner.ext'
                _description = 'Partner Extension'
                partner_id = fields.Many2one('res.partner')
        """)
        result = semantic_validate(mod)
        w1 = [i for i in result.warnings if i.code == "W1"]
        assert w1 == []

    def test_unknown_comodel_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "partner.py", """\
            from odoo import fields, models

            class ResPartnerExt(models.Model):
                _name = 'res.partner.ext'
                _description = 'Partner Extension'
                weird_id = fields.Many2one('completely.unknown.model')
        """)
        result = semantic_validate(mod)
        w1 = [i for i in result.warnings if i.code == "W1"]
        assert len(w1) == 1
        assert "completely.unknown.model" in w1[0].message


# ===========================================================================
# W2: Computed Field Depends
# ===========================================================================


class TestW2ComputedDepends:
    """W2: @api.depends references validated as warnings."""

    def test_valid_depends_no_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "partner.py", """\
            from odoo import api, fields, models

            class ResPartnerExt(models.Model):
                _name = 'res.partner.ext'
                _description = 'Partner Extension'
                name = fields.Char()
                upper_name = fields.Char(compute='_compute_upper')

                @api.depends('name')
                def _compute_upper(self):
                    for rec in self:
                        rec.upper_name = rec.name.upper() if rec.name else ''
        """)
        result = semantic_validate(mod)
        w2 = [i for i in result.warnings if i.code == "W2"]
        assert w2 == []

    def test_invalid_depends_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "partner.py", """\
            from odoo import api, fields, models

            class ResPartnerExt(models.Model):
                _name = 'res.partner.ext'
                _description = 'Partner Extension'
                name = fields.Char()
                upper_name = fields.Char(compute='_compute_upper')

                @api.depends('nonexistent_field')
                def _compute_upper(self):
                    pass
        """)
        result = semantic_validate(mod)
        w2 = [i for i in result.warnings if i.code == "W2"]
        assert len(w2) == 1
        assert "nonexistent_field" in w2[0].message

    def test_dot_notation_only_first_segment(self, tmp_path: Path) -> None:
        """Dot-notation depends ('partner_id.name') validates first segment only."""
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "partner.py", """\
            from odoo import api, fields, models

            class ResPartnerExt(models.Model):
                _name = 'res.partner.ext'
                _description = 'Partner Extension'
                partner_id = fields.Many2one('res.partner')
                display = fields.Char(compute='_compute_display')

                @api.depends('partner_id.name')
                def _compute_display(self):
                    pass
        """)
        result = semantic_validate(mod)
        w2 = [i for i in result.warnings if i.code == "W2"]
        assert w2 == []


# ===========================================================================
# W3: Security Group References
# ===========================================================================


class TestW3GroupRefs:
    """W3: groups= in XML views validated as warnings."""

    def test_known_group_no_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "views" / "partner_views.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_partner_ext_form" model="ir.ui.view">
                    <field name="name">partner.ext.form</field>
                    <field name="model">res.partner.ext</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="name" groups="base.group_user"/>
                        </form>
                    </field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        w3 = [i for i in result.warnings if i.code == "W3"]
        assert w3 == []

    def test_unknown_group_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "views" / "partner_views.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_partner_ext_form" model="ir.ui.view">
                    <field name="name">partner.ext.form</field>
                    <field name="model">res.partner.ext</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="name" groups="fake_module.nonexistent_group"/>
                        </form>
                    </field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        w3 = [i for i in result.warnings if i.code == "W3"]
        assert len(w3) == 1
        assert "fake_module.nonexistent_group" in w3[0].message


# ===========================================================================
# W4: Record Rule Domain Field References
# ===========================================================================


class TestW4RuleDomain:
    """W4: ir.rule domain_force field references validated."""

    def test_valid_domain_no_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "security" / "rules.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="rule_partner_ext" model="ir.rule">
                    <field name="name">Partner Ext Rule</field>
                    <field name="model_id" ref="model_res_partner_ext"/>
                    <field name="domain_force">[('name', '!=', False)]</field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        w4 = [i for i in result.warnings if i.code == "W4"]
        assert w4 == []

    def test_invalid_domain_field_warning(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "security" / "rules.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="rule_partner_ext" model="ir.rule">
                    <field name="name">Partner Ext Rule</field>
                    <field name="model_id" ref="model_res_partner_ext"/>
                    <field name="domain_force">[('nonexistent_field', '=', True)]</field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        w4 = [i for i in result.warnings if i.code == "W4"]
        assert len(w4) == 1
        assert "nonexistent_field" in w4[0].message


# ===========================================================================
# Short-Circuit
# ===========================================================================


class TestShortCircuit:
    """Short-circuit skips cross-ref checks when E1/E2 fails."""

    def test_e1_failure_skips_field_checks(self, tmp_path: Path) -> None:
        """E1 failure on a .py file skips E3/W1/W2 for that file."""
        mod = _make_valid_module(tmp_path)
        # Break the only model file - should not get E3 errors for missing fields
        _write(mod / "models" / "partner.py", "def broken(\n")
        result = semantic_validate(mod)
        e1 = [i for i in result.errors if i.code == "E1"]
        assert len(e1) >= 1
        # Should NOT have E3 errors since models couldn't be parsed
        e3 = [i for i in result.errors if i.code == "E3"]
        assert e3 == []

    def test_e2_failure_skips_xml_checks(self, tmp_path: Path) -> None:
        """E2 failure on .xml file skips E3/E5/W3/W4 for that file."""
        mod = _make_valid_module(tmp_path)
        _write(mod / "views" / "partner_views.xml", "<odoo><broken></odoo>")
        result = semantic_validate(mod)
        e2 = [i for i in result.errors if i.code == "E2"]
        assert len(e2) >= 1
        # Should NOT have E5 errors for this file
        e5 = [i for i in result.errors if i.code == "E5"]
        # Any E5 should not reference the broken file
        for issue in e5:
            assert "partner_views.xml" not in issue.file


# ===========================================================================
# Performance
# ===========================================================================


class TestPerformance:
    """Performance: 10-model module validates under 2 seconds."""

    def test_validation_under_2_seconds(self, tmp_path: Path) -> None:
        mod = tmp_path / "perf_module"
        models_code = ["from odoo import fields, models\n"]
        for i in range(10):
            models_code.append(f"""
class Model{i}(models.Model):
    _name = 'perf.model.{i}'
    _description = 'Perf Model {i}'
    name = fields.Char()
    value = fields.Integer()
    active = fields.Boolean(default=True)
""")
        _write(mod / "__manifest__.py", """\
            {
                'name': 'Perf Module',
                'version': '17.0.1.0.0',
                'depends': ['base'],
                'data': [],
            }
        """)
        _write(mod / "__init__.py", "from . import models\n")
        _write(mod / "models" / "__init__.py", "from . import perf\n")
        _write(mod / "models" / "perf.py", "\n".join(models_code))

        start = time.perf_counter()
        result = semantic_validate(mod)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"Validation took {elapsed:.2f}s (budget: 2s)"
        assert result.duration_ms < 2000


# ===========================================================================
# Result structure
# ===========================================================================


class TestResultStructure:
    """Verify result dataclass properties."""

    def test_has_errors_property(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        assert result.has_errors is False

    def test_has_errors_true_on_error(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        _write(mod / "models" / "broken.py", "def x(\n")
        result = semantic_validate(mod)
        assert result.has_errors is True

    def test_duration_ms_set(self, tmp_path: Path) -> None:
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        assert result.duration_ms >= 0

    def test_print_validation_report(self, tmp_path: Path, caplog) -> None:
        import logging
        mod = _make_valid_module(tmp_path)
        result = semantic_validate(mod)
        with caplog.at_level(logging.INFO):
            print_validation_report(result)
        assert any("Semantic Validation" in r.message for r in caplog.records)


# ===========================================================================
# E2E: CLI Integration
# ===========================================================================


class TestE2ECliIntegration:
    """E2E tests for CLI render-module + semantic validation pipeline."""

    def test_full_module_validation(self, tmp_path: Path) -> None:
        """Render a valid module scaffold and validate it -- zero errors expected."""
        mod = _make_valid_module(tmp_path, "my_module")
        result = semantic_validate(mod)
        assert result.has_errors is False, (
            f"Valid module produced errors: {[e.message for e in result.errors]}"
        )

    def test_cli_skip_validation_flag_exists(self) -> None:
        """--skip-validation flag is accepted by the render-module CLI command."""
        from click.testing import CliRunner

        from amil_utils.cli import main

        runner = CliRunner()
        # Invoke with --help to verify the flag is listed (no spec needed)
        result = runner.invoke(main, ["render-module", "--help"])
        assert result.exit_code == 0
        assert "--skip-validation" in result.output

    def test_validation_gates_registry(self, tmp_path: Path) -> None:
        """Semantic errors block registry update: introduce bad field, confirm has_errors."""
        mod = _make_valid_module(tmp_path)
        # Introduce a deliberate error: reference non-existent field in view
        _write(mod / "views" / "partner_views.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_partner_ext_form" model="ir.ui.view">
                    <field name="name">partner.ext.form</field>
                    <field name="model">res.partner.ext</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="nonexistent_field_xyz"/>
                        </form>
                    </field>
                </record>
            </odoo>
        """)
        result = semantic_validate(mod)
        assert result.has_errors is True
        e3 = [i for i in result.errors if i.code == "E3"]
        assert len(e3) >= 1
        assert "nonexistent_field_xyz" in e3[0].message


# ===========================================================================
# Helpers for E7-E12 tests
# ===========================================================================


def _make_module_with_model(root: Path, model_source: str) -> Path:
    """Scaffold a minimal module with a single model file from source string."""
    mod = root / "test_module"
    _write(mod / "__manifest__.py", """\
        {
            'name': 'Test Module',
            'version': '17.0.1.0.0',
            'depends': ['base'],
            'data': [],
        }
    """)
    _write(mod / "__init__.py", "from . import models\n")
    _write(mod / "models" / "__init__.py", "from . import main\n")
    (mod / "models" / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (mod / "models" / "main.py").write_text(model_source, encoding="utf-8")
    return mod


# ===========================================================================
# E7: Missing Self Iteration
# ===========================================================================


class TestE7MissingSelfIteration:
    """E7: @api.depends/@api.constrains methods must iterate over self."""

    def test_bad_fill_triggers_e7(self, tmp_path: Path) -> None:
        """Assigning to self.field without for-loop triggers E7."""
        from tests.fixtures.logic_writer.e7_missing_self_iteration import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e7 = [i for i in result.errors if i.code == "E7"]
        assert len(e7) == 1
        assert "_compute_total" in e7[0].message
        assert e7[0].severity == "error"

    def test_good_fill_clean(self, tmp_path: Path) -> None:
        """Correct compute with for-loop passes E7."""
        from tests.fixtures.logic_writer.good_compute import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e7 = [i for i in result.errors if i.code == "E7"]
        assert e7 == []

    def test_api_model_exempt(self, tmp_path: Path) -> None:
        """@api.model methods are exempt from E7 (don't operate on recordsets)."""
        source = """\
from odoo import api, fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    name = fields.Char()

    @api.model
    def create(self, vals):
        self.name = vals.get('name', '')
        return super().create(vals)
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e7 = [i for i in result.errors if i.code == "E7"]
        assert e7 == []

    def test_no_decorator_not_checked(self, tmp_path: Path) -> None:
        """Methods without @api.depends/@api.constrains are not checked."""
        source = """\
from odoo import fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    name = fields.Char()

    def do_something(self):
        self.name = "test"
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e7 = [i for i in result.errors if i.code == "E7"]
        assert e7 == []

    def test_constrains_also_checked(self, tmp_path: Path) -> None:
        """@api.constrains methods also trigger E7 if missing self iteration."""
        source = """\
from odoo import api, fields, models
from odoo.exceptions import ValidationError

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    amount = fields.Float()

    @api.constrains("amount")
    def _check_amount(self):
        if self.amount < 0:
            raise ValidationError("Negative!")
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e7 = [i for i in result.errors if i.code == "E7"]
        assert len(e7) == 1


# ===========================================================================
# E8: Compute Doesn't Set Target Field
# ===========================================================================


class TestE8NoTargetSet:
    """E8: @api.depends methods must assign to their target fields."""

    def test_bad_fill_triggers_e8(self, tmp_path: Path) -> None:
        """Compute that never assigns to target field triggers E8."""
        from tests.fixtures.logic_writer.e8_no_target_set import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e8 = [i for i in result.errors if i.code == "E8"]
        assert len(e8) == 1
        assert "total" in e8[0].message.lower()

    def test_good_fill_clean(self, tmp_path: Path) -> None:
        """Correct compute assigning to target field passes E8."""
        from tests.fixtures.logic_writer.good_compute import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e8 = [i for i in result.errors if i.code == "E8"]
        assert e8 == []

    def test_sidecar_read(self, tmp_path: Path) -> None:
        """E8 reads .amil-stubs.json sidecar for target_fields."""
        source = """\
from odoo import api, fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    total = fields.Float()
    tax = fields.Float()

    @api.depends("total")
    def _compute_amounts(self):
        for rec in self:
            rec.total = 0.0
"""
        import json

        mod = _make_module_with_model(tmp_path, source)
        # Sidecar says target fields are total AND tax
        sidecar = {
            "stubs": [
                {
                    "method": "_compute_amounts",
                    "target_fields": ["total", "tax"],
                }
            ]
        }
        (mod / ".amil-stubs.json").write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
        result = semantic_validate(mod)
        e8 = [i for i in result.errors if i.code == "E8"]
        # Should flag because `tax` is never set
        assert len(e8) == 1
        assert "tax" in e8[0].message.lower()

    def test_name_inference_fallback(self, tmp_path: Path) -> None:
        """E8 infers target from _compute_X -> field X when no sidecar."""
        source = """\
from odoo import api, fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    total = fields.Float()

    @api.depends("total")
    def _compute_total(self):
        for rec in self:
            pass  # never assigns to rec.total
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e8 = [i for i in result.errors if i.code == "E8"]
        assert len(e8) == 1
        assert "total" in e8[0].message.lower()


# ===========================================================================
# E9: Constraint Doesn't Raise ValidationError
# ===========================================================================


class TestE9NoValidationError:
    """E9: @api.constrains methods must raise ValidationError."""

    def test_bad_fill_triggers_e9(self, tmp_path: Path) -> None:
        """Constraint that never raises ValidationError triggers E9."""
        from tests.fixtures.logic_writer.e9_no_validation_error import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e9 = [i for i in result.errors if i.code == "E9"]
        assert len(e9) == 1
        assert "_check_amount" in e9[0].message

    def test_good_fill_clean(self, tmp_path: Path) -> None:
        """Correct constraint with raise ValidationError passes E9."""
        from tests.fixtures.logic_writer.good_constraint import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e9 = [i for i in result.errors if i.code == "E9"]
        assert e9 == []

    def test_qualified_validation_error_accepted(self, tmp_path: Path) -> None:
        """Both bare and qualified exceptions.ValidationError accepted."""
        source = """\
from odoo import api, fields, models
from odoo import exceptions

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    amount = fields.Float()

    @api.constrains("amount")
    def _check_amount(self):
        for rec in self:
            if rec.amount < 0:
                raise exceptions.ValidationError("Negative!")
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e9 = [i for i in result.errors if i.code == "E9"]
        assert e9 == []


# ===========================================================================
# E10: Bare Field Access Without Record Variable
# ===========================================================================


class TestE10BareFieldAccess:
    """E10: Bare field names in Load context inside for-loop are errors."""

    def test_bad_fill_triggers_e10(self, tmp_path: Path) -> None:
        """Bare field name in for-loop body triggers E10."""
        from tests.fixtures.logic_writer.e10_bare_field_access import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e10 = [i for i in result.errors if i.code == "E10"]
        assert len(e10) >= 1
        # Should flag 'amount' or 'tax_amount'
        messages = " ".join(i.message for i in e10)
        assert "amount" in messages.lower()

    def test_good_fill_clean(self, tmp_path: Path) -> None:
        """Correct field access via record.field passes E10."""
        from tests.fixtures.logic_writer.good_compute import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e10 = [i for i in result.errors if i.code == "E10"]
        assert e10 == []

    def test_local_vars_not_flagged(self, tmp_path: Path) -> None:
        """Local variables on left side of assignment (Store) not flagged."""
        source = """\
from odoo import api, fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    amount = fields.Float()
    total = fields.Float(compute="_compute_total")

    @api.depends("amount")
    def _compute_total(self):
        for rec in self:
            subtotal = rec.amount * 2
            rec.total = subtotal
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e10 = [i for i in result.errors if i.code == "E10"]
        assert e10 == []

    def test_only_known_fields_flagged(self, tmp_path: Path) -> None:
        """Only bare names matching model fields are flagged, not random variables."""
        source = """\
from odoo import api, fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    amount = fields.Float()
    total = fields.Float(compute="_compute_total")

    @api.depends("amount")
    def _compute_total(self):
        for rec in self:
            multiplier = 2
            rec.total = rec.amount * multiplier
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e10 = [i for i in result.errors if i.code == "E10"]
        assert e10 == []


# ===========================================================================
# E11: Wrong mapped/filtered Syntax
# ===========================================================================


class TestE11WrongMappedFiltered:
    """E11: mapped() and filtered() syntax validation."""

    def test_bad_fill_triggers_e11(self, tmp_path: Path) -> None:
        """mapped(bare_name) and filtered('comparison') trigger E11."""
        from tests.fixtures.logic_writer.e11_wrong_mapped_filtered import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e11 = [i for i in result.errors if i.code == "E11"]
        assert len(e11) >= 1

    def test_good_fill_clean(self, tmp_path: Path) -> None:
        """Correct mapped('field') passes E11."""
        from tests.fixtures.logic_writer.good_compute import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e11 = [i for i in result.errors if i.code == "E11"]
        assert e11 == []

    def test_mapped_string_ok(self, tmp_path: Path) -> None:
        """mapped('field_name') is correct syntax, no error."""
        source = """\
from odoo import api, fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    total = fields.Float(compute="_compute_total")
    line_ids = fields.One2many("fee.invoice.line", "invoice_id")

    @api.depends("line_ids.amount")
    def _compute_total(self):
        for rec in self:
            rec.total = sum(rec.line_ids.mapped('amount'))
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e11 = [i for i in result.errors if i.code == "E11"]
        assert e11 == []

    def test_filtered_lambda_ok(self, tmp_path: Path) -> None:
        """filtered(lambda r: ...) is correct syntax, no error."""
        source = """\
from odoo import api, fields, models
from odoo.exceptions import ValidationError

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    state = fields.Selection([("draft", "Draft"), ("done", "Done")])
    line_ids = fields.One2many("fee.invoice.line", "invoice_id")

    @api.constrains("state")
    def _check_state(self):
        for rec in self:
            done_lines = rec.line_ids.filtered(lambda r: r.state == 'done')
            if not done_lines:
                raise ValidationError("No done lines!")
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e11 = [i for i in result.errors if i.code == "E11"]
        assert e11 == []


# ===========================================================================
# E12: write()/create()/unlink() in Compute
# ===========================================================================


class TestE12WriteInCompute:
    """E12: self.write/create/unlink inside @api.depends methods."""

    def test_bad_fill_triggers_e12(self, tmp_path: Path) -> None:
        """self.write() inside compute triggers E12."""
        from tests.fixtures.logic_writer.e12_write_in_compute import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e12 = [i for i in result.errors if i.code == "E12"]
        assert len(e12) == 1
        assert "write" in e12[0].message.lower()

    def test_good_fill_clean(self, tmp_path: Path) -> None:
        """Correct compute without write/create/unlink passes E12."""
        from tests.fixtures.logic_writer.good_compute import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e12 = [i for i in result.errors if i.code == "E12"]
        assert e12 == []

    def test_env_create_not_flagged(self, tmp_path: Path) -> None:
        """self.env['other.model'].create() is NOT flagged (different receiver)."""
        source = """\
from odoo import api, fields, models

class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    total = fields.Float(compute="_compute_total")
    line_ids = fields.One2many("fee.invoice.line", "invoice_id")

    @api.depends("line_ids.amount")
    def _compute_total(self):
        for rec in self:
            rec.total = sum(rec.line_ids.mapped('amount'))
            self.env['audit.log'].create({'message': 'computed'})
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e12 = [i for i in result.errors if i.code == "E12"]
        assert e12 == []


# ===========================================================================
# Clean Fills: Good Compute + Good Constraint pass all E7-E12
# ===========================================================================


class TestCleanFills:
    """Good compute and constraint fixtures pass all E7-E12 checks."""

    def test_good_compute_passes_all(self, tmp_path: Path) -> None:
        """Good compute fixture passes all E7-E12 with zero errors."""
        from tests.fixtures.logic_writer.good_compute import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        new_errors = [i for i in result.errors if i.code in ("E7", "E8", "E9", "E10", "E11", "E12")]
        assert new_errors == [], f"Good compute produced errors: {[e.message for e in new_errors]}"

    def test_good_constraint_passes_all(self, tmp_path: Path) -> None:
        """Good constraint fixture passes all E7-E12 with zero errors."""
        from tests.fixtures.logic_writer.good_constraint import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        new_errors = [i for i in result.errors if i.code in ("E7", "E8", "E9", "E10", "E11", "E12")]
        assert new_errors == [], f"Good constraint produced errors: {[e.message for e in new_errors]}"


# ===========================================================================
# E13: Override Method Missing super() Call
# ===========================================================================


class TestE13MissingSuperCall:
    """E13: create/write overrides must call super()."""

    def test_create_without_super_triggers_e13(self, tmp_path: Path) -> None:
        """create() method without super() call triggers E13."""
        from tests.fixtures.logic_writer.e13_no_super_call import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e13 = [i for i in result.errors if i.code == "E13"]
        assert len(e13) >= 1
        create_issues = [i for i in e13 if "create" in i.message]
        assert len(create_issues) >= 1
        assert e13[0].severity == "error"

    def test_write_without_super_triggers_e13(self, tmp_path: Path) -> None:
        """write() method without super() call triggers E13."""
        from tests.fixtures.logic_writer.e13_no_super_call import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e13 = [i for i in result.errors if i.code == "E13"]
        write_issues = [i for i in e13 if "write" in i.message]
        assert len(write_issues) >= 1

    def test_create_with_super_clean(self, tmp_path: Path) -> None:
        """create() with super().create(vals_list) does NOT fire E13."""
        from tests.fixtures.logic_writer.good_override import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e13 = [i for i in result.errors if i.code == "E13"]
        assert e13 == []

    def test_old_style_super_clean(self, tmp_path: Path) -> None:
        """create() with super(ClassName, self).create() does NOT fire E13."""
        source = """\
from odoo import api, fields, models


class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    name = fields.Char()

    def create(self, vals_list):
        return super(FeeInvoice, self).create(vals_list)
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e13 = [i for i in result.errors if i.code == "E13"]
        assert e13 == []

    def test_model_create_multi_with_super_clean(self, tmp_path: Path) -> None:
        """create() decorated with @api.model_create_multi that calls super() is clean."""
        source = """\
from odoo import api, fields, models


class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    name = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e13 = [i for i in result.errors if i.code == "E13"]
        assert e13 == []

    def test_only_checks_classes_with_name(self, tmp_path: Path) -> None:
        """E13 only checks methods in classes with _name or _inherit."""
        source = """\
from odoo import fields, models


class HelperMixin:
    # No _name or _inherit -- should NOT be checked
    def create(self, vals):
        return True
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        e13 = [i for i in result.errors if i.code == "E13"]
        assert e13 == []


# ===========================================================================
# W5: Action Method Modifies State Without Checking
# ===========================================================================


class TestW5NoStateCheck:
    """W5: action_* methods that modify state should check current state first."""

    def test_action_without_state_check_triggers_w5(self, tmp_path: Path) -> None:
        """action_submit() assigning state without if-check triggers W5."""
        from tests.fixtures.logic_writer.w5_no_state_check import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        w5 = [i for i in result.warnings if i.code == "W5"]
        assert len(w5) == 1
        assert "action_submit" in w5[0].message
        assert w5[0].severity == "warning"

    def test_action_with_state_check_clean(self, tmp_path: Path) -> None:
        """action_submit() with if self.state check does NOT fire W5."""
        from tests.fixtures.logic_writer.good_action import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        w5 = [i for i in result.warnings if i.code == "W5"]
        assert w5 == []

    def test_action_with_filtered_lambda_clean(self, tmp_path: Path) -> None:
        """action using self.filtered(lambda r: r.state ...) does NOT fire W5."""
        source = """\
from odoo import api, fields, models


class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    state = fields.Selection([('draft', 'Draft'), ('done', 'Done')])

    def action_submit(self):
        records = self.filtered(lambda r: r.state == 'draft')
        for rec in records:
            rec.state = 'submitted'
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        w5 = [i for i in result.warnings if i.code == "W5"]
        assert w5 == []

    def test_action_no_state_assignment_clean(self, tmp_path: Path) -> None:
        """action method that doesn't assign to state at all is clean."""
        source = """\
from odoo import api, fields, models


class FeeInvoice(models.Model):
    _name = "fee.invoice"
    _description = "Fee Invoice"

    name = fields.Char()
    state = fields.Selection([('draft', 'Draft'), ('done', 'Done')])

    def action_print(self):
        return self.env.ref('module.report').report_action(self)
"""
        mod = _make_module_with_model(tmp_path, source)
        result = semantic_validate(mod)
        w5 = [i for i in result.warnings if i.code == "W5"]
        assert w5 == []


# ===========================================================================
# E15: Cron Method Missing @api.model
# ===========================================================================


class TestE15CronMissingApiModel:
    """E15: _cron_* methods must have @api.model decorator."""

    def test_cron_without_api_model_triggers_e15(self, tmp_path: Path) -> None:
        """_cron_send_reminders() without @api.model triggers E15."""
        from tests.fixtures.logic_writer.e15_cron_no_api_model import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e15 = [i for i in result.errors if i.code == "E15"]
        assert len(e15) == 1
        assert "_cron_send_reminders" in e15[0].message
        assert e15[0].severity == "error"

    def test_cron_with_api_model_clean(self, tmp_path: Path) -> None:
        """_cron_send_reminders() with @api.model does NOT fire E15."""
        from tests.fixtures.logic_writer.good_cron import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        e15 = [i for i in result.errors if i.code == "E15"]
        assert e15 == []


# ===========================================================================
# E16: Exclusion Zone Violation (Skeleton Diff)
# ===========================================================================


class TestE16ExclusionZoneViolation:
    """E16: modifications outside BUSINESS LOGIC marker zones are errors."""

    def test_modification_outside_markers_triggers_e16(self, tmp_path: Path) -> None:
        """Lines changed outside marker zones trigger E16."""
        from tests.fixtures.logic_writer.e16_exclusion_zone_violation import (
            FILLED_BAD_SOURCE,
            SKELETON_SOURCE,
        )

        # Setup: module dir + skeleton dir
        mod = tmp_path / "test_module"
        skeleton_dir = tmp_path / ".amil-skeleton" / "test_module"

        _write(mod / "__manifest__.py", """\
            {
                'name': 'Test Module',
                'version': '17.0.1.0.0',
                'depends': ['base'],
                'data': [],
            }
        """)
        _write(mod / "__init__.py", "from . import models\n")
        _write(mod / "models" / "__init__.py", "from . import main\n")
        _write(mod / "models" / "main.py", FILLED_BAD_SOURCE)

        # Skeleton has the original template output
        _write(skeleton_dir / "models" / "main.py", SKELETON_SOURCE)

        result = semantic_validate(mod)
        e16 = [i for i in result.errors if i.code == "E16"]
        assert len(e16) >= 1

    def test_modification_inside_markers_clean(self, tmp_path: Path) -> None:
        """Lines changed only inside marker zones do NOT fire E16."""
        from tests.fixtures.logic_writer.e16_exclusion_zone_violation import (
            FILLED_GOOD_SOURCE,
            SKELETON_SOURCE,
        )

        mod = tmp_path / "test_module"
        skeleton_dir = tmp_path / ".amil-skeleton" / "test_module"

        _write(mod / "__manifest__.py", """\
            {
                'name': 'Test Module',
                'version': '17.0.1.0.0',
                'depends': ['base'],
                'data': [],
            }
        """)
        _write(mod / "__init__.py", "from . import models\n")
        _write(mod / "models" / "__init__.py", "from . import main\n")
        _write(mod / "models" / "main.py", FILLED_GOOD_SOURCE)

        _write(skeleton_dir / "models" / "main.py", SKELETON_SOURCE)

        result = semantic_validate(mod)
        e16 = [i for i in result.errors if i.code == "E16"]
        assert e16 == []

    def test_no_skeleton_dir_returns_empty(self, tmp_path: Path) -> None:
        """E16 silently returns empty list when .amil-skeleton/ does not exist."""
        mod = _make_valid_module(tmp_path)
        # No skeleton directory created
        result = semantic_validate(mod)
        e16 = [i for i in result.errors if i.code == "E16"]
        assert e16 == []


# ===========================================================================
# Clean Fills: Good Override + Good Action + Good Cron pass all checks
# ===========================================================================


class TestCleanOverrideActionCron:
    """Good override/action/cron fixtures pass all E13-E16, W5 checks."""

    def test_good_override_passes_all(self, tmp_path: Path) -> None:
        """Good override fixture passes E13 with zero issues."""
        from tests.fixtures.logic_writer.good_override import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        new_errors = [i for i in result.errors if i.code in ("E13", "E15", "E16")]
        new_warnings = [i for i in result.warnings if i.code == "W5"]
        assert new_errors == [], f"Good override produced errors: {[e.message for e in new_errors]}"
        assert new_warnings == [], f"Good override produced warnings: {[w.message for w in new_warnings]}"

    def test_good_action_passes_all(self, tmp_path: Path) -> None:
        """Good action fixture passes W5 with zero warnings."""
        from tests.fixtures.logic_writer.good_action import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        new_errors = [i for i in result.errors if i.code in ("E13", "E15", "E16")]
        new_warnings = [i for i in result.warnings if i.code == "W5"]
        assert new_errors == [], f"Good action produced errors: {[e.message for e in new_errors]}"
        assert new_warnings == [], f"Good action produced warnings: {[w.message for w in new_warnings]}"

    def test_good_cron_passes_all(self, tmp_path: Path) -> None:
        """Good cron fixture passes E15 with zero issues."""
        from tests.fixtures.logic_writer.good_cron import SOURCE

        mod = _make_module_with_model(tmp_path, SOURCE)
        result = semantic_validate(mod)
        new_errors = [i for i in result.errors if i.code in ("E13", "E15", "E16")]
        new_warnings = [i for i in result.warnings if i.code == "W5"]
        assert new_errors == [], f"Good cron produced errors: {[e.message for e in new_errors]}"
        assert new_warnings == [], f"Good cron produced warnings: {[w.message for w in new_warnings]}"


# ===========================================================================
# E17: Extension xpath references non-existent base field
# W6: Unknown base model warning
# ===========================================================================


def _make_extension_module(
    root: Path,
    model_name: str,
    inherit_view_xml: str,
    module_name: str = "test_ext_module",
) -> Path:
    """Scaffold a minimal extension module with an inherited view XML."""
    mod = root / module_name
    _write(mod / "__manifest__.py", f"""\
        {{
            'name': 'Test Extension',
            'version': '17.0.1.0.0',
            'depends': ['base', 'hr'],
            'data': [
                'views/hr_employee_views.xml',
            ],
        }}
    """)
    _write(mod / "__init__.py", "from . import models\n")
    _write(mod / "models" / "__init__.py", "from . import hr_employee\n")
    _write(mod / "models" / "hr_employee.py", f"""\
        from odoo import fields, models

        class HrEmployee(models.Model):
            _inherit = '{model_name}'

            test_field = fields.Char(string='Test')
    """)
    _write(mod / "views" / "hr_employee_views.xml", inherit_view_xml)
    return mod


class TestE17ExtensionXpathValidation:
    """E17: Extension xpath references non-existent base field."""

    def test_e17_known_model_bad_field(self, tmp_path: Path) -> None:
        """E17 error when xpath references field not on known model (Tier 1)."""
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_hr_employee_form_inherit_test" model="ir.ui.view">
                    <field name="name">hr.employee.form.inherit.test</field>
                    <field name="model">hr.employee</field>
                    <field name="inherit_id" ref="hr.view_employee_form"/>
                    <field name="arch" type="xml">
                        <xpath expr="//field[@name='nonexistent_field']" position="after">
                            <field name="test_field"/>
                        </xpath>
                    </field>
                </record>
            </odoo>
        """)
        mod = _make_extension_module(tmp_path, "hr.employee", xml)
        result = semantic_validate(mod)
        e17_errors = [i for i in result.errors if i.code == "E17"]
        assert len(e17_errors) == 1
        assert "nonexistent_field" in e17_errors[0].message
        assert "hr.employee" in e17_errors[0].message

    def test_e17_known_model_good_field(self, tmp_path: Path) -> None:
        """E17 passes when xpath references valid field on known model."""
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_hr_employee_form_inherit_test" model="ir.ui.view">
                    <field name="name">hr.employee.form.inherit.test</field>
                    <field name="model">hr.employee</field>
                    <field name="inherit_id" ref="hr.view_employee_form"/>
                    <field name="arch" type="xml">
                        <xpath expr="//field[@name='department_id']" position="after">
                            <field name="test_field"/>
                        </xpath>
                    </field>
                </record>
            </odoo>
        """)
        mod = _make_extension_module(tmp_path, "hr.employee", xml)
        result = semantic_validate(mod)
        e17_errors = [i for i in result.errors if i.code == "E17"]
        assert len(e17_errors) == 0

    def test_e17_registry_tier2(self, tmp_path: Path) -> None:
        """E17 error when xpath references field not in registry model (Tier 2)."""
        from amil_utils.registry import ModelRegistry

        # Create a registry with uni.student model via register_module
        reg = ModelRegistry(tmp_path / "registry.json")
        reg.register_module("uni_core", {
            "depends": ["base"],
            "models": [
                {
                    "_name": "uni.student",
                    "fields": {"name": {"type": "Char"}, "gpa": {"type": "Float"}},
                }
            ],
        })

        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_uni_student_form_inherit_test" model="ir.ui.view">
                    <field name="name">uni.student.form.inherit.test</field>
                    <field name="model">uni.student</field>
                    <field name="inherit_id" ref="uni_core.view_student_form"/>
                    <field name="arch" type="xml">
                        <xpath expr="//field[@name='missing_field']" position="after">
                            <field name="test_field"/>
                        </xpath>
                    </field>
                </record>
            </odoo>
        """)
        mod = _make_extension_module(tmp_path, "uni.student", xml, module_name="test_ext_uni")
        result = semantic_validate(mod, registry=reg)
        e17_errors = [i for i in result.errors if i.code == "E17"]
        assert len(e17_errors) == 1
        assert "missing_field" in e17_errors[0].message

    def test_e17_unknown_model(self, tmp_path: Path) -> None:
        """W6 warning for unknown base model not in known_models or registry."""
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_oca_custom_form_inherit_test" model="ir.ui.view">
                    <field name="name">oca.custom.model.form.inherit.test</field>
                    <field name="model">oca.custom.model</field>
                    <field name="inherit_id" ref="oca_module.view_custom_form"/>
                    <field name="arch" type="xml">
                        <xpath expr="//field[@name='some_field']" position="after">
                            <field name="test_field"/>
                        </xpath>
                    </field>
                </record>
            </odoo>
        """)
        mod = _make_extension_module(tmp_path, "oca.custom.model", xml, module_name="test_ext_oca")
        result = semantic_validate(mod)
        w6_warnings = [i for i in result.warnings if i.code == "W6"]
        assert len(w6_warnings) == 1
        assert "oca.custom.model" in w6_warnings[0].message
        # No E17 error for unknown models
        e17_errors = [i for i in result.errors if i.code == "E17"]
        assert len(e17_errors) == 0

    def test_e17_non_inherited_view_ignored(self, tmp_path: Path) -> None:
        """E17 skips non-inherited (regular) view records."""
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_test_form" model="ir.ui.view">
                    <field name="name">test.form</field>
                    <field name="model">hr.employee</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="nonexistent_field"/>
                        </form>
                    </field>
                </record>
            </odoo>
        """)
        mod = _make_extension_module(tmp_path, "hr.employee", xml)
        result = semantic_validate(mod)
        e17_errors = [i for i in result.errors if i.code == "E17"]
        assert len(e17_errors) == 0

    def test_e17_page_xpath_skipped(self, tmp_path: Path) -> None:
        """E17 skips non-field xpaths like //page[@name='public']."""
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_hr_employee_form_inherit_test" model="ir.ui.view">
                    <field name="name">hr.employee.form.inherit.test</field>
                    <field name="model">hr.employee</field>
                    <field name="inherit_id" ref="hr.view_employee_form"/>
                    <field name="arch" type="xml">
                        <xpath expr="//page[@name='public']" position="after">
                            <page string="Test" name="test_page">
                                <group>
                                    <field name="test_field"/>
                                </group>
                            </page>
                        </xpath>
                    </field>
                </record>
            </odoo>
        """)
        mod = _make_extension_module(tmp_path, "hr.employee", xml)
        result = semantic_validate(mod)
        e17_errors = [i for i in result.errors if i.code == "E17"]
        assert len(e17_errors) == 0

    def test_e17_group_xpath_skipped(self, tmp_path: Path) -> None:
        """E17 skips non-field xpaths like //group[@name='settings']."""
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_hr_employee_form_inherit_test" model="ir.ui.view">
                    <field name="name">hr.employee.form.inherit.test</field>
                    <field name="model">hr.employee</field>
                    <field name="inherit_id" ref="hr.view_employee_form"/>
                    <field name="arch" type="xml">
                        <xpath expr="//group[@name='settings']" position="inside">
                            <field name="test_field"/>
                        </xpath>
                    </field>
                </record>
            </odoo>
        """)
        mod = _make_extension_module(tmp_path, "hr.employee", xml)
        result = semantic_validate(mod)
        e17_errors = [i for i in result.errors if i.code == "E17"]
        assert len(e17_errors) == 0

    def test_known_models_common_views(self) -> None:
        """known_odoo_models.json has common_views for frequently extended models."""
        import json

        data_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "amil_utils" / "data" / "known_odoo_models.json"
        )
        data = json.loads(data_path.read_text(encoding="utf-8"))
        models = data["models"]

        expected_models_with_views = [
            "hr.employee", "res.partner", "sale.order",
            "purchase.order", "account.move", "product.template",
            "stock.picking", "crm.lead",
        ]
        for model_name in expected_models_with_views:
            assert model_name in models, f"Missing model: {model_name}"
            assert "common_views" in models[model_name], (
                f"Missing common_views for {model_name}"
            )
            views = models[model_name]["common_views"]
            assert "form" in views, f"Missing form view for {model_name}"
            assert "tree" in views, f"Missing tree view for {model_name}"
            assert "search" in views, f"Missing search view for {model_name}"


# ===========================================================================
# semantic_validate_patterns vs semantic_validate_full
# ===========================================================================


class TestSemanticValidatePatterns:
    """Verify ``semantic_validate_patterns`` runs only AST-pattern checks."""

    def test_alias_is_full(self) -> None:
        """``semantic_validate`` is an alias for ``semantic_validate_full``."""
        assert semantic_validate is semantic_validate_full

    def test_patterns_returns_result(self, tmp_path: Path) -> None:
        """patterns-only mode returns a SemanticValidationResult."""
        mod = _make_valid_module(tmp_path)
        result = semantic_validate_patterns(mod)
        assert isinstance(result, SemanticValidationResult)
        assert result.module == mod.name

    def test_patterns_skips_structural_checks(self, tmp_path: Path) -> None:
        """patterns-only mode does NOT run structural checks (E3-E6, W1-W4, W9, E17).

        We create a module with a deliberate E3 error (bad view field ref)
        that full mode would catch, but patterns-only mode should skip.
        """
        mod = tmp_path / "test_mod"
        _write(mod / "__manifest__.py", """\
            {
                'name': 'Test',
                'version': '17.0.1.0.0',
                'depends': ['base'],
                'data': ['views/v.xml'],
            }
        """)
        _write(mod / "__init__.py", "from . import models\n")
        _write(mod / "models" / "__init__.py", "from . import m\n")
        _write(mod / "models" / "m.py", """\
            from odoo import fields, models

            class TestModel(models.Model):
                _name = 'test.model'
                _description = 'Test'
                name = fields.Char()
        """)
        # View references a non-existent field -- triggers E3 in full mode
        _write(mod / "views" / "v.xml", """\
            <?xml version="1.0" encoding="utf-8"?>
            <odoo>
                <record id="view_test_form" model="ir.ui.view">
                    <field name="name">test.form</field>
                    <field name="model">test.model</field>
                    <field name="arch" type="xml">
                        <form>
                            <field name="nonexistent_field"/>
                        </form>
                    </field>
                </record>
            </odoo>
        """)
        # No security CSV -- triggers E4 in full mode
        patterns_result = semantic_validate_patterns(mod)
        full_result = semantic_validate_full(mod)

        # Patterns mode should NOT detect E3 or E4
        patterns_codes = {i.code for i in patterns_result.errors}
        assert "E3" not in patterns_codes
        assert "E4" not in patterns_codes

        # Full mode SHOULD detect E3 and/or E4
        full_codes = {i.code for i in full_result.errors}
        assert full_codes & {"E3", "E4"}  # at least one structural error

    def test_patterns_catches_ast_errors(self, tmp_path: Path) -> None:
        """patterns-only mode catches Python syntax errors (E1)."""
        mod = tmp_path / "broken_mod"
        _write(mod / "__manifest__.py", """\
            {
                'name': 'Broken',
                'version': '17.0.1.0.0',
                'depends': ['base'],
                'data': [],
            }
        """)
        _write(mod / "__init__.py", "from . import models\n")
        _write(mod / "models" / "__init__.py", "from . import bad\n")
        _write(mod / "models" / "bad.py", "def broken(\n")  # Syntax error

        result = semantic_validate_patterns(mod)
        codes = {i.code for i in result.errors}
        assert "E1" in codes

    def test_patterns_catches_xml_errors(self, tmp_path: Path) -> None:
        """patterns-only mode catches malformed XML (E2)."""
        mod = tmp_path / "xml_mod"
        _write(mod / "__manifest__.py", """\
            {
                'name': 'XML',
                'version': '17.0.1.0.0',
                'depends': ['base'],
                'data': ['views/bad.xml'],
            }
        """)
        _write(mod / "__init__.py", "")
        _write(mod / "views" / "bad.xml", "<odoo><unclosed>")

        result = semantic_validate_patterns(mod)
        codes = {i.code for i in result.errors}
        assert "E2" in codes

    def test_patterns_fewer_check_codes_than_full(self, tmp_path: Path) -> None:
        """patterns-only mode runs strictly fewer check categories.

        On a valid module, both modes should pass -- but on a module with
        issues in every category, patterns mode produces a subset of codes.
        """
        mod = _make_valid_module(tmp_path)

        patterns_result = semantic_validate_patterns(mod)
        full_result = semantic_validate_full(mod)

        # Both should succeed on a valid module
        all_patterns_codes = (
            {i.code for i in patterns_result.errors}
            | {i.code for i in patterns_result.warnings}
        )
        all_full_codes = (
            {i.code for i in full_result.errors}
            | {i.code for i in full_result.warnings}
        )
        # Patterns result codes should be a subset of full result codes
        assert all_patterns_codes <= all_full_codes

    def test_patterns_only_takes_output_dir(self) -> None:
        """``semantic_validate_patterns`` signature takes only output_dir."""
        import inspect

        sig = inspect.signature(semantic_validate_patterns)
        params = list(sig.parameters.keys())
        assert params == ["output_dir"]

    def test_full_accepts_registry_and_spec(self) -> None:
        """``semantic_validate_full`` signature matches old ``semantic_validate``."""
        import inspect

        sig = inspect.signature(semantic_validate_full)
        params = list(sig.parameters.keys())
        assert "output_dir" in params
        assert "registry" in params
        assert "spec" in params
