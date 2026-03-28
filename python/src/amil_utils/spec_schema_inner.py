"""Inner model definitions for the Odoo module spec schema.

Contains leaf-level and subordinate Pydantic models used by the top-level
specs in ``spec_schema.py``: field specs, extension specs, bulk operation
specs, portal specs, workflow specs, security specs, and related helpers.

These models are re-exported from ``spec_schema`` for backward compatibility.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from amil_utils.validation.module_name import MODULE_NAME_RE as _MODULE_NAME_RE

VALID_FIELD_TYPES: frozenset[str] = frozenset({
    "Char",
    "Text",
    "Html",
    "Integer",
    "Float",
    "Monetary",
    "Boolean",
    "Date",
    "Datetime",
    "Binary",
    "Selection",
    "Many2one",
    "One2many",
    "Many2many",
    "Many2oneReference",
    "Json",
})


def _validate_field_type_value(v: str) -> str:
    """Shared field type validator for FieldSpec and ExtensionFieldSpec."""
    if v not in VALID_FIELD_TYPES:
        valid_sorted = ", ".join(sorted(VALID_FIELD_TYPES))
        raise ValueError(
            f"Value '{v}' is not a valid field type. "
            f"Valid types: {valid_sorted}"
        )
    return v


# ---------------------------------------------------------------------------
# Bulk operation specs (Phase 63)
# ---------------------------------------------------------------------------


class BulkWizardFieldSpec(BaseModel):
    """Specification for an extra wizard field in a bulk operation."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    type: str
    required: bool = False
    comodel: str | None = None


class BulkOperationSpec(BaseModel):
    """Specification for a single bulk operation (state_transition, create_related, update_fields)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    id: str
    name: str
    source_model: str
    wizard_model: str
    operation: str  # state_transition | create_related | update_fields
    source_domain: list = []
    target_state: str | None = None
    action_method: str | None = None
    create_model: str | None = None
    create_fields: dict[str, str] = {}
    wizard_fields: list[BulkWizardFieldSpec] = []
    preview_fields: list[str] = []
    side_effects: list[str] = []
    batch_size: int | None = None
    allow_partial: bool = True

    @field_validator("operation")
    @classmethod
    def validate_operation_type(cls, v: str) -> str:
        allowed = {"state_transition", "create_related", "update_fields"}
        if v not in allowed:
            raise ValueError(
                f"operation must be one of {sorted(allowed)}, got '{v}'"
            )
        return v


# ---------------------------------------------------------------------------
# Portal-level specs (Phase 62)
# ---------------------------------------------------------------------------


class PortalActionSpec(BaseModel):
    """Specification for a portal page detail action (e.g., report download)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    label: str = ""
    type: str = "report"
    report_ref: str = ""
    states: list[str] = []


class PortalFilterSpec(BaseModel):
    """Specification for a portal page filter."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    field: str
    label: str = ""


class PortalPageSpec(BaseModel):
    """Specification for a single portal page (detail or list)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    id: str
    type: str
    model: str
    route: str
    title: str = ""
    ownership: str
    fields_visible: list[str] = []
    fields_editable: list[str] = []
    list_fields: list[str] = []
    detail_route: str | None = None
    detail_fields: list[str] = []
    detail_actions: list[PortalActionSpec] = []
    filters: list[PortalFilterSpec] = []
    default_sort: str = "id desc"
    show_in_home: bool = True
    home_icon: str = "fa fa-file"
    home_counter: bool = False
    counter_domain: list | None = None

    @field_validator("type")
    @classmethod
    def validate_page_type(cls, v: str) -> str:
        allowed = {"detail", "list"}
        if v not in allowed:
            raise ValueError(
                f"Portal page type must be one of {sorted(allowed)}, got '{v}'"
            )
        return v


class PortalSpec(BaseModel):
    """Specification for the portal section of a module spec."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    pages: list[PortalPageSpec] = []
    auth: str = "portal"
    menu_label: str = "Portal"


# ---------------------------------------------------------------------------
# Website-level specs (Phase F16)
# ---------------------------------------------------------------------------


class WebsitePageSpec(BaseModel):
    """A single public website page."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    id: str
    url: str
    title: str
    type: str = "list"  # list, detail, static
    model: str | None = None
    fields_visible: list[str] = Field(default_factory=list)
    published: bool = True
    seo_title: str | None = None
    seo_description: str | None = None
    show_in_menu: bool = True
    menu_sequence: int = 50

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("list", "detail", "static"):
            raise ValueError(
                f"type must be 'list', 'detail', or 'static', got '{v}'"
            )
        return v


class WebsiteSpec(BaseModel):
    """Website section of a module spec."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    pages: list[WebsitePageSpec] = Field(default_factory=list)
    default_auth: str = "public"


# ---------------------------------------------------------------------------
# Chain-level specs (Phase 61)
# ---------------------------------------------------------------------------


class ChainStepSpec(BaseModel):
    """Specification for a single step in a computation chain."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    model: str
    field: str
    type: str
    source: str  # direct_input | lookup | computation | aggregation
    depends: list[str] = []
    description: str = ""
    aggregation: str | None = None
    lookup_table: dict[str, float] | None = None
    digits: list[int] | None = None


