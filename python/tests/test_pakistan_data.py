"""Schema sanity for data/pakistan/*.json reference tables."""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA = Path(__file__).parents[1] / "src" / "amil_utils" / "data" / "pakistan"


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_hec_grading_covers_scale() -> None:
    d = _load("hec_grading.json")
    grades = {g["grade"]: g for g in d["grades"]}
    assert grades["A"]["grade_points"] == 4.0
    assert grades["F"]["grade_points"] == 0.0
    assert d["cgpa_scale"] == 4.0
    assert d["passing_cgpa"] == 2.0
    for g in d["grades"]:
        assert 0 <= g["min_percent"] <= 100
        assert g["min_percent"] <= g["max_percent"]


def test_bps_scales_full_range() -> None:
    d = _load("bps_scales.json")
    grades = {s["grade"] for s in d["scales"]}
    assert grades == set(range(1, 23)), "BPS 1..22 required"
    for s in d["scales"]:
        assert s["min_pay"] > 0 and s["max_pay"] > s["min_pay"]
        assert s["annual_increment"] > 0
    assert d["_meta"]["source"], "must record verification source"


def test_tts_scales_three_designations() -> None:
    d = _load("tts_scales.json")
    names = {s["designation"] for s in d["scales"]}
    assert names == {"Assistant Professor", "Associate Professor", "Professor"}
    assert d["_meta"]["source"]


def test_payroll_deductions_structure() -> None:
    d = _load("payroll_deductions.json")
    assert "eobi" in d and "gp_fund" in d and "income_tax_slabs" in d
    assert d["eobi"]["employer_percent"] > 0
    assert d["income_tax_slabs"], "non-empty slab list"
    for slab in d["income_tax_slabs"]:
        assert "up_to" in slab and "rate_percent" in slab
    assert d["_meta"]["source"]


def test_quota_categories() -> None:
    d = _load("quota_categories.json")
    keys = {q["code"] for q in d["categories"]}
    assert {"open_merit", "sports", "disabled", "minority", "provincial"} <= keys
    assert d["tie_breakers"] == ["matric_percent", "date_of_birth_older",
                                  "submission_timestamp"]


def test_identity_formats_regex_valid() -> None:
    d = _load("identity_formats.json")
    cnic = d["formats"]["cnic"]
    assert re.match(cnic["regex"], "35202-1234567-1")
    assert not re.match(cnic["regex"], "12345")
    ntn = d["formats"]["ntn"]
    assert re.match(ntn["regex"], "1234567")
