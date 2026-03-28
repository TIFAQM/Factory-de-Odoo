"""Tests for the shared module name validation utility."""
from amil_utils.validation.module_name import validate_module_name, MODULE_NAME_RE


class TestValidateModuleName:
    def test_valid_simple(self):
        assert validate_module_name("sale") is None

    def test_valid_with_underscores(self):
        assert validate_module_name("hr_contract") is None

    def test_valid_with_digits(self):
        assert validate_module_name("uni_student") is None

    def test_valid_payroll(self):
        assert validate_module_name("hr_payroll") is None

    def test_invalid_uppercase(self):
        assert validate_module_name("Sale") is not None

    def test_invalid_starts_with_number(self):
        assert validate_module_name("1sale") is not None

    def test_invalid_empty(self):
        assert validate_module_name("") is not None

    def test_invalid_starts_with_zero(self):
        assert validate_module_name("0bad") is not None

    def test_regex_matches_valid(self):
        assert MODULE_NAME_RE.fullmatch("my_module_123")

    def test_regex_rejects_dots(self):
        assert not MODULE_NAME_RE.fullmatch("my.module")
