# Odoo 17.0/18.0/19.0 Global Conventions

> **Supported Versions: Odoo 17.0, 18.0, and 19.0**
> This knowledge base covers Odoo 17.0 (primary), 18.0, and 19.0. The base rules
> are for 17.0. Each KB file has "Changed in 18.0" and "Changed in 19.0" sections
> documenting breaking changes and new patterns. Read `odoo_version` from spec/config
> to determine which version-specific rules to apply. Do NOT mix patterns from Odoo 8-16.

## Naming Conventions

### Module Name
- **Format:** `snake_case` (e.g., `library_management`, `fleet_repair`)
- **Prefix:** Use company/org prefix for custom modules (e.g., `acme_hr_leave`)
- Never use hyphens, dots, or uppercase in module technical names

### Model Name (`_name`)
- **Format:** Dot notation (e.g., `library.book`, `fleet.repair.order`)
- Maps to database table: dots become underscores (`library_book`)
- Use the module name as prefix for custom models

### Field Names
- **Format:** `snake_case` (e.g., `date_published`, `partner_id`)
- Relational fields end with `_id` (Many2one) or `_ids` (One2many, Many2many)
- Boolean fields start with `is_` or `has_` (e.g., `is_active`, `has_attachments`)
- Computed fields: same naming, defined BEFORE their compute method

### Method Names
- Compute methods: `_compute_xxx` (e.g., `_compute_total_amount`)
- Constraint methods: `_check_xxx` (e.g., `_check_date_range`)
- Onchange methods: `_onchange_xxx` (e.g., `_onchange_partner_id`)
- Action methods: `action_xxx` (e.g., `action_confirm`, `action_cancel`)
- Private/internal: prefix with `_` (e.g., `_prepare_invoice_values`)

## Import Pattern

**CORRECT:**
```python
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero
```

**WRONG (pre-Odoo 10 -- NEVER use):**
```python
from openerp import api, fields, models  # WRONG -- openerp removed in Odoo 10
```

### Import Ordering (OCA Standard)
1. Standard library imports
2. Third-party imports
3. `from odoo import ...`
4. `from odoo.addons import ...`
5. Blank line, then module-relative imports

## Python Style (OCA Deviations from PEP 8)

