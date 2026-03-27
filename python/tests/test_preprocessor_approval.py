"""Tests for approval workflow preprocessor."""

from __future__ import annotations

import pytest

from amil_utils.preprocessors.approval import _process_approval_patterns


def _make_spec(
    *,
    models=None,
    security_roles=None,
    module_name="test_module",
):
    """Build a minimal spec dict for testing."""
    return {
        "module_name": module_name,
        "models": models or [],
        "security_roles": security_roles or [],
    }


def _make_model(
    *,
    name="test.request",
    description="Test Request",
    fields=None,
    approval=None,
):
    """Build a minimal model dict for testing."""
    return {
        "name": name,
        "description": description,
        "fields": fields or [
            {"name": "name", "type": "Char"},
            {"name": "amount", "type": "Float"},
        ],
        "approval": approval,
    }


def _make_approval(*, levels=None, on_reject="draft", **kwargs):
    """Build a minimal approval block."""
    if levels is None:
        levels = [
            {
                "state": "manager_approved",
                "role": "manager",
                "next": "approved",
            },
        ]
    return {"levels": levels, "on_reject": on_reject, **kwargs}


class TestProcessApprovalPatterns:
    """Tests for _process_approval_patterns preprocessor."""

    def test_happy_path_single_level(self):
        """Single-level approval produces correct enrichment."""
        roles = [{"name": "manager", "label": "Manager"}]
        approval = _make_approval()
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)

        assert result is not spec, "Must return a new spec (immutability)"
        enriched = result["models"][0]

        # State field injected at position 0
        state_field = enriched["fields"][0]
        assert state_field["name"] == "state"
        assert state_field["type"] == "Selection"
        assert state_field["default"] == "draft"
        assert state_field["required"] is True
        # Selection includes draft + level + terminal
        selection_keys = [s[0] for s in state_field["selection"]]
        assert selection_keys == ["draft", "manager_approved", "approved"]

        # Model-level flags
        assert enriched["has_approval"] is True
        assert enriched["needs_translate"] is True
        assert enriched["has_write_override"] is True
        assert "approval" in enriched["override_sources"]["write"]

        # Submit action
        submit = enriched["approval_submit_action"]
        assert submit["name"] == "action_submit"
        assert submit["from_state"] == "draft"
        assert submit["to_state"] == "manager_approved"

        # Reject action
        reject = enriched["approval_reject_action"]
        assert reject["name"] == "action_reject"
        assert reject["to_state"] == "draft"

        # Reset action
        reset = enriched["approval_reset_action"]
        assert reset["name"] == "action_reset_to_draft"

        # Record rules
        assert len(enriched["approval_record_rules"]) == 2
        assert "approval" in enriched["record_rule_scopes"]

    def test_happy_path_multi_level(self):
        """Multi-level approval produces one action method per level."""
        roles = [
            {"name": "manager", "label": "Manager"},
            {"name": "director", "label": "Director"},
        ]
        levels = [
            {"state": "manager_approved", "role": "manager", "next": "director_approved"},
            {"state": "director_approved", "role": "director", "next": "approved"},
        ]
        approval = _make_approval(levels=levels)
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]

        # Two action methods
        methods = enriched["approval_action_methods"]
        assert len(methods) == 2
        assert methods[0]["name"] == "action_approve_manager_approved"
        assert methods[0]["from_state"] == "draft"
        assert methods[1]["name"] == "action_approve_director_approved"
        assert methods[1]["from_state"] == "manager_approved"

        # State selection: draft, manager_approved, director_approved, approved
        state_field = enriched["fields"][0]
        selection_keys = [s[0] for s in state_field["selection"]]
        assert selection_keys == [
            "draft", "manager_approved", "director_approved", "approved",
        ]

    def test_empty_spec_no_models(self):
        """Empty models list returns spec unchanged."""
        spec = _make_spec(models=[])

        result = _process_approval_patterns(spec)

        assert result is spec, "Should return same ref when no enrichment"

    def test_no_approval_models(self):
        """Models without approval blocks return spec unchanged."""
        model = _make_model(approval=None)
        spec = _make_spec(models=[model])

        result = _process_approval_patterns(spec)

        assert result is spec

    def test_empty_levels_skips_enrichment(self):
        """Model with approval block but empty levels is skipped with warning."""
        approval = {"levels": []}
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model])

        result = _process_approval_patterns(spec)

        enriched = result["models"][0]
        assert enriched.get("has_approval") is None

    def test_on_reject_rejected_adds_state(self):
        """on_reject='rejected' adds a rejected state to selection."""
        roles = [{"name": "manager", "label": "Manager"}]
        approval = _make_approval(on_reject="rejected")
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]

        state_field = enriched["fields"][0]
        selection_keys = [s[0] for s in state_field["selection"]]
        assert "rejected" in selection_keys
        assert enriched["on_reject"] == "rejected"

    def test_missing_role_raises_error(self):
        """Reference to non-existent role raises ValueError."""
        approval = _make_approval(levels=[
            {"state": "approved", "role": "nonexistent", "next": "done"},
        ])
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=[])

        with pytest.raises(ValueError, match="not found in security_roles"):
            _process_approval_patterns(spec)

    def test_explicit_group_skips_role_validation(self):
        """When level has explicit group, role lookup is skipped."""
        approval = _make_approval(levels=[
            {
                "state": "approved",
                "role": "nonexistent_role",
                "next": "done",
                "group": "custom.group_approver",
            },
        ])
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=[])

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]
        method = enriched["approval_action_methods"][0]
        assert method["group_xml_id"] == "custom.group_approver"

    def test_existing_state_field_replaced(self):
        """An existing state Selection field is removed and replaced."""
        fields = [
            {"name": "name", "type": "Char"},
            {"name": "state", "type": "Selection", "selection": [("x", "X")]},
        ]
        roles = [{"name": "manager", "label": "Manager"}]
        approval = _make_approval()
        model = _make_model(fields=fields, approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]

        # Only one state field
        state_fields = [f for f in enriched["fields"] if f["name"] == "state"]
        assert len(state_fields) == 1
        assert state_fields[0]["default"] == "draft"

    def test_lock_after_and_editable_fields(self):
        """lock_after and editable_fields from approval block are propagated."""
        roles = [{"name": "manager", "label": "Manager"}]
        approval = _make_approval(lock_after="manager_approved", editable_fields=["note"])
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]

        assert enriched["lock_after"] == "manager_approved"
        assert enriched["editable_fields"] == ["note"]

    def test_immutability_original_not_mutated(self):
        """Original spec and model dicts are not mutated."""
        roles = [{"name": "manager", "label": "Manager"}]
        approval = _make_approval()
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        original_model_keys = set(model.keys())
        original_spec_keys = set(spec.keys())

        _process_approval_patterns(spec)

        assert set(model.keys()) == original_model_keys
        assert set(spec.keys()) == original_spec_keys
        # Original model should not have 'has_approval'
        assert "has_approval" not in model

    def test_cyclic_states_raises_error(self):
        """Cyclic approval states raise ValueError (BUG-M15)."""
        roles = [
            {"name": "a", "label": "A"},
            {"name": "b", "label": "B"},
        ]
        # Create a cycle: draft -> state_a -> state_b, with state_b.next = state_a
        # This forms state_a -> state_b -> state_a (cycle)
        levels = [
            {"state": "state_a", "role": "a", "next": "state_b"},
            {"state": "state_b", "role": "b", "next": "state_a"},
        ]
        approval = _make_approval(levels=levels)
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        with pytest.raises(ValueError, match="Circular approval states"):
            _process_approval_patterns(spec)

    def test_skip_if_delegation_escalation(self):
        """FLAW-18: skip_if, allow_delegation, and escalation are propagated."""
        roles = [
            {"name": "manager", "label": "Manager"},
            {"name": "director", "label": "Director"},
        ]
        levels = [
            {
                "state": "manager_approved",
                "role": "manager",
                "next": "approved",
                "skip_if": "rec.amount < 1000",
                "allow_delegation": True,
                "escalation": {
                    "timeout_hours": 24,
                    "escalate_to": "director",
                },
            },
        ]
        approval = _make_approval(levels=levels)
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]

        method = enriched["approval_action_methods"][0]
        assert method["skip_if"] == "rec.amount < 1000"
        assert method["allow_delegation"] is True

        assert enriched["has_approval_escalation"] is True
        esc = enriched["approval_escalation_configs"][0]
        assert esc["timeout_hours"] == 24
        assert esc["escalate_to_role"] == "director"

    def test_non_approval_models_passed_through(self):
        """Models without approval are included unchanged in output."""
        roles = [{"name": "manager", "label": "Manager"}]
        plain_model = _make_model(name="test.plain", approval=None)
        approval_model = _make_model(
            name="test.request",
            approval=_make_approval(),
        )
        spec = _make_spec(
            models=[plain_model, approval_model],
            security_roles=roles,
        )

        result = _process_approval_patterns(spec)

        assert len(result["models"]) == 2
        assert result["models"][0]["name"] == "test.plain"
        assert result["models"][0].get("has_approval") is None
        assert result["models"][1]["name"] == "test.request"
        assert result["models"][1]["has_approval"] is True

    def test_initial_label_customization(self):
        """Custom initial_label is used in state selection."""
        roles = [{"name": "manager", "label": "Manager"}]
        approval = _make_approval(initial_label="New")
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]

        state_field = enriched["fields"][0]
        assert state_field["selection"][0] == ("draft", "New")

    def test_reject_allowed_from_custom(self):
        """Custom reject_allowed_from restricts which states allow rejection."""
        roles = [
            {"name": "manager", "label": "Manager"},
            {"name": "director", "label": "Director"},
        ]
        levels = [
            {"state": "mgr_approved", "role": "manager", "next": "dir_approved"},
            {"state": "dir_approved", "role": "director", "next": "approved"},
        ]
        approval = _make_approval(
            levels=levels,
            reject_allowed_from=["mgr_approved"],
        )
        model = _make_model(approval=approval)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_approval_patterns(spec)
        enriched = result["models"][0]

        assert enriched["reject_allowed_from"] == ["mgr_approved"]
        assert enriched["approval_reject_action"]["reject_allowed_from"] == ["mgr_approved"]
