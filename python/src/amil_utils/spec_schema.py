"""Pydantic v2 spec schema for Odoo module validation.

Defines typed models mirroring the spec JSON hierarchy:
ModuleSpec > ModelSpec > FieldSpec + supporting specs.

``ModuleSpec`` uses ``extra='forbid'`` to reject unknown/typo'd keys.
Inner models use ``extra='allow'`` with a ``_warn_unknown_keys`` validator
that emits warnings for likely typos (using difflib close-match detection).
All models use ``protected_namespaces=()`` to avoid conflicts with Odoo's
``model_`` prefixed field names.

Usage::

    from amil_utils.spec_schema import validate_spec
    spec = validate_spec(raw_dict)  # Returns ModuleSpec or raises
"""

from __future__ import annotations

import logging
import re
import warnings
from difflib import get_close_matches
from typing import Any, Literal

logger = logging.getLogger(__name__)

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# ---------------------------------------------------------------------------
# Valid Odoo field types (16 total)
# ---------------------------------------------------------------------------

_MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

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

# ---------------------------------------------------------------------------
# Chain-level specs (Phase 61)
# ---------------------------------------------------------------------------


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
        if v not in VALID_FIELD_TYPES:
            valid_sorted = ", ".join(sorted(VALID_FIELD_TYPES))
            raise ValueError(
                f"Value '{v}' is not a valid field type. "
                f"Valid types: {valid_sorted}"
            )
        return v


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
        if v not in VALID_FIELD_TYPES:
            valid_sorted = ", ".join(sorted(VALID_FIELD_TYPES))
            raise ValueError(
                f"Value '{v}' is not a valid field type. "
                f"Valid types: {valid_sorted}"
            )
        return v


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


# ---------------------------------------------------------------------------
# Model-level spec
# ---------------------------------------------------------------------------


class ModelSpec(BaseModel):
    """Specification for a single Odoo model.

    Uses ``extra='allow'`` so Odoo-specific extra keys are preserved, but
    warns on likely typos via ``_warn_unknown_keys``.
    """

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    description: str = ""
    fields: list[FieldSpec] = []
    security: SecurityBlockSpec | None = None
    approval: ApprovalSpec | None = None
    webhooks: WebhookSpec | None = None
    constraints: list[ConstraintSpec] = []
    chatter: bool | None = None
    hierarchical: bool = False
    inherit: str | None = None
    audit: bool = False
    audit_exclude: list[str] = []
    import_export: bool = False
    transient: bool = False
    bulk: bool = False
    cacheable: bool = False
    archival: bool = False
    no_active: bool = False
    record_rules: list[str] | None = None
    related_counts: list[RelatedCountSpec] = []
    server_actions: list[ServerActionSpec] = []
    display_name_pattern: str | None = None
    expected_examples: list[dict] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _warn_unknown_keys(cls, values: Any) -> Any:
        """Emit a warning for unknown keys that look like typos of known fields."""
        if not isinstance(values, dict):
            return values
        known = set(cls.model_fields.keys())
        unknown = set(values.keys()) - known
        for key in unknown:
            close = get_close_matches(key, known, n=1, cutoff=0.85)
            if close:
                warnings.warn(
                    f"Unknown key '{key}' in {cls.__name__}"
                    f" — did you mean '{close[0]}'?",
                    UserWarning,
                    stacklevel=2,
                )
        return values


# ---------------------------------------------------------------------------
# Supporting top-level specs
# ---------------------------------------------------------------------------


class CronJobSpec(BaseModel):
    """Specification for a scheduled action (cron job)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    model: str = ""
    method: str
    interval_number: int = 1
    interval_type: str = "days"


class ReportSpec(BaseModel):
    """Specification for a QWeb report."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    model: str = ""
    report_type: str = "qweb-pdf"
    template: str = ""
    xml_id: str = ""


# ---------------------------------------------------------------------------
# OWL component specs (NEW-01)
# ---------------------------------------------------------------------------


class OWLComponentSpec(BaseModel):
    """Specification for an OWL frontend component."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    type: Literal["field_widget", "systray", "client_action", "view"] = "field_widget"
    description: str = ""



# ---------------------------------------------------------------------------
# Settings specs (NEW-08)
# ---------------------------------------------------------------------------


class SettingSpec(BaseModel):
    """Specification for an ir.config_parameter setting exposed via res.config.settings."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: str
    type: Literal["Boolean", "Char", "Integer", "Selection"] = "Boolean"
    default: str | bool | int = ""
    description: str = ""
    group: str = "general"


# ---------------------------------------------------------------------------
# Module-level spec (root)
# ---------------------------------------------------------------------------


