# Odoo 17.0/18.0/19.0 Models & ORM Rules

> Loaded alongside MASTER.md. Covers field types, relational fields, computed fields,
> constraints, CRUD overrides, decorators, inheritance, and OCA conventions.

## Field Types

### Basic Fields

| Type | Usage | Common Parameters |
|------|-------|-------------------|
| `Char` | Short text (name, code) | `string`, `required`, `size`, `trim` |
| `Text` | Long text (notes, description) | `string`, `translate` |
| `Integer` | Whole numbers | `string`, `default` |
| `Float` | Decimal numbers | `string`, `digits` |
| `Boolean` | True/False flags | `string`, `default` |
| `Date` | Date only | `string`, `default` |
| `Datetime` | Date + time | `string`, `default` |
| `Selection` | Dropdown choices | `selection`, `string`, `default` |
| `Binary` | File/image storage | `string`, `attachment` |
| `Html` | Rich text with HTML | `string`, `sanitize` |
| `Monetary` | Currency amounts | `string`, `currency_field` |

### Use `fields.Boolean` not `fields.Integer` for flags

**WRONG:**
```python
is_active = fields.Integer(string="Active", default=1)
```

**CORRECT:**
```python
is_active = fields.Boolean(string="Active", default=True)
```

**Why:** Boolean fields render as checkboxes in views and support proper truthiness. Integer flags require manual 0/1 handling and confuse search filters.

### Use `fields.Selection` not `fields.Char` for fixed choices

**WRONG:**
```python
state = fields.Char(string="Status", default="draft")
```

**CORRECT:**
```python
state = fields.Selection(
    selection=[
        ("draft", "Draft"),
        ("confirmed", "Confirmed"),
        ("done", "Done"),
    ],
    string="Status",
    default="draft",
)
```

**Why:** Selection fields enforce valid values, render as dropdowns in forms, and support statusbar widget. Char fields accept any string, with no UI or validation.

### Use `fields.Monetary` with `currency_field` for amounts

**WRONG:**
```python
amount = fields.Float(string="Amount", digits=(16, 2))
```

**CORRECT:**
```python
currency_id = fields.Many2one(
    comodel_name="res.currency",
    string="Currency",
    default=lambda self: self.env.company.currency_id,
)
amount = fields.Monetary(string="Amount", currency_field="currency_id")
```

**Why:** Monetary fields auto-format based on currency, handle rounding correctly, and display the currency symbol. Float with manual digits is fragile and locale-unaware.

### Use `fields.Date.today()` not `date.today()` for defaults

**WRONG:**
```python
from datetime import date

date_order = fields.Date(string="Order Date", default=date.today)
```

**CORRECT:**
```python
date_order = fields.Date(string="Order Date", default=fields.Date.today)
```

**Why:** `fields.Date.today()` respects the Odoo user's timezone context. Python's `date.today()` uses the server timezone, which may differ.

## Relational Fields

### Many2one -- single reference to another model

```python
partner_id = fields.Many2one(
    comodel_name="res.partner",
    string="Customer",
    required=True,
    ondelete="restrict",
)
```

Key parameters: `comodel_name` (required), `string`, `required`, `ondelete` (`"restrict"`, `"cascade"`, `"set null"`), `domain`, `index=True`.

### One2many -- reverse of Many2one

```python
order_line_ids = fields.One2many(
    comodel_name="sale.order.line",
    inverse_name="order_id",
    string="Order Lines",
)
```

Key parameters: `comodel_name`, `inverse_name` (required -- the Many2one field on the child model), `string`, `copy`.

### Many2many -- multiple references

```python
tag_ids = fields.Many2many(comodel_name="project.tag", string="Tags")
```

For explicit relation table, add `relation`, `column1`, `column2` parameters.

### Always use `comodel_name=` keyword for clarity

**WRONG:**
```python
partner_id = fields.Many2one("res.partner", "Customer")
```

**CORRECT:**
```python
partner_id = fields.Many2one(
    comodel_name="res.partner",
    string="Customer",
)
```