class ChainSpec(BaseModel):
    """Specification for a named computation chain with ordered steps."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    chain_id: str
    description: str = ""
    steps: list[ChainStepSpec] = []


# ---------------------------------------------------------------------------
# Leaf-level specs
# ---------------------------------------------------------------------------


class FieldSpec(BaseModel):
    """Specification for a single Odoo model field."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    type: str
    string: str = ""
    required: bool = False
    readonly: bool = False
    index: bool = False
    store: bool | None = None
    default: Any = None
    compute: str | None = None
    depends: list[str] = []
    onchange: str | None = None
    constrains: list[str] | None = None
    selection: list = []
    comodel_name: str | None = None
    inverse_name: str | None = None
    ondelete: str = "set null"
    tracking: bool = False
    groups: str | None = None
    sensitive: bool = False
    internal: bool = False

    @field_validator("type")
    @classmethod
    def validate_field_type(cls, v: str) -> str:
        return _validate_field_type_value(v)


class ConstraintSpec(BaseModel):
    """Specification for a model constraint (check, unique, exclude)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    type: str
    expression: str = ""
    message: str = ""


class WebhookSpec(BaseModel):
    """Specification for model webhook configuration."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    watched_fields: list[str] = []
    on_create: bool = False
    on_write: list[str] = []
    on_unlink: bool = False


class ApprovalLevelSpec(BaseModel):
    """Specification for one approval workflow level."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str = ""
    role: str = ""
    state: str = ""
    group: str | None = None


class ApprovalSpec(BaseModel):
    """Specification for an approval workflow."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    levels: list[ApprovalLevelSpec] = []
    on_reject: str = "draft"


class SecurityACLSpec(BaseModel):
    """CRUD permission set for a single role."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    create: bool = True
    read: bool = True
    write: bool = True
    unlink: bool = True


class SecurityBlockSpec(BaseModel):
    """Security configuration block (model-level or module-level)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    roles: list[str] = []
    acl: dict[str, SecurityACLSpec] = {}
    defaults: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Extension-level specs (Phase 59)
# ---------------------------------------------------------------------------


class ExtensionFieldSpec(BaseModel):
    """Specification for a field added by an extension module (_inherit)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    type: str
    string: str = ""
    comodel: str | None = None
    comodel_name: str | None = None
    selection: list = []
    values: list | None = None  # Alias for selection; preprocessor normalizes
    required: bool = False
    store: bool | None = None
    compute: str | None = None
    depends: list[str] = []
    groups: str | None = None
    inverse_name: str | None = None

    @field_validator("type")
    @classmethod
    def validate_field_type(cls, v: str) -> str:
        return _validate_field_type_value(v)


class ViewInsertionSpec(BaseModel):
    """Specification for a single xpath insertion in a view extension."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    xpath: str
    position: str = "after"
    fields: list[str] = []
    content: str | None = None  # e.g., "page" for Pattern B
    page_name: str | None = None
    page_string: str | None = None


class ViewExtensionSpec(BaseModel):
    """Specification for extending a base view with xpath insertions."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    base_view: str
    insertions: list[ViewInsertionSpec] = []


class ExtensionComputedSpec(BaseModel):
    """Specification for a computed field added by an extension."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    type: str
    compute: str
    depends: list[str] = []
    store: bool = False


class ExtensionConstraintSpec(BaseModel):
    """Specification for a constraint added by an extension."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    fields: list[str] = []
    rule: str = ""
    type: str = "check"


class ExtensionMethodSpec(BaseModel):
    """Specification for a method added by an extension."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    decorator: str | None = None
    business_rules: list[str] = []


class ExtensionSpec(BaseModel):
    """Specification for extending an existing Odoo model via _inherit."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    base_model: str
    base_module: str
    add_fields: list[ExtensionFieldSpec] = []
    add_computed: list[ExtensionComputedSpec] = []
    add_constraints: list[ExtensionConstraintSpec] = []
    add_methods: list[ExtensionMethodSpec] = []
    view_extensions: list[ViewExtensionSpec] = []


# ---------------------------------------------------------------------------
# Workflow specs
# ---------------------------------------------------------------------------


class WorkflowTransitionSpec(BaseModel):
    """A single state transition in a workflow."""

    model_config = ConfigDict(extra="allow", protected_namespaces=(), populate_by_name=True)

    from_state: str = Field("", alias="from")
    to_state: str = Field("", alias="to")
    action: str = ""
    conditions: str = ""


class WorkflowSpec(BaseModel):
    """State machine definition for a model."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    model: str = ""
    states: list[str] = []
    transitions: list[WorkflowTransitionSpec] = []


class ViewHintSpec(BaseModel):
    """Layout guidance for a model's views."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    model: str = ""
    view_type: str = "form"
    key_fields: list[str] = []
    notes: str = ""


# ---------------------------------------------------------------------------
# Related count / stat button specs (TMPL-01)
# ---------------------------------------------------------------------------


class RelatedCountSpec(BaseModel):
    """Specification for a related record count stat button."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    field: str  # e.g., "invoice_count"
    comodel: str  # e.g., "account.move"
    domain_field: str  # e.g., "partner_id"
    icon: str = "fa-list"
    label: str = ""


# ---------------------------------------------------------------------------
# Server action specs (NEW-03)
# ---------------------------------------------------------------------------


class ServerActionSpec(BaseModel):
    """Specification for a server action bound to a model's list view."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    label: str
    method: str


# ---------------------------------------------------------------------------
# Migration specs (NEW-06)
# ---------------------------------------------------------------------------


class MigrationOp(BaseModel):
    """A single migration operation (rename, add, drop, sql)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    type: Literal[
        "rename_field", "add_column", "drop_column", "rename_model", "sql"
    ] = "rename_field"
    model: str = ""
    old_name: str = ""
    new_name: str = ""
    sql: str = ""


class MigrationSpec(BaseModel):
    """Specification for a versioned migration with ordered operations."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    from_version: str
    to_version: str
    operations: list[MigrationOp] = []