- **Line length:** 120 characters (not PEP 8's 79)
- **Trailing commas:** Always use on the last item of multi-line dicts, lists, function args
- **String quotes:** Double quotes `"` preferred for user-facing strings, single `'` for internal
- **No `@api.multi`** -- removed in Odoo 13. Methods operate on recordsets by default.
- **No `@api.one`** -- removed in Odoo 13. Iterate `self` explicitly.
- **No `@api.returns`** -- removed in Odoo 13.

## File Organization (OCA Directory Structure)

```
module_name/
  __init__.py
  __manifest__.py
  README.rst
  models/
    __init__.py
    model_one.py          # One model per file
    model_two.py
  views/
    model_one_views.xml
    model_two_views.xml
    menu.xml
  security/
    security.xml          # Groups and categories
    ir.model.access.csv   # ACLs
  tests/
    __init__.py
    test_model_one.py
    test_model_two.py
  data/                   # Default data loaded on install
  demo/                   # Demo data (only in demo mode)
    demo_data.xml
  i18n/                   # Translation files
  static/
    description/
      icon.png
```

## Version Format

**Pattern:** `17.0.X.Y.Z` (5-part)

| Part | Meaning | Example |
|------|---------|---------|
| `17.0` | Odoo version | Always `17.0` |
| `X` | Major module version | `1` |
| `Y` | Minor (features) | `0` |
| `Z` | Patch (bugfixes) | `0` |

**Example:** `"version": "17.0.1.0.0"`

## License

- **OCA modules:** `"LGPL-3"` (required by OCA)
- **Other valid:** `"GPL-3"`, `"AGPL-3"`, `"OEEL-1"` (Enterprise)
- `license` key is **REQUIRED** in `__manifest__.py` (pylint-odoo E8501)

## XML Rules

- **Root tag:** Always `<odoo>` (never `<openerp>` -- removed in Odoo 10)
- **View modifiers:** Inline expressions only (e.g., `invisible="state != 'draft'"`)
- **No `attrs` dict:** The `attrs` attribute is completely removed in Odoo 17.0
- **No `states` attribute:** Use `invisible="state != 'draft'"` instead
- **List views:** Use `<tree>` tag in Odoo 17.0 (NOT `<list>`, which is Odoo 18+ only)

## Top Version Pitfalls

| Pitfall | Wrong (Old) | Correct (17.0) |
|---------|-------------|-----------------|
| Import path | `from openerp import ...` | `from odoo import ...` |
| Decorators | `@api.multi`, `@api.one` | Removed -- methods work on recordsets |
| View attrs | `attrs="{'invisible': [...]}"` | `invisible="expression"` |
| States attr | `states="draft"` on buttons | `invisible="state != 'draft'"` |
| Old ORM | `_columns = {...}` | `name = fields.Char(...)` |
| Root tag | `<openerp>` | `<odoo>` |
| List tag | `<list>` (18+ only) | `<tree>` |
| Column hide | `attrs="{'column_invisible':...}"` | `column_invisible="expression"` |

## Model Class Template

```python
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MyModel(models.Model):
    _name = "my.module.model"
    _description = "My Model"
    _order = "name, create_date desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Name", required=True, tracking=True)
    active = fields.Boolean(default=True)
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("done", "Done"),
        ],
        string="Status",
        default="draft",
        tracking=True,
    )
```

## Odoo 18.0 Version Awareness

This system supports generating modules for both Odoo 17.0 and 18.0. The `odoo_version`
field in spec.json or defaults.json controls which version-specific patterns to use.

**Key 18.0 breaking changes** (see individual KB files for details):
- `<tree>` tag removed -- use `<list>` (see views.md "Changed in 18.0")
- `view_mode="tree,form"` becomes `view_mode="list,form"` (see views.md)
- `states=` parameter removed from fields (see models.md "Changed in 18.0")
- `group_operator=` renamed to `aggregator=` (see models.md)
- `_name_search()` replaced by `_search_display_name()` (see models.md)
- Version prefix: `18.0.X.Y.Z` (see manifest.md "Changed in 18.0")

**Version-specific template directories:**
- `templates/17.0/` -- 17.0 view, model, and action templates (uses `<tree>`, `tree,form`)
- `templates/18.0/` -- 18.0 view, model, and action templates (uses `<list>`, `list,form`)
- `templates/19.0/` -- 19.0 view, model, and action templates (builds on 18.0 with 19.0-specific changes)
- `templates/shared/` -- Templates identical across versions

### Odoo 19.0 Version Awareness

When `odoo_version` is `"19.0"`, these additional rules apply (on top of 18.0 changes):

| What Changed | Before (18.0) | Now (19.0) | Impact |
|-------------|---------------|------------|--------|
| `name_get()` | Deprecated | **Removed** | Always use `_compute_display_name()` |
| `read_group()` | Available | **Replaced** | Use `_read_group()` or `formatted_read_group()` |
| `@api.returns` | Available | **Removed** | Use modern return patterns |
| HTTP route type | `type='json'` | `type='jsonrpc'` | Update all controllers |
| Kanban card | `t-name="kanban-box"` | `t-name="card"` | Update kanban views |
| QWeb `t-esc` | Available | **Deprecated** | Use `t-out` instead |
| SQL constraints | `_sql_constraints` | `models.Constraint` recommended | New import pattern |
| Domain helpers | `expression.OR` | `Domain.OR` | New import |
| Security groups | `category_id` | `privilege_id` | Update group XML |
| Version prefix | `18.0.X.Y.Z` | `19.0.X.Y.Z` | Manifest version |

**Enterprise edition awareness:**
- Use `amil-utils check-edition` to detect Enterprise-only dependencies
- Enterprise module registry at `data/enterprise_modules.json` (31 modules)
- OCA Community alternatives suggested where available

### Domain Knowledge Files (load when the spec touches the domain)

- `accounting.md` -- invoicing, journals, payments (load when depends includes `account`)
- `inventory.md` -- warehouse, stock moves, pickings (load when depends includes `stock`)
- `owl.md` -- OWL JavaScript components (load when spec has dashboards/JS widgets)
- `education.md` -- university/HEC domain: enrollment, GPA, attendance, merit, challans, affiliate colleges, OCA payroll (BPS/TTS), QEC/OBE (load when `localization == "pk"` or module name starts with `university_`/`uni_`)

---
*Knowledge base for Odoo 17.0/18.0/19.0 -- loaded by all agents via @include*
