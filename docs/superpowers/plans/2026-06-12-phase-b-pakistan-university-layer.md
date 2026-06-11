# Phase B: Pakistan / University Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Factory-de-Odoo the domain layer it needs to generate the Pakistani-university ERP defined by the PRD (`/home/inshal-rauf/TIFAQM/Project 1 Documentation/ERP /` — note trailing space in dir name): education + Pakistan-integration knowledge, HEC/BPS reference data, Pakistani report templates (fee challan, HEC transcript, QR-verified degree), localization-aware decomposition, and PRD-specific module questions.

**Architecture:** Knowledge files teach the generation agents domain patterns (consumed at agent-spawn time); JSON data files under `data/pakistan/` provide authoritative reference tables; new QWeb report templates are selected via a `template_style` key on report specs in `render_reports`; workflow/question edits plumb the user's localization answer through to the research agents.

**Tech Stack:** Python 3.12, Jinja2, pytest, Markdown knowledge files. No new dependencies.

**Locked decisions (user, 2026-06-12):**
1. **Payroll = OCA** (`OCA/payroll` repo, `payroll`/`payroll_account` modules) — never Odoo Enterprise `hr_payroll`.
2. **Module 31 = `university_qec`** (QEC/OBE: CLO-PLO-PEO attainment, self-assessment reports, HEC IPE) added to the PRD's 30 modules.
3. Target: complete e2e ERP for Pakistani universities, human-in-the-loop at every spec gate.

**Depends on:** Phase A complete (workflows de-Noded; `decomposition`/`spec` CLI groups exist).

**Branch:** continue on `phase-a-pipeline-fixes` or branch `phase-b-pakistan-layer` off it.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `amil/knowledge/education.md` | Create | University-domain Odoo patterns (enrollment, GPA, attendance, merit, affiliate) |
| `amil/knowledge/pakistan.md` | Create | Pakistan integration + compliance patterns (NADRA, RAAST, HEC PMIS, EOBI, FBR, challan) |
| `python/src/amil_utils/data/pakistan/hec_grading.json` | Create | HEC grade↔GPA mapping |
| `python/src/amil_utils/data/pakistan/bps_scales.json` | Create | BPS 1–22 pay scales (web-verified at execution) |
| `python/src/amil_utils/data/pakistan/tts_scales.json` | Create | TTS faculty pay ranges (web-verified at execution) |
| `python/src/amil_utils/data/pakistan/payroll_deductions.json` | Create | EOBI / GP Fund / income-tax-slab references (web-verified) |
| `python/src/amil_utils/data/pakistan/quota_categories.json` | Create | Admission quota categories + tie-breaker rules |
| `python/src/amil_utils/data/pakistan/identity_formats.json` | Create | CNIC / NTN / STRN regex + display formats |
| `python/src/amil_utils/templates/shared/pk_fee_challan.xml.j2` | Create | Bank-format fee challan QWeb report (3 copies) |
| `python/src/amil_utils/templates/shared/pk_transcript.xml.j2` | Create | HEC-format transcript QWeb report |
| `python/src/amil_utils/templates/shared/pk_degree.xml.j2` | Create | Degree certificate with QR verification |
| `python/src/amil_utils/renderer_stages.py` | Modify | `render_reports` selects template by `report.template_style` |
| `amil/references/module-questions.json` | Modify | Add `affiliate`, `payroll_pk`, `taxation` sections; extend `fee`/`exam` |
| `amil/workflows/new-erp.md` | Modify | Pass `LOCALIZATION` to the 4 research agents; PK decomposition rules |
| `agents/amil-model-gen.md` | Modify | Conditionally load `accounting.md` / `education.md` / `pakistan.md` |
| `agents/amil-logic-writer.md` | Modify | Same conditional knowledge loading |
| `python/tests/test_pk_report_templates.py` | Create | Render tests for the 3 PK report styles |
| `python/tests/test_pakistan_data.py` | Create | Schema sanity tests for the data files |
| `python/tests/test_security_cross_module_groups.py` | Create | Verify external (cross-module) group refs in ACLs/rules work |

---

### Task 1: Pakistan reference data files

**Files:**
- Create: `python/src/amil_utils/data/pakistan/` (6 JSON files)
- Test: `python/tests/test_pakistan_data.py`

