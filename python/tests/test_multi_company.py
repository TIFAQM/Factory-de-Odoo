"""Tests for multi-company preprocessor and end-to-end rendering.

TDD tests for the multi_company spec flag that auto-injects company_id
fields and triggers company record rules.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from amil_utils.preprocessors.multi_company import inject_multi_company_fields


# ---------------------------------------------------------------------------
# Preprocessor unit tests
# ---------------------------------------------------------------------------


class TestMultiCompanyPreprocessor:
    """Unit tests for inject_multi_company_fields preprocessor."""

    def _make_spec(
        self,
        multi_company: bool = False,
        models: list[dict] | None = None,
    ) -> dict:
        return {
            "module_name": "test_mc",
            "multi_company": multi_company,
            "depends": ["base"],
            "models": models or [
                {
                    "name": "test.order",
                    "fields": [{"name": "name", "type": "Char"}],
                },
            ],
        }

    def test_multi_company_true_injects_company_id(self):
        """When multi_company=True, company_id is injected into each model."""
        spec = self._make_spec(multi_company=True)
        result = inject_multi_company_fields(spec)
        fields = result["models"][0]["fields"]
        company_fields = [f for f in fields if f["name"] == "company_id"]
        assert len(company_fields) == 1
        assert company_fields[0]["type"] == "Many2one"
        assert company_fields[0]["comodel_name"] == "res.company"

    def test_multi_company_false_no_injection(self):
        """When multi_company=False (default), no company_id is injected."""
        spec = self._make_spec(multi_company=False)
        result = inject_multi_company_fields(spec)
        fields = result["models"][0]["fields"]
        company_fields = [f for f in fields if f["name"] == "company_id"]
        assert len(company_fields) == 0

    def test_multi_company_skips_existing_company_id(self):
        """When model already has company_id, do not add a duplicate."""
        spec = self._make_spec(
            multi_company=True,
            models=[
                {
                    "name": "test.order",
                    "fields": [
                        {"name": "name", "type": "Char"},
                        {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
                    ],
                },
            ],
        )
        result = inject_multi_company_fields(spec)
        fields = result["models"][0]["fields"]
        company_fields = [f for f in fields if f["name"] == "company_id"]
        assert len(company_fields) == 1

    def test_multi_company_skips_transient_models(self):
        """Transient models (wizards) should NOT get company_id injected."""
        spec = self._make_spec(
            multi_company=True,
            models=[
                {
                    "name": "test.wizard",
                    "transient": True,
                    "fields": [{"name": "name", "type": "Char"}],
                },
            ],
        )
        result = inject_multi_company_fields(spec)
        fields = result["models"][0]["fields"]
        company_fields = [f for f in fields if f["name"] == "company_id"]
        assert len(company_fields) == 0

    def test_multi_company_multiple_models(self):
        """All non-transient models get company_id when multi_company=True."""
        spec = self._make_spec(
            multi_company=True,
            models=[
                {
                    "name": "test.order",
                    "fields": [{"name": "name", "type": "Char"}],
                },
                {
                    "name": "test.line",
                    "fields": [{"name": "description", "type": "Text"}],
                },
            ],
        )
        result = inject_multi_company_fields(spec)
        for model in result["models"]:
            company_fields = [f for f in model["fields"] if f["name"] == "company_id"]
            assert len(company_fields) == 1, f"Model {model['name']} missing company_id"

    def test_immutability_original_spec_not_mutated(self):
        """Preprocessor must not mutate the original spec dict."""
        spec = self._make_spec(multi_company=True)
        original_fields_count = len(spec["models"][0]["fields"])
        inject_multi_company_fields(spec)
        assert len(spec["models"][0]["fields"]) == original_fields_count

    def test_multi_company_missing_key_treated_as_false(self):
        """Spec without multi_company key should behave as False."""
        spec = {
            "module_name": "test_mc",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "fields": [{"name": "name", "type": "Char"}],
                },
            ],
        }
        result = inject_multi_company_fields(spec)
        fields = result["models"][0]["fields"]
        company_fields = [f for f in fields if f["name"] == "company_id"]
        assert len(company_fields) == 0

    def test_injected_company_id_has_correct_default(self):
        """Injected company_id should have a lambda default pointing to env.company."""
        spec = self._make_spec(multi_company=True)
        result = inject_multi_company_fields(spec)
        company_field = next(
            f for f in result["models"][0]["fields"] if f["name"] == "company_id"
        )
        assert "default" in company_field
        assert "env.company" in company_field["default"]
