"""Tests for utils/copy module — deep copy utilities for preprocessor model dicts."""
from __future__ import annotations

import pytest

from amil_utils.utils.copy import (
    _DICT_OF_SETS_KEYS,
    _LIST_KEYS,
    deep_copy_model,
    has_field,
    merge_override_source,
)


# ===========================================================================
# deep_copy_model
# ===========================================================================


class TestDeepCopyModel:
    """Tests for canonical deep-copy of preprocessor model dicts."""

    # -- Happy path ---------------------------------------------------------

    def test_shallow_keys_copied(self):
        original = {"name": "sale.order", "description": "Sales Order"}
        copied = deep_copy_model(original)
        assert copied == original
        assert copied is not original

    def test_list_keys_are_independent(self):
        """Mutating copied lists must not affect the original."""
        original = {
            "name": "test",
            "fields": [{"name": "a"}, {"name": "b"}],
            "sql_constraints": [{"name": "c1"}],
        }
        copied = deep_copy_model(original)

        # Mutate copied lists
        copied["fields"].append({"name": "c"})
        copied["sql_constraints"].pop()

        # Original untouched
        assert len(original["fields"]) == 2
        assert len(original["sql_constraints"]) == 1

    def test_all_list_keys_copied(self):
        """Every key in _LIST_KEYS is independently copied."""
        original = {key: [f"item_{key}"] for key in _LIST_KEYS}
        copied = deep_copy_model(original)

        for key in _LIST_KEYS:
            assert copied[key] is not original[key]
            assert copied[key] == original[key]

    def test_override_sources_deep_copied(self):
        """override_sources dict-of-sets is independently copied."""
        original = {
            "name": "test",
            "override_sources": {
                "create": {"preprocessor_a", "preprocessor_b"},
                "write": {"preprocessor_c"},
            },
        }
        copied = deep_copy_model(original)

        # Mutate the copied dict and inner sets
        copied["override_sources"]["create"].add("new_source")
        copied["override_sources"]["unlink"] = {"x"}

        # Original untouched
        assert "new_source" not in original["override_sources"]["create"]
        assert "unlink" not in original["override_sources"]

    def test_missing_list_keys_no_error(self):
        """Keys in _LIST_KEYS that are absent from the model are silently skipped."""
        original = {"name": "test"}
        copied = deep_copy_model(original)
        assert copied == {"name": "test"}

    def test_missing_dict_of_sets_keys_no_error(self):
        original = {"name": "test"}
        copied = deep_copy_model(original)
        assert "override_sources" not in copied

    # -- Edge cases ---------------------------------------------------------

    def test_empty_model(self):
        copied = deep_copy_model({})
        assert copied == {}

    def test_empty_lists_remain_empty(self):
        original = {"fields": [], "sql_constraints": []}
        copied = deep_copy_model(original)
        assert copied["fields"] == []
        assert copied["sql_constraints"] == []
        assert copied["fields"] is not original["fields"]

    def test_empty_override_sources(self):
        original = {"override_sources": {}}
        copied = deep_copy_model(original)
        assert copied["override_sources"] == {}
        assert copied["override_sources"] is not original["override_sources"]

    def test_items_inside_lists_are_same_references(self):
        """Individual dicts inside lists are NOT deep-copied (documented behavior)."""
        field = {"name": "amount", "type": "Float"}
        original = {"fields": [field]}
        copied = deep_copy_model(original)
        assert copied["fields"][0] is field

    def test_non_list_key_values_shared(self):
        """Scalar and non-list-key values are shallow-shared (standard spread)."""
        nested = {"key": "value"}
        original = {"custom_data": nested}
        copied = deep_copy_model(original)
        assert copied["custom_data"] is nested


# ===========================================================================
# has_field
# ===========================================================================


class TestHasField:
    def test_field_exists(self):
        model = {"fields": [{"name": "amount"}, {"name": "state"}]}
        assert has_field(model, "amount") is True

    def test_field_not_exists(self):
        model = {"fields": [{"name": "amount"}]}
        assert has_field(model, "state") is False

    def test_empty_fields(self):
        model = {"fields": []}
        assert has_field(model, "anything") is False

    def test_no_fields_key(self):
        model = {}
        assert has_field(model, "anything") is False

    def test_field_dict_without_name_key(self):
        model = {"fields": [{"type": "Char"}]}
        assert has_field(model, "Char") is False


# ===========================================================================
# merge_override_source
# ===========================================================================


class TestMergeOverrideSource:
    def test_adds_to_existing_method(self):
        model = {"override_sources": {"create": {"source_a"}}}
        merge_override_source(model, "create", "source_b")
        assert model["override_sources"]["create"] == {"source_a", "source_b"}

    def test_creates_method_entry(self):
        model = {"override_sources": {}}
        merge_override_source(model, "write", "my_preprocessor")
        assert model["override_sources"]["write"] == {"my_preprocessor"}

    def test_creates_override_sources_key(self):
        model = {}
        merge_override_source(model, "create", "injector")
        assert model["override_sources"]["create"] == {"injector"}

    def test_duplicate_source_idempotent(self):
        model = {"override_sources": {"create": {"source_a"}}}
        merge_override_source(model, "create", "source_a")
        assert model["override_sources"]["create"] == {"source_a"}

    def test_multiple_methods(self):
        model = {}
        merge_override_source(model, "create", "pp1")
        merge_override_source(model, "write", "pp2")
        merge_override_source(model, "unlink", "pp3")
        assert len(model["override_sources"]) == 3

    def test_works_on_copied_model(self):
        """Typical usage: copy model first, then mutate the copy."""
        original = {"override_sources": {"create": {"a"}}}
        copied = deep_copy_model(original)
        merge_override_source(copied, "create", "b")
        assert "b" in copied["override_sources"]["create"]
        assert "b" not in original["override_sources"]["create"]
