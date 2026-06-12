"""Document management domain preprocessor for Odoo module generation.

Generates ``document.type`` and ``document.document`` model dicts when
``document_management: true`` is present in the spec.  Configurable via
optional ``document_config`` dict (enable_versioning, enable_verification,
default_types).

Registered at order=28 (after academic_calendar=27, before constraints=30).

Phase 52 -- DOMN-01.
"""

from __future__ import annotations

from typing import Any

from amil_utils.preprocessors._registry import register_preprocessor


# -- String constants for constraint check_body --------------------------------

_FILE_VALIDATION_BODY = """\
for rec in self:
    if not rec.file or not rec.document_type_id:
        continue
    import base64
    file_size_bytes = len(base64.b64decode(rec.file))
    max_bytes = rec.document_type_id.max_file_size * 1024 * 1024
    if file_size_bytes > max_bytes:
        raise ValidationError(
            _("File size (%d KB) exceeds the maximum allowed size of %d MB for document type '%s'.")
            % (file_size_bytes // 1024, rec.document_type_id.max_file_size, rec.document_type_id.name)
        )
    allowed = rec.document_type_id.allowed_mime_types
    if allowed and rec.mime_type:
        allowed_list = [m.strip() for m in allowed.split(',')]
        if rec.mime_type not in allowed_list:
            raise ValidationError(
                _("File type '%s' is not allowed for document type '%s'. Allowed types: %s")
                % (rec.mime_type, rec.document_type_id.name, allowed)
            )"""

_ACTION_VERIFY_BODY = """\
\"\"\"Verify the document as authentic.\"\"\"
self.ensure_one()
if self.verification_state == 'verified':
    raise UserError(
        _("Document '%s' is already verified. Reset to pending first.") % self.name
    )
self.write({
    'verification_state': 'verified',
    'verified_by': self.env.uid,
    'verified_date': fields.Datetime.now(),
    'rejection_reason': False,
})"""

_ACTION_REJECT_BODY = """\
\"\"\"Reject the document.\"\"\"
self.ensure_one()
if not self.rejection_reason:
    raise UserError(
        _("Please provide a rejection_reason before rejecting the document.")
    )
self.write({
    'verification_state': 'rejected',
})"""

_ACTION_RESET_BODY = """\
\"\"\"Reset the document to pending state.\"\"\"
self.ensure_one()
self.write({
    'verification_state': 'pending',
    'verified_by': False,
    'verified_date': False,
    'rejection_reason': False,
})"""

_ACTION_UPLOAD_NEW_VERSION_BODY = """\
\"\"\"Upload a new version of the document.\"\"\"
self.ensure_one()
self.write({'is_latest': False})
new_doc = self.copy({
    'version': self.version + 1,
    'previous_version_id': self.id,
    'is_latest': True,
    'verification_state': 'pending',
    'verified_by': False,
    'verified_date': False,
    'rejection_reason': False,
    'file': False,
})
return {
    'type': 'ir.actions.act_window',
    'res_model': self._name,
    'res_id': new_doc.id,
    'view_mode': 'form',
    'target': 'current',
}"""


# -- Builder functions ---------------------------------------------------------


def _build_document_type_model() -> dict[str, Any]:
    """Build a complete model dict for document.type."""
    return {
        "name": "document.type",
        "description": "Document Type",
        "model_order": "sequence, name",
        "fields": [
            {
                "name": "name",
                "type": "Char",
                "string": "Document Type",
                "required": True,
            },
            {
                "name": "code",
                "type": "Char",
                "string": "Code",
                "required": True,
            },
            {
                "name": "required_for",
                "type": "Selection",
                "string": "Required For",
                "default": "admission",
                "selection": [
                    ("admission", "Admission"),
                    ("enrollment", "Enrollment"),
                    ("graduation", "Graduation"),
                    ("employment", "Employment"),
                    ("always", "Always"),
                ],
            },
            {
                "name": "max_file_size",
                "type": "Integer",
                "string": "Max File Size (MB)",
                "default": 5,
            },
            {
                "name": "allowed_mime_types",
                "type": "Char",
                "string": "Allowed MIME Types",
                "default": "application/pdf,image/jpeg,image/png",
            },
            {
                "name": "sequence",
                "type": "Integer",
                "string": "Sequence",
                "default": 10,
            },
            {
                "name": "active",
                "type": "Boolean",
                "string": "Active",
                "default": True,
            },
            {
                "name": "description",
                "type": "Text",
                "string": "Description",
            },
        ],
        "sql_constraints": [
            {
                "name": "unique_code",
                "definition": "UNIQUE(code)",
                "message": "Document type code must be unique.",
            },
        ],
    }