**Accuracy rule:** grading/quota/identity content below is authoritative and stable — write as given. BPS/TTS/deduction figures change with federal budget notifications: the executor MUST verify current figures via WebSearch ("BPS pay scales 2024 Pakistan notification", "TTS pay scales HEC", "EOBI contribution rate 2025", "Pakistan income tax slabs salaried 2025-26") and fill `scales`/`rates` with sourced values, recording the source URL in `_meta.source`. Structure is fixed by the tests; numbers come from research.

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/test_pakistan_data.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd python && uv run pytest tests/test_pakistan_data.py -v`
Expected: FAIL — files missing

- [ ] **Step 3: Create the authoritative-content files**

```json
// python/src/amil_utils/data/pakistan/hec_grading.json
{
  "_meta": {
    "description": "HEC Pakistan semester-system grading scheme (4.0 CGPA scale)",
    "source": "HEC Grading Scheme for Semester System guidelines",
    "last_updated": "2026-06-12"
  },
  "cgpa_scale": 4.0,
  "passing_cgpa": 2.0,
  "deans_list_cgpa": 3.5,
  "probation_cgpa": 2.0,
  "grades": [
    {"grade": "A",  "grade_points": 4.0,  "min_percent": 85, "max_percent": 100},
    {"grade": "A-", "grade_points": 3.67, "min_percent": 80, "max_percent": 84},
    {"grade": "B+", "grade_points": 3.33, "min_percent": 75, "max_percent": 79},
    {"grade": "B",  "grade_points": 3.0,  "min_percent": 71, "max_percent": 74},
    {"grade": "B-", "grade_points": 2.67, "min_percent": 68, "max_percent": 70},
    {"grade": "C+", "grade_points": 2.33, "min_percent": 64, "max_percent": 67},
    {"grade": "C",  "grade_points": 2.0,  "min_percent": 61, "max_percent": 63},
    {"grade": "C-", "grade_points": 1.67, "min_percent": 58, "max_percent": 60},
    {"grade": "D+", "grade_points": 1.33, "min_percent": 54, "max_percent": 57},
    {"grade": "D",  "grade_points": 1.0,  "min_percent": 50, "max_percent": 53},
    {"grade": "F",  "grade_points": 0.0,  "min_percent": 0,  "max_percent": 49}
  ],
  "academic_standing": [
    {"status": "good_standing", "min_cgpa": 2.0},
    {"status": "warning",       "min_cgpa": 1.5, "max_cgpa": 1.99},
    {"status": "probation",     "max_cgpa": 1.49,
     "note": "second consecutive probation semester -> rustication proceedings"}
  ]
}
```

```json
// python/src/amil_utils/data/pakistan/quota_categories.json
{
  "_meta": {
    "description": "Admission seat quota categories per PRD WF-02/WF-03",
    "source": "university_workflow_diagrams.docx WF-02 step 05, WF-03 step 03",
    "last_updated": "2026-06-12"
  },
  "categories": [
    {"code": "open_merit", "label": "Open Merit",        "fill_order": 1},
    {"code": "sports",     "label": "Sports",            "fill_order": 2},
    {"code": "disabled",   "label": "Disabled (2%)",     "fill_order": 3},
    {"code": "minority",   "label": "Minorities",        "fill_order": 4},
    {"code": "provincial", "label": "Provincial/Domicile", "fill_order": 5,
     "provinces": ["Punjab", "Sindh", "KPK", "Balochistan", "AJK", "GB"]}
  ],
  "unfilled_quota_rule": "transfer remaining quota seats to open merit",
  "tie_breakers": ["matric_percent", "date_of_birth_older", "submission_timestamp"]
}
```

```json
// python/src/amil_utils/data/pakistan/identity_formats.json
{
  "_meta": {
    "description": "Pakistani identity number formats and validation regex",
    "source": "NADRA CNIC format; FBR NTN/STRN formats",
    "last_updated": "2026-06-12"
  },
  "formats": {
    "cnic": {
      "label": "CNIC",
      "regex": "^[0-9]{5}-[0-9]{7}-[0-9]$",
      "placeholder": "XXXXX-XXXXXXX-X",
      "example": "35202-1234567-1"
    },
    "ntn": {
      "label": "National Tax Number",
      "regex": "^[0-9]{7}$|^[0-9]{13}$",
      "placeholder": "NNNNNNN or 13-digit CNIC-based",
      "example": "1234567"
    },
    "strn": {
      "label": "Sales Tax Registration Number",
      "regex": "^[0-9]{2}-[0-9]{2}-[0-9]{4}-[0-9]{3}-[0-9]{2}$",
      "example": "12-34-5678-901-23"
    },
    "phone_pk": {
      "label": "Pakistani Mobile",
      "regex": "^(\\+92|0)3[0-9]{2}-?[0-9]{7}$",
      "example": "+923001234567"
    }
  }
}
```

- [ ] **Step 4: Research and create the budget-sensitive files**

Use WebSearch to verify current figures, then write `bps_scales.json`, `tts_scales.json`, `payroll_deductions.json` in exactly these shapes (values from research, `_meta.source` = the notification/page you used):

```json
// python/src/amil_utils/data/pakistan/bps_scales.json  (STRUCTURE — values from research)
{
  "_meta": {"description": "Basic Pay Scale (BPS) grades 1-22",
             "source": "<verified URL/notification>", "effective": "<FY>",
             "last_updated": "2026-06-12"},
  "scales": [
    {"grade": 1, "min_pay": 0, "max_pay": 0, "annual_increment": 0, "stages": 30}
    // ... grades 2..22
  ],
  "allowances": {
    "house_rent_big_city_percent": 0,
    "house_rent_small_city_percent": 0,
    "medical_allowance_note": "<verified>",
    "conveyance_allowance_note": "<verified>"
  }
}
```

```json
// python/src/amil_utils/data/pakistan/tts_scales.json  (STRUCTURE)
{
  "_meta": {"description": "HEC Tenure Track System pay scales",
             "source": "<verified>", "last_updated": "2026-06-12"},
  "scales": [
    {"designation": "Assistant Professor", "min_pay": 0, "max_pay": 0},
    {"designation": "Associate Professor", "min_pay": 0, "max_pay": 0},
    {"designation": "Professor",           "min_pay": 0, "max_pay": 0}
  ]
}
```

```json
// python/src/amil_utils/data/pakistan/payroll_deductions.json  (STRUCTURE)
{
  "_meta": {"description": "Statutory payroll deductions for Pakistani universities",
             "source": "<verified>", "last_updated": "2026-06-12"},
  "eobi": {"employer_percent": 5.0, "employee_percent": 1.0,
            "wage_base_note": "<verified minimum-wage base>"},
  "gp_fund": {"note": "General Provident Fund — % of basic pay by BPS band",
               "bands": [{"bps_min": 1, "bps_max": 15, "percent": 0},
                          {"bps_min": 16, "bps_max": 22, "percent": 0}]},
  "income_tax_slabs": [
    {"up_to": 600000, "rate_percent": 0, "fixed": 0}
    // ... remaining salaried slabs for current FY from FBR
  ]
}
```

- [ ] **Step 5: Run tests until green**

Run: `cd python && uv run pytest tests/test_pakistan_data.py -v`
Expected: 6 PASSED

- [ ] **Step 6: Commit**

```bash
git add python/src/amil_utils/data/pakistan python/tests/test_pakistan_data.py
git commit -m "feat(data): Pakistan reference tables — HEC grading, BPS/TTS, deductions, quotas, identity formats"
```

---

### Task 2: `amil/knowledge/education.md`

**Files:**
- Create: `amil/knowledge/education.md`

Follow the house format: study `amil/knowledge/accounting.md` first (`head -80 amil/knowledge/accounting.md`) and mirror its heading structure and WRONG/CORRECT pair style exactly.

- [ ] **Step 1: Write the file** with these sections, each with at least one WRONG/CORRECT pair of real Odoo 19 code (15+ pairs total, ≥400 lines). Content requirements per section:

1. **Student & Enrollment models** — `university.student` holds a `partner_id` Many2one to `res.partner` (not `_inherits`); enrollment is a separate `university.enrollment` model linking student↔course-offering↔semester; never store mutable GPA on the enrollment line. WRONG: putting student fields directly on res.partner via inherit. CORRECT: dedicated model + partner link.
2. **GPA/CGPA computation** — load grade↔points mapping from configuration data (reference `data/pakistan/hec_grading.json` values seeded as `university.grade.scale` records), compute semester GPA = Σ(points×credit_hours)/Σ(credit_hours), CGPA cumulative; `@api.depends` through `enrollment_ids.grade_id.points`. WRONG: hardcoding the scale in Python. CORRECT: data-driven scale + stored computed fields with full depends chain.
3. **Attendance %** — per course; `(attended / held) * 100`; thresholds 85/80/75 with activity_schedule escalation; course-specific exam bar below 75% (PRD WF-05). Show the compute + the cron that sends shortage alerts.
4. **Merit score** — weighted formula (matric 10% + inter 40% + test 50% per PRD WF-02; weights configurable via `ir.config_parameter`); tie-breakers per `quota_categories.json`. Show `_compute_merit_score` with zero-division guards.
5. **Fee challan lifecycle** — challan model states `unpaid→paid/expired/refunded`; bank fields; webhook confirmation advances the linked application/enrollment workflow; link to `account.move` for accounting. WRONG: marking paid from a button without payment evidence fields. CORRECT: webhook handler + audit fields (bank ref, paid date).
6. **State-machine workflows** — application lifecycle per PRD WF-01 (`draft→submitted→fee_paid→eligible→selected→confirmed/rejected/cancelled`), buttons with `groups=`, `tracking=True` on state, `activity_schedule` for approvals.
7. **Roles & record rules for universities** — student-own-record, faculty-own-students (`enrollment_ids.course_id.faculty_id.user_id`), HOD-own-department, affiliate-own-college domains (copy the exact domain patterns from the PRD's ORM translation §5); cross-module groups live in the base module and are referenced as `university_base.group_*`.
8. **Affiliate-college patterns** — `college_id` on every affiliate-scoped model; time-bound access (active affiliation check in record rule domain); submission windows enforced with `@api.constrains` against session open/close datetimes.
9. **Payroll (OCA)** — depend on OCA `payroll` (repo `OCA/payroll`), NEVER Enterprise `hr_payroll`; salary structures for BPS/TTS reference `data/pakistan/bps_scales.json` seeded as `hr.payroll.structure` + rule records; deductions per `payroll_deductions.json`. WRONG: `"depends": ["hr_payroll"]`. CORRECT: `"depends": ["payroll", "payroll_account"]` with OCA note.
10. **QEC/OBE (Module 31 — `university_qec`)** — CLO/PLO/PEO models, CLO-PLO mapping matrix, attainment computed from assessment results, course self-assessment reports; HEC IPE checklist as data records.
11. **Degree audit & graduation** — credit-hour audit against program requirements; no-dues multi-department clearance pattern (one2many of clearance lines, each owned by a department group).
12. **Document issuance** — QWeb reports with QR verification (`t-field` barcode widget, serial-number `ir.sequence`), issuance audit log pattern.

- [ ] **Step 2: Sanity checks**

```bash
wc -l amil/knowledge/education.md          # expect >= 400
grep -c "WRONG" amil/knowledge/education.md  # expect >= 15
grep -n "hr_payroll" amil/knowledge/education.md  # only inside WRONG examples
```

- [ ] **Step 3: Register in MASTER.md**

Add one line to the knowledge index in `amil/knowledge/MASTER.md` following its existing file-listing format: `education.md — university/HEC domain patterns (enrollment, GPA, attendance, merit, challan, affiliate, OCA payroll, QEC/OBE)`.

- [ ] **Step 4: Commit**

```bash
git add amil/knowledge/education.md amil/knowledge/MASTER.md
git commit -m "feat(knowledge): education.md — university domain patterns for Pakistani HEC ERPs"
```

---

### Task 3: `amil/knowledge/pakistan.md` (integrations + compliance)

**Files:**
- Create: `amil/knowledge/pakistan.md`

- [ ] **Step 1: Write the file** (≥250 lines, ≥8 WRONG/CORRECT pairs) with sections:

1. **Credential handling** — all external API credentials via `ir.config_parameter` with `sudo()`, never hardcoded; parameter naming convention `university.<service>_api_url` / `_token` (mirrors PRD §7).
2. **NADRA CNIC verification** — the full pattern from the PRD: `requests.post` with 10s timeout, 3 retries at 30s intervals, `verified/flagged` selection field, `activity_schedule` to Registrar on mismatch; WRONG: blocking the create() on API call. CORRECT: post-payment trigger + async-tolerant flagging.
3. **SBP 1-Link / RAAST payment confirmation** — webhook controller (`@http.route(type="json", auth="public", csrf=False)` + signature check), challan number lookup, idempotent confirmation (re-delivery safe), daily reconciliation cron. WRONG: trusting the webhook body without verifying against the gateway. CORRECT: verify-then-update.
4. **HEC PMIS/PRMS annual return** — `ir.cron` batch submission with payload aggregation (`read_group` by program/gender), failure logging to chatter on a settings record.
5. **SMS gateway** — Odoo `sms` module usage, PK number normalization (`identity_formats.json` phone regex), critical notifications dual-channel (SMS+email).
6. **EOBI batch file** — monthly cron generating registration batch for new staff.
7. **FBR withholding** — salaried slab computation referencing `payroll_deductions.json`; tax certificate report data.
8. **CNIC/NTN validation constraints** — `@api.constrains` using the regexes from `identity_formats.json` (note: `pakistan_hec.py` preprocessor already injects CNIC/phone constraints — reference, don't duplicate).
9. **Fiscal year** — Pakistani FY July–June: `account.fiscal.year` configuration, `date_range` notes for reports.
10. **Timezone/locale** — `Asia/Karachi` tz in user defaults and cron `nextcall` math.

- [ ] **Step 2: Sanity + register in MASTER.md** (same pattern as Task 2 Step 3)

```bash
wc -l amil/knowledge/pakistan.md   # >= 250
grep -c "WRONG" amil/knowledge/pakistan.md  # >= 8
```

- [ ] **Step 3: Commit**

```bash
git add amil/knowledge/pakistan.md amil/knowledge/MASTER.md
git commit -m "feat(knowledge): pakistan.md — NADRA/RAAST/HEC/EOBI/FBR integration patterns"
```

---

### Task 4: Pakistani QWeb report templates + `template_style` selection

**Files:**
- Create: `python/src/amil_utils/templates/shared/pk_fee_challan.xml.j2`
- Create: `python/src/amil_utils/templates/shared/pk_transcript.xml.j2`
- Create: `python/src/amil_utils/templates/shared/pk_degree.xml.j2`
- Modify: `python/src/amil_utils/renderer_stages.py:560-575` (`render_reports` template selection)
- Test: `python/tests/test_pk_report_templates.py`

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/test_pk_report_templates.py
"""PK report styles render via report.template_style selection."""
from __future__ import annotations

from pathlib import Path

from amil_utils.renderer import render_module


def _spec(report: dict) -> dict:
    return {
        "module_name": "uni_fee",
        "module_title": "University Fees",
        "odoo_version": "19.0",
        "depends": ["base", "account"],
        "models": [{
            "name": "uni.fee.challan",
            "description": "Fee Challan",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "amount_total", "type": "Float"},
                {"name": "due_date", "type": "Date"},
                {"name": "bank_name", "type": "Char"},
                {"name": "roll_number", "type": "Char"},
            ],
        }],
        "security": {},
        "reports": [report],
    }


def _render_report_xml(tmp_path: Path, report: dict) -> str:
    render_module(_spec(report), tmp_path)  # match real signature per test_renderer.py
    path = tmp_path / "uni_fee" / "data" / f"report_{report['xml_id']}_template.xml"
    return path.read_text()


def test_pk_challan_style(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "fee_challan", "name": "Fee Challan",
        "model": "uni.fee.challan", "template_style": "pk_challan",
        "amount_field": "amount_total", "challan_number_field": "name",
        "due_date_field": "due_date", "bank_field": "bank_name",
        "copies": ["Bank Copy", "University Copy", "Student Copy"],
    })
    assert "Bank Copy" in content
    assert "University Copy" in content
    assert "Student Copy" in content
    assert "barcode" in content  # challan number barcode


def test_pk_transcript_style(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "transcript", "name": "Transcript",
        "model": "uni.fee.challan", "template_style": "pk_transcript",
        "student_name_field": "name", "cgpa_field": "amount_total",
        "lines_field": False,
    })
    assert "CGPA" in content
    assert "Controller of Examinations" in content


def test_pk_degree_style_has_qr(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "degree", "name": "Degree",
        "model": "uni.fee.challan", "template_style": "pk_degree",
        "student_name_field": "name", "serial_field": "name",
        "verify_url_param": "university.degree_verify_url",
    })
    assert "QR" in content
    assert "verify" in content.lower()


def test_default_style_unchanged(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "plain", "name": "Plain",
        "model": "uni.fee.challan",
        "header_fields": [{"label": "Amount", "field": "amount_total"}],
    })
    assert "web.external_layout" in content  # existing generic template
```