class ModuleSpec(BaseModel):
    """Root specification for an Odoo module.

    Uses ``extra='forbid'`` so that typos in top-level keys are immediately
    rejected by Pydantic validation rather than silently accepted.

    Cross-reference validators check:
    - Approval level roles exist in per-model security.roles
    - audit_exclude fields exist in per-model field lists
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    module_name: str
    module_title: str = ""
    odoo_version: str = "19.0"
    version: str = ""
    summary: str = ""
    author: str = ""
    website: str = ""
    license: str = "LGPL-3"
    category: str = "Uncategorized"
    application: bool = True
    depends: list[str] = ["base"]
    models: list[ModelSpec] = []
    extends: list[ExtensionSpec] = []
    wizards: list[dict] = []
    cron_jobs: list[CronJobSpec] = []
    reports: list[ReportSpec] = []
    controllers: list[dict] | None = None
    portal: PortalSpec | None = None
    bulk_operations: list[BulkOperationSpec] = []
    owl_components: list[OWLComponentSpec] = []
    dashboards: list[dict] = []
    relationships: list[dict] = []
    computation_chains: list[dict] = []
    workflow: list[WorkflowSpec] = []
    business_rules: list[str] = []
    view_hints: list[ViewHintSpec] = []
    constraints: list[dict] = []
    security: SecurityBlockSpec | None = None
    settings: list[SettingSpec] = []
    migrations: list[MigrationSpec] = []
    multi_company: bool = False
    notifications: list[dict] = []
    localization: str | None = None
    document_management: bool = False
    document_config: dict = {}
    academic_calendar: bool = False
    academic_config: dict = {}

    @field_validator("module_name")
    @classmethod
    def validate_module_name_pattern(cls, v: str) -> str:
        """Validate module_name matches Odoo naming conventions.

        Prevents path traversal (``../../../tmp/evil``) and enforces
        lowercase + underscores only. Same regex as docker_runner.py.
        """
        if not _MODULE_NAME_RE.fullmatch(v):
            raise ValueError(
                f"module_name '{v}' must match [a-z][a-z0-9_]* "
                "(lowercase, underscores only, starts with letter). "
                "This prevents path traversal and aligns with Odoo conventions."
            )
        return v

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_with_suggestions(cls, values: Any) -> Any:
        """Provide helpful 'did you mean?' suggestions for unknown keys.

        Runs before Pydantic's ``extra='forbid'`` check so the error
        message includes the closest valid field name.
        """
        if not isinstance(values, dict):
            return values
        known = set(cls.model_fields.keys())
        unknown = set(values.keys()) - known
        if not unknown:
            return values
        messages: list[str] = []
        for key in sorted(unknown):
            close = get_close_matches(key, known, n=1, cutoff=0.6)
            if close:
                messages.append(
                    f"Unknown key '{key}' — did you mean '{close[0]}'?"
                )
            else:
                messages.append(f"Unknown key '{key}' is not a valid field.")
        raise ValueError(
            f"ModuleSpec received unknown key(s): {'; '.join(messages)}"
        )

    @model_validator(mode="after")
    def check_no_duplicate_extends(self) -> ModuleSpec:
        """Reject duplicate base_model entries in extends list."""
        seen: set[str] = set()
        for ext in self.extends:
            if ext.base_model in seen:
                raise ValueError(
                    f"duplicate base_model '{ext.base_model}' in extends list"
                )
            seen.add(ext.base_model)
        return self

    @model_validator(mode="after")
    def check_approval_roles_exist(self) -> ModuleSpec:
        """Verify approval level roles reference defined security roles."""
        for model in self.models:
            if not model.approval or not model.security:
                continue
            defined_roles = set(model.security.roles)
            for level in (model.approval.levels or []):
                if level.role and level.role not in defined_roles:
                    raise ValueError(
                        f"Approval role '{level.role}' in model '{model.name}' "
                        f"not found in security.roles: {sorted(defined_roles)}"
                    )
        return self

    @model_validator(mode="after")
    def check_audit_exclude_fields(self) -> ModuleSpec:
        """Verify audit_exclude references actual field names."""
        for model in self.models:
            if not model.audit or not model.audit_exclude:
                continue
            field_names = {f.name for f in model.fields}
            for excluded in model.audit_exclude:
                if excluded not in field_names:
                    raise ValueError(
                        f"audit_exclude field '{excluded}' in model "
                        f"'{model.name}' not found in model fields: "
                        f"{sorted(field_names)}"
                    )
        return self


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_spec(raw_spec: dict[str, Any]) -> ModuleSpec:
    """Validate a raw spec dict against the Pydantic schema.

    Returns a ``ModuleSpec`` instance with defaults filled.
    Raises ``ValidationError`` on invalid input (hard fail).
    """
    try:
        return ModuleSpec(**raw_spec)
    except ValidationError as exc:
        module_name = raw_spec.get("module_name", "unknown")
        formatted = format_validation_errors(exc, module_name)
        logger.error(formatted)
        raise


def format_validation_errors(exc: ValidationError, module_name: str) -> str:
    """Format a ``ValidationError`` into human-readable output.

    Output format::

        Spec validation failed for {module_name}:
            {loc}
              {msg}
              Got: {input!r}
    """
    lines = [f"Spec validation failed for {module_name}:"]
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        msg = error["msg"]
        inp = error.get("input", "")
        lines.append(f"    {loc}")
        lines.append(f"      {msg}")
        if inp:
            lines.append(f"      Got: {inp!r}")
    return "\n".join(lines)