def _build_document_document_model(
    config: dict[str, Any],
    module_name: str,
) -> dict[str, Any]:
    """Build a complete model dict for document.document.

    Respects enable_versioning and enable_verification config flags.
    """
    enable_versioning = config.get("enable_versioning", True)
    enable_verification = config.get("enable_verification", True)

    fields: list[dict[str, Any]] = [
        {
            "name": "name",
            "type": "Char",
            "string": "Document Name",
            "required": True,
            "tracking": True,
        },
        {
            "name": "document_type_id",
            "type": "Many2one",
            "comodel_name": "document.type",
            "string": "Document Type",
            "required": True,
        },
        {
            "name": "file",
            "type": "Binary",
            "string": "File",
            "required": True,
            "attachment": True,
        },
        {
            "name": "filename",
            "type": "Char",
            "string": "Filename",
        },
        {
            "name": "mime_type",
            "type": "Char",
            "string": "MIME Type",
            "readonly": True,
        },
        {
            "name": "file_size",
            "type": "Integer",
            "string": "File Size (KB)",
            "readonly": True,
        },
        {
            "name": "upload_date",
            "type": "Datetime",
            "string": "Upload Date",
            "default": "now",
            "readonly": True,
        },
        {
            "name": "res_model",
            "type": "Char",
            "string": "Resource Model",
            "index": True,
        },
        {
            "name": "res_id",
            "type": "Many2oneReference",
            "string": "Resource ID",
            "model_field": "res_model",
        },
    ]

    # Verification fields (conditional)
    if enable_verification:
        fields.extend([
            {
                "name": "verification_state",
                "type": "Selection",
                "string": "Verification State",
                "default": "pending",
                "tracking": True,
                "selection": [
                    ("pending", "Pending"),
                    ("verified", "Verified"),
                    ("rejected", "Rejected"),
                ],
            },
            {
                "name": "verified_by",
                "type": "Many2one",
                "comodel_name": "res.users",
                "string": "Verified By",
                "readonly": True,
            },
            {
                "name": "verified_date",
                "type": "Datetime",
                "string": "Verified Date",
                "readonly": True,
            },
            {
                "name": "rejection_reason",
                "type": "Text",
                "string": "Rejection Reason",
            },
        ])

    # Versioning fields (conditional)
    if enable_versioning:
        fields.extend([
            {
                "name": "version",
                "type": "Integer",
                "string": "Version",
                "default": 1,
                "readonly": True,
            },
            {
                "name": "previous_version_id",
                "type": "Many2one",
                "comodel_name": "document.document",
                "string": "Previous Version",
                "readonly": True,
            },
            {
                "name": "is_latest",
                "type": "Boolean",
                "string": "Is Latest",
                "default": True,
                "index": True,
            },
        ])

    # Classification fields
    enable_classification = config.get("enable_classification", True)
    if enable_classification:
        fields.extend([
            {
                "name": "classification",
                "type": "Selection",
                "string": "Classification",
                "default": "internal",
                "selection": [
                    ("public", "Public"),
                    ("internal", "Internal"),
                    ("confidential", "Confidential"),
                    ("restricted", "Restricted"),
                ],
                "tracking": True,
            },
            {
                "name": "access_groups",
                "type": "Char",
                "string": "Access Groups",
                "help": "Comma-separated group XML IDs for restricted access",
            },
        ])

    # Expiry fields
    enable_expiry = config.get("enable_expiry", True)
    if enable_expiry:
        fields.extend([
            {
                "name": "expiry_date",
                "type": "Date",
                "string": "Expiry Date",
                "tracking": True,
                "help": "Document expires after this date",
            },
            {
                "name": "is_expired",
                "type": "Boolean",
                "string": "Expired",
                "compute": "_compute_is_expired",
                "store": True,
                "depends": ["expiry_date"],
            },
            {
                "name": "renewal_reminder_days",
                "type": "Integer",
                "string": "Reminder (days before expiry)",
                "default": 30,
            },
        ])

    # Notes field (always present)
    fields.append({
        "name": "notes",
        "type": "Text",
        "string": "Notes",
    })

    # Complex constraints
    complex_constraints: list[dict[str, Any]] = [
        {
            "name": "doc_file_validation",
            "fields": ["file", "document_type_id"],
            "type": "doc_file_validation",
            "check_body": _FILE_VALIDATION_BODY,
        },
    ]

    if enable_verification:
        complex_constraints.extend([
            {
                "name": "doc_action_verify",
                "fields": ["verification_state"],
                "type": "doc_action_verify",
                "check_body": _ACTION_VERIFY_BODY,
            },
            {
                "name": "doc_action_reject",
                "fields": ["verification_state"],
                "type": "doc_action_reject",
                "check_body": _ACTION_REJECT_BODY,
            },
            {
                "name": "doc_action_reset",
                "fields": ["verification_state"],
                "type": "doc_action_reset",
                "check_body": _ACTION_RESET_BODY,
            },
        ])

    if enable_versioning:
        complex_constraints.append({
            "name": "doc_action_upload_new_version",
            "fields": ["version", "is_latest"],
            "type": "doc_action_upload_new_version",
            "check_body": _ACTION_UPLOAD_NEW_VERSION_BODY,
        })

    model: dict[str, Any] = {
        "name": "document.document",
        "description": "Document",
        "model_order": "create_date desc",
        "inherit": ["mail.thread"],
        "has_document_verification": enable_verification,
        "has_document_versioning": enable_versioning,
        "fields": fields,
        "complex_constraints": complex_constraints,
    }

    return model