**Why:** Positional arguments are ambiguous and error-prone. OCA coding standards require keyword arguments for relational field parameters.

### Always set `ondelete` on Many2one

**WRONG:**
```python
partner_id = fields.Many2one(comodel_name="res.partner", string="Partner")
```

**CORRECT:**
```python
partner_id = fields.Many2one(
    comodel_name="res.partner",
    string="Partner",
    ondelete="restrict",
)
```

**Why:** Without explicit `ondelete`, PostgreSQL defaults to `SET NULL`, which silently nullifies references when the target record is deleted. Set `"restrict"` (prevent deletion), `"cascade"` (delete together), or `"set null"` explicitly.

## Computed Fields

### Pattern: field definition BEFORE compute method

**WRONG:**
```python
class SaleOrder(models.Model):
    _name = "sale.order"

    @api.depends("order_line_ids.price_total")
    def _compute_amount_total(self):
        for record in self:
            record.amount_total = sum(record.order_line_ids.mapped("price_total"))

    amount_total = fields.Float(
        string="Total",
        compute="_compute_amount_total",
        store=True,
    )
```

**CORRECT:**
```python
class SaleOrder(models.Model):
    _name = "sale.order"
    _description = "Sale Order"

    amount_total = fields.Float(
        string="Total",
        compute="_compute_amount_total",
        store=True,
    )

    @api.depends("order_line_ids.price_total")
    def _compute_amount_total(self):
        for record in self:
            record.amount_total = sum(record.order_line_ids.mapped("price_total"))
```

**Why:** OCA convention requires field definitions at the top of the class, methods below. Readers can see the model's data shape before the logic.

### Always use `@api.depends` for stored computed fields

**WRONG:**
```python
total = fields.Float(compute="_compute_total", store=True)

def _compute_total(self):
    for record in self:
        record.total = record.qty * record.price
```

**CORRECT:**
```python
total = fields.Float(
    string="Total",
    compute="_compute_total",
    store=True,
)

@api.depends("qty", "price")
def _compute_total(self):
    for record in self:
        record.total = record.qty * record.price
```

**Why:** Without `@api.depends`, Odoo cannot know when to recompute. The stored value becomes stale and never updates when `qty` or `price` changes.

### Iterate `self` in compute methods

**WRONG:**
```python
@api.depends("qty")
def _compute_total(self):
    self.total = self.qty * self.price  # Fails on multi-record recordsets
```

**CORRECT:**
```python
@api.depends("qty")
def _compute_total(self):
    for record in self:
        record.total = record.qty * record.price
```

**Why:** Compute methods can be called on multi-record recordsets. Assigning on `self` directly raises an error when `len(self) > 1`.

## Constraints

### `@api.constrains` for Python validation

**WRONG:**
```python
_constraints = [
    (_check_date, "End date must be after start date.", ["date_end"]),
]
```

**CORRECT:**
```python
@api.constrains("date_start", "date_end")
def _check_date_range(self):
    for record in self:
        if record.date_start and record.date_end and record.date_end < record.date_start:
            raise ValidationError("End date must be after start date.")
```

**Why:** `_constraints` list is the old API (removed). Use `@api.constrains` decorator with `ValidationError`.

### SQL constraints for simple uniqueness/checks

```python
_sql_constraints = [
    ("code_unique", "UNIQUE(code)", "The code must be unique."),
    ("check_qty_positive", "CHECK(quantity >= 0)", "Quantity must be positive."),
]
```

## CRUD Overrides

### `create` with `@api.model_create_multi`

**WRONG:**
```python
@api.model
def create(self, vals):
    vals["code"] = self.env["ir.sequence"].next_by_code("my.model")
    return super().create(vals)
```

**CORRECT:**
```python
@api.model_create_multi
def create(self, vals_list):
    for vals in vals_list:
        if not vals.get("code"):
            vals["code"] = self.env["ir.sequence"].next_by_code("my.model")
    return super().create(vals_list)
```