NOTE: confirm `render_module`'s call form and the rendered path from `renderer_stages.py:564` (`module_dir / "data" / f"report_{report['xml_id']}_template.xml"`); if reports require manifest wiring keys, copy the minimal report fixture from existing `python/tests/` report tests.

- [ ] **Step 2: Run to verify failure**

Run: `cd python && uv run pytest tests/test_pk_report_templates.py -v`
Expected: first three FAIL (unknown style → generic output), `test_default_style_unchanged` PASSES already.

- [ ] **Step 3: Modify `render_reports`** in `renderer_stages.py` — replace the fixed template name with style selection:

```python
_REPORT_STYLE_TEMPLATES = {
    "pk_challan": "pk_fee_challan.xml.j2",
    "pk_transcript": "pk_transcript.xml.j2",
    "pk_degree": "pk_degree.xml.j2",
}

# inside the `for report in reports:` loop, replace the second render_template call:
template_name = _REPORT_STYLE_TEMPLATES.get(
    report.get("template_style", ""), "report_template.xml.j2")
render_template(
    env, template_name,
    module_dir / "data" / f"report_{report['xml_id']}_template.xml",
    report_ctx,
)
```

(Keep the `report_action.xml.j2` call unchanged — actions are style-independent.)

- [ ] **Step 4: Write the three templates**

