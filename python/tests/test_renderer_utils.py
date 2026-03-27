"""Tests for renderer_utils module — shared utility functions and constants."""
from __future__ import annotations

import pytest

from amil_utils.renderer_utils import (
    INDEXABLE_TYPES,
    MONETARY_FIELD_PATTERNS,
    NON_INDEXABLE_TYPES,
    SEQUENCE_FIELD_NAMES,
    _is_monetary_field,
    _model_ref,
    _to_class,
    _to_python_var,
    _to_xml_id,
    _topologically_sort_fields,
)


# ===========================================================================
# Constants
# ===========================================================================


class TestConstants:
    def test_sequence_field_names_is_frozenset(self):
        assert isinstance(SEQUENCE_FIELD_NAMES, frozenset)
        assert "reference" in SEQUENCE_FIELD_NAMES
        assert "sequence" in SEQUENCE_FIELD_NAMES

    def test_monetary_patterns_is_frozenset(self):
        assert isinstance(MONETARY_FIELD_PATTERNS, frozenset)
        assert "amount" in MONETARY_FIELD_PATTERNS
        assert "salary" in MONETARY_FIELD_PATTERNS

    def test_indexable_and_non_indexable_disjoint(self):
        assert INDEXABLE_TYPES & NON_INDEXABLE_TYPES == frozenset()


# ===========================================================================
# _is_monetary_field
# ===========================================================================


class TestIsMonetaryField:
    # -- Happy path ---------------------------------------------------------

    def test_explicit_monetary_type(self):
        assert _is_monetary_field({"name": "x", "type": "Monetary"}) is True

    def test_float_with_monetary_name(self):
        assert _is_monetary_field({"name": "total_amount", "type": "Float"}) is True

    def test_float_with_salary_name(self):
        assert _is_monetary_field({"name": "base_salary", "type": "Float"}) is True

    def test_float_with_price_name(self):
        assert _is_monetary_field({"name": "unit_price", "type": "Float"}) is True

    # -- Non-monetary cases -------------------------------------------------

    def test_float_without_monetary_name(self):
        assert _is_monetary_field({"name": "weight", "type": "Float"}) is False

    def test_char_type_with_monetary_name(self):
        assert _is_monetary_field({"name": "amount_ref", "type": "Char"}) is False

    def test_integer_type_with_monetary_name(self):
        assert _is_monetary_field({"name": "total_amount", "type": "Integer"}) is False

    def test_explicit_monetary_false_opt_out(self):
        field = {"name": "total_amount", "type": "Float", "monetary": False}
        assert _is_monetary_field(field) is False

    def test_explicit_monetary_false_on_monetary_type(self):
        field = {"name": "x", "type": "Monetary", "monetary": False}
        assert _is_monetary_field(field) is False

    # -- Edge cases ---------------------------------------------------------

    def test_empty_field_dict(self):
        assert _is_monetary_field({}) is False

    def test_missing_name(self):
        assert _is_monetary_field({"type": "Float"}) is False

    def test_missing_type(self):
        assert _is_monetary_field({"name": "amount"}) is False

    def test_all_monetary_patterns_detected(self):
        for pattern in MONETARY_FIELD_PATTERNS:
            field = {"name": f"test_{pattern}_field", "type": "Float"}
            assert _is_monetary_field(field) is True, f"Pattern '{pattern}' not detected"


# ===========================================================================
# _model_ref
# ===========================================================================


class TestModelRef:
    def test_simple_model(self):
        assert _model_ref("sale.order") == "model_sale_order"

    def test_nested_dots(self):
        assert _model_ref("sale.order.line") == "model_sale_order_line"

    def test_single_word(self):
        assert _model_ref("product") == "model_product"

    def test_empty_string(self):
        assert _model_ref("") == "model_"


# ===========================================================================
# _to_class
# ===========================================================================