**Why:** Odoo 17 uses `@api.model_create_multi` which receives a list of dicts. Using `@api.model` with a single dict still works but is less efficient for batch creation and triggers a deprecation path.

### `write` and `unlink` overrides

```python
def write(self, vals):
    result = super().write(vals)
    if "state" in vals and vals["state"] == "confirmed":
        self._send_confirmation_email()
    return result

def unlink(self):
    for record in self:
        if record.state != "draft":
            raise UserError("Cannot delete a non-draft record.")
    return super().unlink()
```

### Always call `super()` in CRUD overrides

**WRONG:**
```python
def write(self, vals):
    # Custom logic only, forgot super()
    self.message_post(body="Record updated")
    return True
```

**CORRECT:**
```python
def write(self, vals):
    result = super().write(vals)
    self.message_post(body="Record updated")
    return result
```

**Why:** Not calling `super()` skips all parent class logic, including ORM field writes, access checks, and inherited behavior. The record is never actually updated.

## Decorators

### Allowed Decorators in Odoo 17.0

| Decorator | Purpose | Use With |
|-----------|---------|----------|
| `@api.depends(...)` | Trigger computed field recomputation | Compute methods |
| `@api.constrains(...)` | Validate field values | Constraint methods |
| `@api.onchange(...)` | React to form field changes (UI only) | Onchange methods |
| `@api.model` | Class-level method (no recordset) | Search, default_get |
| `@api.model_create_multi` | Batch `create` | `create()` override |

### Forbidden Decorators (Removed Since Odoo 13)

| Decorator | Status | Replacement |
|-----------|--------|-------------|
| `@api.multi` | **REMOVED** | Not needed -- methods work on recordsets by default |
| `@api.one` | **REMOVED** | Iterate `self` explicitly in the method body |
| `@api.returns(...)` | **REMOVED** | Return values directly |

**WRONG:**
```python
@api.multi
def action_confirm(self):
    for record in self:
        record.state = "confirmed"
```

**CORRECT:**
```python
def action_confirm(self):
    for record in self:
        record.state = "confirmed"
```

**Why:** `@api.multi` was removed in Odoo 13. Methods already operate on recordsets. Adding it causes an `AttributeError`.

## Inheritance

### Extend an existing model (`_inherit` only)

```python
class ResPartner(models.Model):
    _inherit = "res.partner"

    loyalty_points = fields.Integer(string="Loyalty Points", default=0)
```

No `_name` needed -- adds fields/methods to the existing model.

### Create new model inheriting from another (`_name` + `_inherit`)

```python
class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Project Task"

    name = fields.Char(string="Task Name", required=True)
```

### Delegation inheritance (`_inherits`)

```python
class Employee(models.Model):
    _name = "hr.employee"
    _inherits = {"res.partner": "partner_id"}
    _description = "Employee"
    partner_id = fields.Many2one(comodel_name="res.partner", required=True, ondelete="cascade")
```

Creates a composition relationship -- Employee "has a" Partner, accessing all Partner fields directly while storing them in `res_partner` table.

## mail.thread and mail.activity.mixin

### When to add mail.thread inheritance

**Rule:** When `mail` is in the module's `depends` list, the model MUST inherit from both `mail.thread` and `mail.activity.mixin`.

**WRONG:**
```python
# __manifest__.py has "depends": ["base", "mail"]
# But model lacks mail.thread inheritance:
class HrTraining(models.Model):
    _name = "hr.training"
    _description = "HR Training"

    name = fields.Char(string="Name", required=True)
```

**CORRECT:**
```python
class HrTraining(models.Model):
    _name = "hr.training"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "HR Training"

    name = fields.Char(string="Name", required=True)
```

**Why:** Without `mail.thread`, the model cannot use Odoo's chatter (messages, followers, activities). The module will install but chatter fields (`message_ids`, `message_follower_ids`, `activity_ids`) will cause `KeyError` at runtime.

### The Triple Dependency

**Rule:** Three things must be consistent when using mail/chatter:

1. **Manifest:** `"mail"` in the `depends` list
2. **Model:** `_inherit = ["mail.thread", "mail.activity.mixin"]`
3. **View:** `<div class="oe_chatter">` with `message_follower_ids` and `message_ids` fields

If any one is missing, the module will either fail to install or crash at runtime.

**WRONG:** Chatter in view but no mail.thread inheritance:
```xml
<!-- views/hr_training_views.xml -->
<div class="oe_chatter">
    <field name="message_follower_ids"/>
    <field name="message_ids"/>
</div>
```
```python
# models/hr_training.py -- MISSING _inherit!
class HrTraining(models.Model):
    _name = "hr.training"
    _description = "HR Training"
```

**CORRECT:** All three aligned:
```python
# __manifest__.py
{"depends": ["base", "mail"]}

# models/hr_training.py
class HrTraining(models.Model):
    _name = "hr.training"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "HR Training"
```
```xml
<!-- views/hr_training_views.xml -->
<div class="oe_chatter">
    <field name="message_follower_ids"/>
    <field name="message_ids"/>
</div>
```

**Why:** Odoo 17.0 raises `ValueError` during module loading if a view references fields that don't exist on the model. `message_ids` and `message_follower_ids` only exist when the model inherits `mail.thread`.

### mail.activity.mixin is always paired with mail.thread

**Rule:** Always inherit both `mail.thread` AND `mail.activity.mixin` together. Never inherit just one.

**Why:** `mail.activity.mixin` depends on `mail.thread` fields. Inheriting activity mixin without thread causes `MissingError`. Inheriting thread without activity mixin loses the activity scheduling feature that users expect when they see the chatter.

## OCA Conventions

### One model per Python file

**WRONG:** Multiple models in `models/models.py`:
```python
class Book(models.Model):
    _name = "library.book"
    ...

class Author(models.Model):
    _name = "library.author"
    ...
```

**CORRECT:** Separate files:
- `models/library_book.py` contains `library.book`
- `models/library_author.py` contains `library.author`
- `models/__init__.py` imports both

**Why:** OCA requires one model per file for maintainability. The filename matches the model technical name (dots to underscores).

### `_description` is REQUIRED

**WRONG:**
```python
class LibraryBook(models.Model):
    _name = "library.book"
    # No _description
```

**CORRECT:**
```python
class LibraryBook(models.Model):
    _name = "library.book"
    _description = "Library Book"
```

**Why:** pylint-odoo W8150 flags missing `_description`. It is used in the UI (e.g., "Create a new Library Book") and for admin clarity.

### Set `_order` and `_rec_name`

- **`_order`**: Always set explicitly (e.g., `_order = "name, create_date desc"`). Without it, records sort by `id` (insertion order), which is unpredictable.
- **`_rec_name`**: Set if the display field is not `name` (e.g., `_rec_name = "number"`). Odoo uses it for Many2one dropdowns and breadcrumbs.

## Changed in 17.0

| What Changed | Before (16.0 and earlier) | Now (17.0) | Notes |
|-------------|---------------------------|------------|-------|
| `attrs` in views | `attrs="{'invisible': [('state','=','draft')]}"` | `invisible="state == 'draft'"` | Python expression, not domain |
| `states` attribute | `states="draft"` on buttons | `invisible="state != 'draft'"` | Attribute completely removed |
| `column_invisible` | `attrs="{'column_invisible':...}"` | `column_invisible="parent.show_col"` | New dedicated attribute |
| `<chatter/>` | Explicit `mail.thread` fields in XML | `<chatter/>` shorthand | Simpler XML |
| `@api.model_create_multi` | Optional | Preferred for `create()` overrides | Batch-first pattern |
| Company mixin | `_inherit = ['mail.thread']` + manual | `_inherit = ['mail.thread', 'mail.activity.mixin']` | Activity mixin now standard |

## pylint-odoo Rules