`pk_fee_challan.xml.j2` — three-copy bank challan; each copy identical block with copy label, perforation rule between copies:

```jinja
{# pk_fee_challan.xml.j2 -- Pakistani bank fee challan (multi-copy) #}
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <template id="report_{{ report.xml_id }}">
        <t t-call="web.html_container">
            <t t-foreach="docs" t-as="doc">
                <div class="page">
{% for copy in report.get('copies', ['Bank Copy', 'University Copy', 'Student Copy']) %}
                    <div class="row border p-2 mb-1" style="page-break-inside: avoid;">
                        <div class="col-12 text-center">
                            <strong>{{ module_title }}</strong>
                            <span class="float-end badge text-bg-secondary">{{ copy }}</span>
                        </div>
                        <div class="col-6">
                            <strong>Challan #:</strong>
                            <span t-field="doc.{{ report.challan_number_field }}"/>
                        </div>
                        <div class="col-6">
                            <strong>Due Date:</strong>
                            <span t-field="doc.{{ report.due_date_field }}"/>
                        </div>
{% if report.get('roll_number_field') %}
                        <div class="col-6">
                            <strong>Roll No:</strong>
                            <span t-field="doc.{{ report.roll_number_field }}"/>
                        </div>
{% endif %}
{% if report.get('bank_field') %}
                        <div class="col-6">
                            <strong>Bank:</strong>
                            <span t-field="doc.{{ report.bank_field }}"/>
                        </div>
{% endif %}
{% if report.get('fee_lines_field') %}
                        <table class="table table-sm table-bordered col-12">
                            <thead><tr><th>Fee Head</th><th class="text-end">Amount (PKR)</th></tr></thead>
                            <tbody>
                                <tr t-foreach="doc.{{ report.fee_lines_field }}" t-as="line">
                                    <td><span t-field="line.name"/></td>
                                    <td class="text-end"><span t-field="line.amount"/></td>
                                </tr>
                            </tbody>
                        </table>
{% endif %}
                        <div class="col-6">
                            <strong>Total Amount:</strong>
                            <span t-field="doc.{{ report.amount_field }}"/>
                        </div>
                        <div class="col-6 text-center">
                            <img t-att-src="'/report/barcode/?barcode_type=Code128&amp;value=%s&amp;width=300&amp;height=40' % doc.{{ report.challan_number_field }}"
                                 alt="Challan barcode"/>
                        </div>
                        <div class="col-12"><small class="text-muted">
                            Pay before due date at the designated bank branch or via 1-Link/RAAST.
                        </small></div>
                    </div>
{% if not loop.last %}
                    <hr style="border-top: 1px dashed #000;"/>
{% endif %}
{% endfor %}
                </div>
            </t>
        </t>
    </template>
</odoo>
```

