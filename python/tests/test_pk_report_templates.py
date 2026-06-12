"""PK report styles render via report.template_style selection.

Pakistani universities need bank-format fee challans, HEC transcripts, and
QR-verified degrees — generic report_template.xml.j2 stays the default.
"""
from __future__ import annotations

from pathlib import Path

from amil_utils.renderer import get_template_dir, render_module


def _spec(report: dict) -> dict:
    return {
        "module_name": "uni_fee",
        "module_title": "University Fees",
        "odoo_version": "19.0",
        "depends": ["base"],
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
    render_module(_spec(report), get_template_dir(), tmp_path)
    path = tmp_path / "uni_fee" / "data" / f"report_{report['xml_id']}_template.xml"
    assert path.exists(), f"report template not rendered at {path}"
    return path.read_text()


def test_pk_challan_style(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "fee_challan", "name": "Fee Challan",
        "model_name": "uni.fee.challan", "template_style": "pk_challan",
        "amount_field": "amount_total", "challan_number_field": "name",
        "due_date_field": "due_date", "bank_field": "bank_name",
        "roll_number_field": "roll_number",
        "copies": ["Bank Copy", "University Copy", "Student Copy"],
    })
    assert "Bank Copy" in content
    assert "University Copy" in content
    assert "Student Copy" in content
    assert "barcode" in content  # challan number barcode
    assert "doc.amount_total" in content


def test_pk_challan_default_copies(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "fee_challan", "name": "Fee Challan",
        "model_name": "uni.fee.challan", "template_style": "pk_challan",
        "amount_field": "amount_total", "challan_number_field": "name",
        "due_date_field": "due_date",
    })
    # three standard copies by default
    assert content.count("Copy") >= 3


def test_pk_transcript_style(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "transcript", "name": "Transcript",
        "model_name": "uni.fee.challan", "template_style": "pk_transcript",
        "student_name_field": "name", "cgpa_field": "amount_total",
        "program_field": "bank_name", "roll_number_field": "roll_number",
    })
    assert "CGPA" in content
    assert "Controller of Examinations" in content
    assert "doc.amount_total" in content


def test_pk_degree_style_has_qr(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "degree", "name": "Degree",
        "model_name": "uni.fee.challan", "template_style": "pk_degree",
        "student_name_field": "name", "serial_field": "name",
        "program_field": "bank_name",
        "verify_url_param": "university.degree_verify_url",
    })
    assert "QR" in content
    assert "verify" in content.lower()
    assert "Vice Chancellor" in content
    assert "Controller of Examinations" in content


def test_default_style_unchanged(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "plain", "name": "Plain",
        "model_name": "uni.fee.challan",
        "header_fields": [{"label": "Amount", "field": "amount_total"}],
    })
    assert "web.html_container" in content  # generic template still default
    assert "Bank Copy" not in content


def test_unknown_style_falls_back_to_default(tmp_path: Path) -> None:
    content = _render_report_xml(tmp_path, {
        "xml_id": "odd", "name": "Odd",
        "model_name": "uni.fee.challan", "template_style": "no_such_style",
        "header_fields": [{"label": "Amount", "field": "amount_total"}],
    })
    assert "web.html_container" in content
    assert "Bank Copy" not in content