| Rule | Trigger | Fix |
|------|---------|-----|
| **W8120** | Missing `_description` on model with `_name` | Add `_description = "Human Readable Name"` |
| **W8150** | Use of `_columns` or `_defaults` (old API) | Use `name = fields.Char(...)` new-API field definitions |
| **W8105** | Model class missing both `_inherit` and `_name` | Add `_name = "my.model"` or `_inherit = "existing.model"` |
| **R8110** | `@api.returns` decorator present | Remove decorator -- return values directly |

## Changed in 18.0

| What Changed | Before (17.0) | Now (18.0) | Impact |
|-------------|---------------|------------|--------|
| `states=` parameter | Allowed on field definitions (though discouraged) | **REMOVED** entirely | **Breaking** -- field definitions with `states=` cause errors |
| `group_operator=` | Correct name for aggregation | Renamed to `aggregator=` | **Silent failure** -- aggregation stops working if using old name |
| `_name_search()` | Override for custom search | Replaced by `_search_display_name()` | **Breaking** -- custom `_name_search()` overrides are ignored |
| `name_get()` | Override for display name | Deprecated -- use `display_name` compute field | **Deprecation** -- still works but will be removed |
| `check_access_rights()` + `check_access_rule()` | Separate methods | Consolidated into `record.check_access()` | **Breaking** -- old methods deprecated |
| `numbercall` on `ir.cron` | Field available for limiting cron runs | **REMOVED** from `ir.cron` model | **Breaking** -- data files with `numbercall` cause errors |

### `states=` parameter REMOVED

**WRONG (causes error in 18.0):**
```python
amount = fields.Float(
    string="Amount",
    states={"posted": [("readonly", True)]},
)
```

**CORRECT (18.0):**
```python
amount = fields.Float(string="Amount")
# Handle conditional readonly in XML view:
# <field name="amount" readonly="state == 'posted'"/>
```

**Why:** Odoo 18 completely removed the `states` parameter from Python field definitions. Conditional field behavior must be handled entirely in XML views using inline modifier expressions.

### `group_operator` renamed to `aggregator`

**WRONG (silent failure in 18.0):**
```python
quantity = fields.Float(string="Quantity", group_operator="avg")
```

**CORRECT (18.0):**
```python
quantity = fields.Float(string="Quantity", aggregator="avg")
```

**Why:** Renamed for clarity. The `group_operator` parameter is silently ignored in 18.0, causing aggregation in list views to stop working.

### `_name_search()` replaced by `_search_display_name()`

**WRONG (ignored in 18.0):**
```python
@api.model
def _name_search(self, name, domain=None, operator='ilike', limit=100, order=None):
    ...
```

**CORRECT (18.0):**
```python
@api.model
def _search_display_name(self, name, domain=None, operator='ilike', limit=100, order=None):
    ...
```

**Why:** The method was replaced to support broader search capabilities. Custom `_name_search()` overrides are silently ignored in 18.0.

## Changed in 19.0

| What Changed | Before (18.0) | Now (19.0) | Impact |
|-------------|---------------|------------|--------|
| `name_get()` | Deprecated (still works) | **Fully removed** | **Breaking** -- any `name_get()` override causes `AttributeError` |
| `read_group()` | Public API method | **Replaced** by `_read_group()` and `formatted_read_group()` | **Breaking** -- direct `read_group()` calls fail |
| `@api.returns` | Available (deprecated) | **Removed** | **Breaking** -- decorator causes `AttributeError` |
| `_sql_constraints` | Standard list of tuples | `models.Constraint` class recommended | **Recommendation** -- old syntax still works but new pattern preferred |
| Domain expressions | `from odoo.osv import expression` + `expression.OR(...)` | `from odoo.fields import Domain` + `Domain(...) \| Domain(...)` | **New API** -- old import still works but new `Domain` class preferred |

### `name_get()` fully removed -- use `_compute_display_name()`

**WRONG (causes error in 19.0):**
```python
def name_get(self):
    result = []
    for record in self:
        result.append((record.id, f"[{record.code}] {record.name}"))
    return result
```

**CORRECT (19.0):**
```python
@api.depends("code", "name")
def _compute_display_name(self):
    for record in self:
        record.display_name = f"[{record.code}] {record.name}"
```