`pk_transcript.xml.j2` — HEC transcript: university header, student block (name/father/roll/program/CNIC fields configurable), semester-wise course table (`lines_field` optional one2many with course/credit/grade/points columns), CGPA box, signature block "Controller of Examinations" + "Checked by", medium-of-instruction note. Full Jinja in the same style as the challan (write it completely; ~90 lines).

`pk_degree.xml.j2` — landscape certificate body: ornamental heading, "This is to certify that <name> …", program/CGPA/session, serial number, QR verification block:

```jinja
                        <div class="text-center">
                            <img t-att-src="'/report/barcode/?barcode_type=QR&amp;value=%s&amp;width=120&amp;height=120' % (env['ir.config_parameter'].sudo().get_param('{{ report.verify_url_param }}', '') + str(doc.{{ report.serial_field }}))"
                                 alt="QR verification code"/>
                            <div><small>Scan to verify (QR)</small></div>
                        </div>
```

plus dual signature lines (Controller of Examinations, Vice Chancellor).

- [ ] **Step 5: Run tests**

Run: `cd python && uv run pytest tests/test_pk_report_templates.py tests/ -k "report" -q`
Expected: all PASS, no regression in existing report tests

- [ ] **Step 6: Commit**

```bash
git add python/src/amil_utils/templates/shared/pk_*.xml.j2 python/src/amil_utils/renderer_stages.py python/tests/test_pk_report_templates.py
git commit -m "feat(templates): Pakistani report styles — bank challan, HEC transcript, QR degree"
```