def _inject_security_roles(
    spec: dict[str, Any],
    module_name: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return updated security_roles list with document management roles.

    Injects viewer/uploader/verifier/manager (with implied_ids hierarchy).
    Skips roles already present by name.
    When enable_verification=False, the verifier role is omitted and
    manager only implies uploader.

    Does NOT mutate the input list.
    """
    enable_verification = config.get("enable_verification", True)
    security_roles = list(spec.get("security_roles", []))
    role_names = {r["name"] for r in security_roles}

    viewer_xml_id = f"group_{module_name}_viewer"
    uploader_xml_id = f"group_{module_name}_uploader"
    verifier_xml_id = f"group_{module_name}_verifier"
    manager_xml_id = f"group_{module_name}_manager"

    if "viewer" not in role_names:
        security_roles.append({
            "name": "viewer",
            "label": "Document Viewer",
            "crud": "r",
            "can_create": False,
            "xml_id": viewer_xml_id,
            "implied_ids": [],
            "is_highest": False,
        })

    if "uploader" not in role_names:
        security_roles.append({
            "name": "uploader",
            "label": "Document Uploader",
            "crud": "cru",
            "can_create": True,
            "xml_id": uploader_xml_id,
            "implied_ids": [viewer_xml_id],
            "is_highest": False,
        })

    if enable_verification and "verifier" not in role_names:
        security_roles.append({
            "name": "verifier",
            "label": "Document Verifier",
            "crud": "ru",
            "can_create": False,
            "xml_id": verifier_xml_id,
            "implied_ids": [viewer_xml_id],
            "is_highest": False,
        })

    if "manager" not in role_names:
        manager_implied = [uploader_xml_id]
        if enable_verification:
            manager_implied.append(verifier_xml_id)
        security_roles.append({
            "name": "manager",
            "label": "Document Manager",
            "xml_id": manager_xml_id,
            "implied_ids": manager_implied,
            "is_highest": True,
        })

    return security_roles


# -- Main preprocessor --------------------------------------------------------


@register_preprocessor(order=28, name="document_management")
def _process_document_management(spec: dict[str, Any]) -> dict[str, Any]:
    """Generate document management models when ``document_management: true``.

    Generates ``document.type`` and ``document.document`` model dicts and
    appends them to the spec's ``models`` list.  Injects ``mail`` into spec
    ``depends`` for mail.thread mixin support.  Injects security roles
    (viewer/uploader/verifier/manager).

    Returns a new spec dict -- never mutates the input.
    """
    if not spec.get("document_management"):
        return spec

    module_name = spec.get("module_name", "module")
    config = spec.get("document_config", {})

    # Build new models list: preserve existing + append generated
    new_models = list(spec.get("models", []))
    new_models.append(_build_document_type_model())
    new_models.append(_build_document_document_model(config, module_name))

    new_spec: dict[str, Any] = {**spec, "models": new_models}

    # Inject mail dependency only when document model uses mail.thread
    doc_model = new_models[-1]  # document.document is last appended
    uses_mail = "mail.thread" in (doc_model.get("inherit") or [])
    if uses_mail:
        depends = list(new_spec.get("depends", []))
        if "mail" not in depends:
            depends.append("mail")
        new_spec["depends"] = depends

    # Inject security roles
    new_spec["security_roles"] = _inject_security_roles(
        spec, module_name, config,
    )

    # Handle default_types -> extra_data_files
    default_types = config.get("default_types")
    if default_types:
        extra_data_files = list(new_spec.get("extra_data_files", []))
        extra_data_files.append("data/document_type_data.xml")
        new_spec["extra_data_files"] = extra_data_files

    return new_spec