**Why:** `name_get()` was deprecated in 18.0 and fully removed in 19.0. Use the `display_name` computed field with `_compute_display_name()` instead. This method is a standard compute method decorated with `@api.depends`.

### `read_group()` replaced by `_read_group()` and `formatted_read_group()`

**WRONG (fails in 19.0):**
```python
results = self.env["sale.order"].read_group(
    domain=[("state", "=", "sale")],
    fields=["partner_id", "amount_total:sum"],
    groupby=["partner_id"],
)
```

**CORRECT (19.0):**
```python
results = self.env["sale.order"]._read_group(
    domain=[("state", "=", "sale")],
    groupby=["partner_id"],
    aggregates=["amount_total:sum"],
)
```

**Why:** The public `read_group()` method was replaced. Use `_read_group()` for internal Python code or `formatted_read_group()` when you need the legacy dict-style output format.

### `models.Constraint` recommended for SQL constraints

**Old pattern (still works):**
```python
_sql_constraints = [
    ("code_unique", "UNIQUE(code)", "The code must be unique."),
]
```

**New pattern (19.0 recommended):**
```python
from odoo import models

code_unique = models.Constraint(
    "UNIQUE(code)",
    "The code must be unique.",
)
```

**Why:** The `models.Constraint` class provides a more structured and extensible way to define SQL constraints. The old `_sql_constraints` list-of-tuples pattern still works but the new class is preferred for new modules.

### Domain Construction

#### Simple Domains (all versions)
```python
# List syntax works in all Odoo versions
domain = [('state', '=', 'draft'), ('active', '=', True)]
records = self.env['sale.order'].search(domain)
```

#### Complex Domains — OR/AND Composition

WRONG (Odoo 19.0 — deprecated):
```python
from odoo.osv.expression import OR, AND
domain = OR([[('state', '=', 'draft')], [('state', '=', 'sent')]])
combined = AND([domain, [('active', '=', True)]])
```

CORRECT (Odoo 19.0):
```python
from odoo.fields import Domain
domain = Domain("state", "=", "draft") | Domain("state", "=", "sent")
combined = domain & Domain("active", "=", True)
```

CORRECT (Odoo 17.0-18.0 — expression module still available):
```python
from odoo.osv import expression
domain = expression.OR([[('state', '=', 'draft')], [('state', '=', 'sent')]])
```

Note: Simple list syntax `[('field', 'op', value)]` remains valid in all versions.
Use `Domain` class only when composing OR/AND logic in Odoo 19.0+.

### @api.private (Odoo 19.0+)

Marks methods as non-RPC-callable. Prevents external API access to internal logic.

**WRONG (Odoo 19.0 -- internal method exposed to RPC):**
```python
def _prepare_invoice_values(self):
    return {'partner_id': self.partner_id.id}
```

**CORRECT (Odoo 19.0 -- internal method protected):**
```python
@api.private
def _prepare_invoice_values(self):
    return {'partner_id': self.partner_id.id}
```

**Why:** Without `@api.private`, any method starting with `_` can still be called via XML-RPC or JSON-RPC by external integrations. `@api.private` blocks RPC access entirely, hardening internal logic against unauthorized external calls.

**Do NOT use `@api.private` on:**
- ORM overrides: `create()`, `write()`, `unlink()`
- Computed field methods with `@api.depends`
- Constraint methods with `@api.constrains`
- Onchange methods with `@api.onchange`
- Methods decorated with `@api.model` or `@api.model_create_multi`
- Methods called by XML actions or buttons (these need RPC access)
- ORM hooks: `_search_display_name()`, `_compute_display_name()`
- Cached methods with `@tools.ormcache`

**Use `@api.private` on:**
- `_prepare_*` helper methods
- `_get_*` internal data retrieval
- `_audit_*` internal audit helpers
- `_webhook_*` internal extension points
- `_post_*` internal processing hooks
- Any standalone `_check_*` validator not decorated with `@api.constrains`

---
*Odoo 17.0/18.0/19.0 Models & ORM -- loaded by model generation agents*