---

### Task 5: PRD-specific module questions

**Files:**
- Modify: `amil/references/module-questions.json` (existing keys: core, student, fee, exam, faculty, hr, timetable, notification, portal, generic)

- [ ] **Step 1: Inspect format**

Run: `python3 -c "import json; d=json.load(open('amil/references/module-questions.json')); print(json.dumps(d['fee'], indent=2)[:1500])"`
Mirror the exact per-question object shape (id/question/options/default keys as found).

- [ ] **Step 2: Add three new sections** (in the discovered shape):

- `affiliate` — questions: affiliation lifecycle states needed? per-college program approval? exam-form submission window enforcement? college inspection workflow? roll-number issuance by mother university? results visible only post-publication? compliance dashboard scope?
- `payroll_pk` — BPS or TTS or both? OCA payroll confirmed (default yes, note: never Enterprise hr_payroll)? deduction set (income tax, EOBI, GP Fund, benevolent fund, insurance)? bank transfer file format (HBL/NBP/MCB)? arrears/increment processing?
- `taxation` — withholding sections applicable? NTN capture on vendors? fiscal year July–June confirm? tax certificate generation?

- [ ] **Step 3: Extend two existing sections**

- `fee`: add questions — challan copies (2/3/4)? collecting banks? 1-Link/RAAST confirmation webhook? late-fee policy? installment plans?
- `exam`: add — HEC grading scale confirm (A=85+/4.0)? UFM workflow? re-checking fee + window? gazette approval chain (Controller→VC)? attendance bar 75%?

