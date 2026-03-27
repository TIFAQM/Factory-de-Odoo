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
import warnings
from difflib import get_close_matches
from typing import Any, Literal

logger = logging.getLogger(__name__)

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# ---------------------------------------------------------------------------
# Re-export inner models for backward compatibility
# ---------------------------------------------------------------------------

from amil_utils.spec_schema_inner import (  # noqa: E402, F401
    VALID_FIELD_TYPES,
    ApprovalLevelSpec,
    ApprovalSpec,
    BulkOperationSpec,
    BulkWizardFieldSpec,
    ChainSpec,
    ChainStepSpec,
    ConstraintSpec,
    ExtensionComputedSpec,
    ExtensionConstraintSpec,
    ExtensionFieldSpec,
    ExtensionMethodSpec,
    ExtensionSpec,
    FieldSpec,
    MigrationOp,
    MigrationSpec,
    PortalActionSpec,
    PortalFilterSpec,
    PortalPageSpec,
    PortalSpec,
    RelatedCountSpec,
    SecurityACLSpec,
    SecurityBlockSpec,
    ServerActionSpec,
    ViewExtensionSpec,
    ViewHintSpec,
    ViewInsertionSpec,
    WebhookSpec,
    WorkflowSpec,
    WorkflowTransitionSpec,
    _MODULE_NAME_RE,
    _validate_field_type_value,
)

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