class TestToClass:
    def test_dot_notation(self):
        assert _to_class("sale.order") == "SaleOrder"

    def test_underscore_notation(self):
        assert _to_class("sale_order") == "SaleOrder"

    def test_mixed(self):
        assert _to_class("sale.order.line") == "SaleOrderLine"

    def test_single_word(self):
        assert _to_class("product") == "Product"

    def test_empty_string(self):
        assert _to_class("") == ""


# ===========================================================================
# _to_python_var
# ===========================================================================


class TestToPythonVar:
    def test_dot_notation(self):
        assert _to_python_var("sale.order") == "sale_order"

    def test_already_underscored(self):
        assert _to_python_var("sale_order") == "sale_order"

    def test_nested_dots(self):
        assert _to_python_var("sale.order.line") == "sale_order_line"

    def test_empty_string(self):
        assert _to_python_var("") == ""


# ===========================================================================
# _to_xml_id
# ===========================================================================


class TestToXmlId:
    def test_dot_notation(self):
        assert _to_xml_id("sale.order") == "sale_order"

    def test_already_underscored(self):
        assert _to_xml_id("sale_order") == "sale_order"

    def test_empty_string(self):
        assert _to_xml_id("") == ""


# ===========================================================================
# _topologically_sort_fields
# ===========================================================================


class TestTopologicallySortFields:
    def test_independent_fields_preserve_order(self):
        fields = [
            {"name": "a", "compute": "_compute_a"},
            {"name": "b", "compute": "_compute_b"},
        ]
        result = _topologically_sort_fields(fields)
        assert [f["name"] for f in result] == ["a", "b"]

    def test_dependent_fields_sorted(self):
        fields = [
            {"name": "total", "compute": "_compute_total", "depends": ["subtotal"]},
            {"name": "subtotal", "compute": "_compute_subtotal"},
        ]
        result = _topologically_sort_fields(fields)
        names = [f["name"] for f in result]
        assert names.index("subtotal") < names.index("total")

    def test_external_deps_ignored(self):
        """Dotted dependencies (e.g., partner_id.name) are external and ignored."""
        fields = [
            {"name": "display", "compute": "_compute_display",
             "depends": ["partner_id.name"]},
        ]
        result = _topologically_sort_fields(fields)
        assert len(result) == 1
        assert result[0]["name"] == "display"

    def test_deps_on_non_computed_ignored(self):
        """Dependencies on non-computed fields are ignored."""
        fields = [
            {"name": "total", "compute": "_compute_total", "depends": ["quantity"]},
        ]
        # "quantity" is not in the computed_fields list
        result = _topologically_sort_fields(fields)
        assert len(result) == 1

    def test_cycle_returns_original(self):
        """Cyclic dependencies return original order (graceful fallback)."""
        fields = [
            {"name": "a", "compute": "_a", "depends": ["b"]},
            {"name": "b", "compute": "_b", "depends": ["a"]},
        ]
        result = _topologically_sort_fields(fields)
        assert len(result) == 2

    def test_empty_list(self):
        assert _topologically_sort_fields([]) == []

    def test_single_field(self):
        fields = [{"name": "x", "compute": "_compute_x"}]
        result = _topologically_sort_fields(fields)
        assert len(result) == 1
        assert result[0]["name"] == "x"

    def test_chain_of_three(self):
        """c -> b -> a should sort as a, b, c."""
        fields = [
            {"name": "c", "compute": "_c", "depends": ["b"]},
            {"name": "a", "compute": "_a"},
            {"name": "b", "compute": "_b", "depends": ["a"]},
        ]
        result = _topologically_sort_fields(fields)
        names = [f["name"] for f in result]
        assert names.index("a") < names.index("b") < names.index("c")

    def test_no_depends_key(self):
        """Fields without 'depends' key should not error."""
        fields = [
            {"name": "x", "compute": "_compute_x"},
            {"name": "y", "compute": "_compute_y"},
        ]
        result = _topologically_sort_fields(fields)
        assert len(result) == 2