- [ ] **Step 4: Validate + commit**

```bash
python3 -m json.tool amil/references/module-questions.json >/dev/null && echo OK
git add amil/references/module-questions.json
git commit -m "feat(questions): affiliate, PK payroll, taxation question sets; PK-specific fee/exam questions"
```

---

### Task 6: Localization plumbing in `new-erp.md`

**Files:**
- Modify: `amil/workflows/new-erp.md` (Stage B agent spawns ~lines 118-170; Stage C rules ~line 255+)

Phase A already de-Noded this file; this task adds localization awareness.

- [ ] **Step 1: Pass LOCALIZATION to all four research agents**

In each of the 4 agent prompt blocks in Stage B (Module Boundary Analyzer, OCA Registry Checker, Dependency Mapper, Computation Chain Identifier), add to the prompt variables where `PRD_TEXT` is passed:

```
LOCALIZATION: {LOCALIZATION}   # from Q3; e.g. "pk" — agents must apply localization-specific module/dependency rules
```

- [ ] **Step 2: Add a localization rules block to Stage C** (after the merge step):

```markdown
### Localization rules (applied when LOCALIZATION == "pk")

- Payroll modules MUST depend on OCA `payroll` (repo OCA/payroll) — never Enterprise `hr_payroll`.
- Accounting-touching modules add `l10n_pk` to depends.
- Identity fields follow `data/pakistan/identity_formats.json` (CNIC, NTN); CNIC fields are injected by the `pakistan_hec` preprocessor when `localization: "pk"` is set in spec.
- Fee modules include challan generation (report template_style `pk_challan`) and 1-Link/RAAST confirmation hooks.
- Exam/transcript modules use HEC grading (`data/pakistan/hec_grading.json`) and template_style `pk_transcript` / `pk_degree`.
- For university ERPs include Module 31 `university_qec` (QEC/OBE: CLO-PLO-PEO attainment, SAR, HEC IPE) unless the PRD explicitly excludes it.
- Knowledge files `education.md` and `pakistan.md` must be listed in the spawned generation agents' knowledge set.
```

- [ ] **Step 3: Verify spec passes `localization`**

Run: `grep -n "localization" amil/workflows/new-erp.md amil/workflows/plan-module.md python/src/amil_utils/spec_schema.py | head`
`spec_schema.py:240` already has `localization: str | None`. Ensure plan-module's spec-generator agent prompt includes `LOCALIZATION` so generated spec.json carries `"localization": "pk"` (add the variable to its prompt block the same way as Step 1).

- [ ] **Step 4: Commit**

```bash
git add amil/workflows/new-erp.md amil/workflows/plan-module.md
git commit -m "feat(workflows): thread LOCALIZATION through research/spec agents; PK decomposition rules"
```

---

### Task 7: Conditional knowledge loading in generation agents

**Files:**
- Modify: `agents/amil-model-gen.md`
- Modify: `agents/amil-logic-writer.md`

- [ ] **Step 1: Inspect current knowledge-loading section**

Run: `grep -n "knowledge\|MASTER.md\|models.md" agents/amil-model-gen.md | head`

- [ ] **Step 2: Add conditional loads** to both agents, following each file's existing knowledge-list format:

```markdown
Conditionally load additional knowledge files:
- If spec `depends` includes `account` or any model references `account.*`: also read `knowledge/accounting.md`.
- If spec `localization` == "pk" OR module name starts with `university_`/`uni_`: also read `knowledge/education.md` and `knowledge/pakistan.md`.
- If spec `depends` includes `stock`: also read `knowledge/inventory.md`.
```

- [ ] **Step 3: Commit**

```bash
git add agents/amil-model-gen.md agents/amil-logic-writer.md
git commit -m "feat(agents): conditional domain knowledge loading (accounting/education/pakistan/inventory)"
```

---

### Task 8: Cross-module security groups — verify & test

**Files:**
- Test: `python/tests/test_security_cross_module_groups.py`
- Possibly modify: `python/src/amil_utils/preprocessors/security.py` (`_resolve_group_ref`, line ~221) — ONLY if the test exposes a gap.

The PRD defines 26 global roles in `university_base`, referenced by 30 other modules (`groups="university_base.group_registrar"`). The security preprocessor resolves bare role names to module-local groups; this task proves dotted external refs pass through untouched in ACLs, record rules, and field `groups`.

- [ ] **Step 1: Write the test**

```python
# python/tests/test_security_cross_module_groups.py
"""External (cross-module) group references must pass through security
preprocessing and land in generated ACL/rules/fields unchanged."""
from __future__ import annotations

from pathlib import Path

from amil_utils.renderer import render_module


SPEC = {
    "module_name": "uni_fee",
    "module_title": "University Fees",
    "odoo_version": "19.0",
    "depends": ["base", "university_base"],
    "models": [{
        "name": "uni.fee.challan",
        "description": "Fee Challan",
        "fields": [
            {"name": "name", "type": "Char", "required": True},
            {"name": "bank_ref", "type": "Char",
             "groups": "university_base.group_treasurer"},
        ],
        "custom_record_rules": [{
            "xml_id": "rule_challan_registrar",
            "label": "Registrar: all challans",
            "group_xml_id": "university_base.group_registrar",
            "domain_force": "[(1, '=', 1)]",
        }],
    }],
    "security": {},
}


def test_external_group_refs_survive_render(tmp_path: Path) -> None:
    render_module(SPEC, tmp_path)  # match real signature
    module = tmp_path / "uni_fee"
    model_py = next((module / "models").glob("*.py")).read_text()
    assert 'groups="university_base.group_treasurer"' in model_py
    rules_files = list(module.glob("security/*.xml"))
    rules = "".join(f.read_text() for f in rules_files)
    assert "university_base.group_registrar" in rules
```

NOTE: align `custom_record_rules` placement and the `security` block shape with what `preprocessors/security.py:_security_detect_record_rule_scopes` / the record_rules template actually read (existing tests in `python/tests/test_preprocessor_security*.py` or `test_security_fixes.py` show working spec shapes — copy one).

- [ ] **Step 2: Run**

Run: `cd python && uv run pytest tests/test_security_cross_module_groups.py -v`
- If PASS: capability confirmed — document it: add a short "Cross-module roles" WRONG/CORRECT pair to `education.md` §7 showing `university_base.group_*` refs.
- If FAIL: fix `_resolve_groups_value`/`_resolve_group_ref` in `preprocessors/security.py` to pass any value containing `.` through unchanged (it already special-cases dots for `implied_ids` in the template — mirror that), then re-run.

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_security_cross_module_groups.py python/src/amil_utils/preprocessors/security.py amil/knowledge/education.md
git commit -m "test(security): cross-module group references verified end-to-end"
```

---

### Task 9: Final regression + docs touch-up

- [ ] **Step 1: Full suite**

Run: `cd python && uv run pytest tests/ -m "not docker and not e2e and not e2e_slow and not odoo_ls" -q`
Expected: all pass

- [ ] **Step 2: Update repo CLAUDE.md** — add to the Key Paths table:

```
| Pakistan data | `python/src/amil_utils/data/pakistan/` |
| PK report templates | `templates/shared/pk_*.xml.j2` (template_style: pk_challan/pk_transcript/pk_degree) |
```

and add to Odoo Patterns: `Payroll: OCA payroll (OCA/payroll), never Enterprise hr_payroll`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: register Pakistan layer in CLAUDE.md"
```

---

## Self-Review Checklist

- [ ] Every PRD-blocking Tier-2 item covered: challan ✓ (Task 4), RBAC depth ✓ (Task 8 + education.md §7), PK data ✓ (Task 1), knowledge ✓ (Tasks 2-3), integrations ✓ (Task 3), reports ✓ (Task 4), questions ✓ (Task 5), localization plumbing ✓ (Task 6), agent knowledge ✓ (Task 7)
- [ ] OCA-payroll decision encoded in: education.md §9, module-questions `payroll_pk`, new-erp.md localization rules
- [ ] Module 31 (`university_qec`) encoded in: education.md §10, new-erp.md localization rules
- [ ] BPS/TTS/deduction figures carry a real `_meta.source` (no invented numbers)
- [ ] Full suite green
