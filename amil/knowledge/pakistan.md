# Odoo 17.0/18.0/19.0 Pakistan Integration & Compliance Rules

> Loaded alongside MASTER.md and education.md. Covers external-system integration
> (NADRA, SBP 1-Link/RAAST, HEC PMIS/PRMS, SMS gateways, EOBI) and statutory
> compliance (FBR withholding, identity formats, July–June fiscal year,
> Asia/Karachi timezone) for Pakistani university ERPs.
>
> **Authoritative reference data:** `python/src/amil_utils/data/pakistan/` —
> `payroll_deductions.json` (FY 2025-26 FBR slabs, EOBI, GP Fund),
> `identity_formats.json` (CNIC/NTN/STRN/phone regex). Never restate rates or
> regex inline without citing the file; figures change with every Finance Act.

## Credential Handling

### All external credentials live in ir.config_parameter, accessed with sudo()

**WRONG:**
```python
# Hardcoded token -- leaks in VCS, unrotatable, identical across deployments
NADRA_TOKEN = "sk_live_8f3a..."

def _verify_cnic(self):
    requests.post(NADRA_URL, headers={"Authorization": f"Bearer {NADRA_TOKEN}"})
```

**CORRECT:**
```python
def _verify_cnic(self):
    icp = self.env["ir.config_parameter"].sudo()
    url = icp.get_param("university.nadra_api_url")
    token = icp.get_param("university.nadra_api_token")
    if not url or not token:
        raise UserError(self.env._("NADRA integration is not configured."))
    response = requests.post(
        url,
        json={"cnic": self.cnic, "name": self.applicant_id.name},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
```

**Why:** `ir.config_parameter` keeps credentials out of code and per-deployment.
`sudo()` is required because ordinary users have no read access to system
parameters. Naming convention for every external service in this factory:
`university.<service>_api_url` / `university.<service>_api_token` /
`university.<service>_api_key`. Always pass an explicit `timeout=` — a hung
government API must never hang an Odoo worker indefinitely.

## NADRA CNIC Verification

### Verify after fee payment, never inside create(); tolerate API failure

**WRONG:**
```python
@api.model_create_multi
def create(self, vals_list):
    records = super().create(vals_list)
    for rec in records:
        rec._run_nadra_verification()  # blocks every create on a remote API
    return records
```

**CORRECT:**
```python
nadra_status = fields.Selection(
    [("pending", "Pending"),
     ("verified", "Verified"),
     ("flagged", "Flagged — Manual Review")],
    default="pending",
    tracking=True,
)

def action_confirm_fee(self):
    self.ensure_one()
    self.state = "fee_paid"
    self._run_nadra_verification()  # triggered by workflow, not by create()

def _run_nadra_verification(self):
    self.ensure_one()
    icp = self.env["ir.config_parameter"].sudo()
    last_error = None
    for _attempt in range(3):  # retry policy: 3 attempts
        try:
            response = requests.post(
                icp.get_param("university.nadra_api_url"),
                json={"cnic": self.cnic, "name": self.applicant_id.name},
                headers={"Authorization": "Bearer %s"
                         % icp.get_param("university.nadra_api_token")},
                timeout=10,
            )
            data = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(30)
    else:
        # API unreachable after 3 tries: flag for manual check, do NOT crash
        self.nadra_status = "flagged"
        self.activity_schedule(
            "mail.mail_activity_data_warning",
            summary=self.env._("NADRA unreachable — manual CNIC verification "
                               "required within 24 hours"),
            user_id=self._get_registrar_user_id(),
            note=str(last_error),
        )
        return
    if data.get("status") == "verified":
        self.nadra_status = "verified"
    else:
        self.nadra_status = "flagged"
        self.activity_schedule(
            "mail.mail_activity_data_warning",
            summary=self.env._("NADRA mismatch — manual verification required"),
            user_id=self._get_registrar_user_id(),
        )
```

**Why:** NADRA verification is a workflow step (triggered on fee confirmation,
per WF-02), not a data-entry constraint — applicants must be able to submit
even when the government API is down. A failed call degrades to a `flagged`
status plus a Registrar activity, never an exception. The applicant keeps
moving through the pipeline; a human resolves the flag.

## SBP 1-Link / RAAST Payment Webhook

### Verify the payment against the gateway; make confirmation idempotent

**WRONG:**
```python
@http.route("/payment/raast/notify", type="json", auth="public", csrf=False)
def raast_notify(self, **payload):
    challan = request.env["university.fee.challan"].sudo().search(
        [("name", "=", payload.get("challan_number"))])
    challan.payment_status = "paid"   # trusts the body; double-fires on redelivery
```

**CORRECT:**
```python
@http.route("/payment/raast/notify", type="json", auth="public",
            csrf=False, methods=["POST"])
def raast_notify(self, **payload):
    icp = request.env["ir.config_parameter"].sudo()
    secret = icp.get_param("university.raast_webhook_secret")
    signature = request.httprequest.headers.get("X-Raast-Signature", "")
    expected = hmac.new(secret.encode(), request.httprequest.data,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        _logger.warning("RAAST webhook signature mismatch")
        return {"status": "rejected"}

    challan = request.env["university.fee.challan"].sudo().search(
        [("name", "=", payload.get("challan_number"))], limit=1)
    if not challan:
        return {"status": "unknown_challan"}
    if challan.payment_status == "paid":
        return {"status": "already_processed"}   # idempotent under redelivery

    # Re-verify with the gateway before trusting the notification
    verify_url = icp.get_param("university.raast_api_url")
    response = requests.get(
        f"{verify_url}/verify/{challan.name}",
        headers={"X-API-Key": icp.get_param("university.raast_api_key")},
        timeout=10,
    )
    if response.json().get("paid"):
        challan.write({
            "payment_status": "paid",
            "payment_date": fields.Datetime.now(),
            "bank_transaction_ref": payload.get("transaction_id"),
        })
        challan.application_id.action_confirm_fee()
    return {"status": "ok"}
```

**Why:** Payment webhooks are redelivered (gateway retries) and forgeable
(public endpoint). Three defenses, all mandatory: HMAC signature check with
`hmac.compare_digest` (constant-time), idempotency guard on the already-paid
state, and a verify-back call to the gateway before flipping state. Persist
the bank transaction reference and timestamp as the audit trail. Pair the
webhook with a daily reconciliation `ir.cron` that pulls the bank's settled
list and flags challans marked paid locally but missing from the settlement —
banks reverse transactions.

## HEC PMIS / PRMS Annual Return

### Aggregate with read_group, submit via cron, log failures to chatter

**WRONG:**
```python
def cron_submit_hec_return(self):
    students = self.env["university.student"].search([("state", "=", "active")])
    by_program = {}
    for s in students:                      # N-record Python loop for a GROUP BY
        by_program.setdefault(s.program_id.name, 0)
        by_program[s.program_id.name] += 1
```

**CORRECT:**
```python
def cron_submit_hec_annual_return(self):
    settings = self.env.ref("university_hec.hec_settings_record")
    Student = self.env["university.student"]
    by_program = Student.read_group(
        [("state", "=", "active")], ["id"], ["program_id"])
    by_gender = Student.read_group(
        [("state", "=", "active")], ["id"], ["gender"])
    payload = {
        "university_code": settings.hec_university_code,
        "academic_year": str(fields.Date.context_today(self).year),
        "by_program": {g["program_id"][1]: g["program_id_count"]
                       for g in by_program if g["program_id"]},
        "by_gender": {g["gender"]: g["gender_count"] for g in by_gender},
    }
    icp = self.env["ir.config_parameter"].sudo()
    try:
        response = requests.post(
            icp.get_param("university.hec_pmis_url"),
            json=payload,
            headers={"Authorization": "Bearer %s"
                     % icp.get_param("university.hec_pmis_token")},
            timeout=30,
        )
        response.raise_for_status()
        settings.message_post(
            body=self.env._("HEC annual return submitted: %s") % payload)
    except requests.RequestException as exc:
        settings.message_post(
            body=self.env._("HEC annual return FAILED: %s") % exc)
        # do not raise: cron must not retry-storm a government endpoint
```

**Why:** `read_group` pushes the aggregation into SQL (one query per dimension,
not one per student). Success AND failure are posted to the chatter of a
well-known settings record so the Registrar has a permanent, auditable
submission history without reading server logs. The cron swallows the
exception deliberately — HEC submission failures are resolved by humans, and
`ir.cron` retry storms against PMIS get universities rate-limited.

## SMS Gateway

### Normalize Pakistani numbers before sending; dual-channel critical notices

**WRONG:**
```python
self.env["sms.sms"].create({"number": student.phone, "body": msg})
# "0300-1234567", "+92 300 1234567", "03001234567" all reach the gateway raw
```

**CORRECT:**
```python
PK_MOBILE = re.compile(r"^(\+92|0)?3\d{9}$")  # identity_formats.json: phone_pk

def _normalize_pk_mobile(self, raw):
    digits = re.sub(r"[\s\-\(\)]", "", raw or "")
    if digits.startswith("0"):
        digits = "+92" + digits[1:]
    elif digits.startswith("3"):
        digits = "+92" + digits
    if not PK_MOBILE.match(digits.replace("+92", "0", 1)) and \
       not PK_MOBILE.match(digits):
        raise ValidationError(
            self.env._("Invalid Pakistani mobile number: %s") % raw)
    return digits

def _notify_exam_bar(self):
    for student in self:
        number = self._normalize_pk_mobile(student.mobile)
        student._message_sms(
            self.env._("Attendance below 75%% in %s — barred from exam. "
                       "Contact your department.") % student.course_id.name,
            partner_ids=student.partner_id.ids,
        )
        # critical notices are dual-channel: SMS AND email
        template = self.env.ref("university_sis.mail_template_exam_bar")
        template.send_mail(student.id)
```

**Why:** The gateway (Jazz/Zong/Telenor business SMS) rejects or misroutes
non-E.164 numbers. Normalization follows the `phone_pk` pattern in
`identity_formats.json` — the `pakistan_hec` preprocessor injects the same
validation constraint on `phone_pk` fields when `localization == "pk"`, so
normalize at the boundary and validate at the model. Every critical notice
(exam bar, fee deadline, merit selection) goes out on BOTH channels: SMS
delivery in Pakistan is best-effort.

## EOBI Monthly Batch

### Generate the registration batch from payroll data, file per EOBI format

**CORRECT (pattern):**
```python
def cron_generate_eobi_batch(self):
    """Monthly batch of new staff registrations for the EOBI portal upload."""
    new_staff = self.env["hr.employee"].search([
        ("eobi_registered", "=", False),
        ("contract_ids.state", "=", "open"),
    ])
    # payroll_deductions.json: employer 5% / employee 1% of the statutory
    # minimum wage (Rs 40,000/month, FY 2025-26) => Rs 2,000 / Rs 400
    lines = [{
        "cnic": emp.identification_id,
        "name": emp.name,
        "date_of_joining": emp.first_contract_date.isoformat(),
        "monthly_contribution": 2000 + 400,
    } for emp in new_staff]
    attachment = self.env["ir.attachment"].create({
        "name": "eobi_batch_%s.csv" % fields.Date.context_today(self),
        "raw": self._render_eobi_csv(lines),
        "res_model": "university.hr.settings",
        "res_id": self.env.ref("university_hr.hr_settings_record").id,
    })
    new_staff.write({"eobi_batch_id": attachment.id})
```

**Why:** EOBI accepts monthly batch files, not an API — generate, attach to a
settings record for the audit trail, and mark employees batched so the next
run picks up only new joiners. Contribution amounts come from
`payroll_deductions.json`, never inline constants (the minimum-wage base
changes with federal budgets).

## FBR Income-Tax Withholding

### Compute from the slab table data, never hardcode rates

**WRONG:**
```python
def _compute_income_tax(self):
    for slip in self:
        slip.tax = slip.annual_gross * 0.05  # flat-rate fiction; FBR uses slabs
```

**CORRECT:**
```python
def _compute_annual_withholding(self, annual_taxable):
    """Progressive FY 2025-26 salaried slabs from payroll_deductions.json:
    up to 600,000: 0%; 1,200,000: 1% (fixed 0); 2,200,000: 11% (fixed 6,000);
    3,200,000: 23% (fixed 116,000); 4,100,000: 30% (fixed 346,000);
    above: 35% (fixed 616,000). 9% surcharge where annual taxable > 10,000,000.
    """
    slabs = self._get_fbr_slabs()  # loaded from data, seeded at install
    prev_cap = 0
    for slab in slabs:
        cap = slab["up_to"]
        if cap is None or annual_taxable <= cap:
            tax = slab["fixed"] + (annual_taxable - prev_cap) * slab["rate_percent"] / 100.0
            if annual_taxable > 10_000_000:
                tax *= 1.09  # FBR surcharge, see payroll_deductions.json
            return tax
        prev_cap = cap
    return 0.0
```

**Why:** The slab boundaries, marginal rates, AND cumulative fixed amounts all
change with every Finance Act — FY 2025-26 values live in
`payroll_deductions.json` with the FBR source recorded in `_meta.source`.
Seed them as data records (`university.tax.slab`) at module install so the
treasurer can update slabs from the UI when the next budget lands, without a
code deployment. Year-end tax certificates (employer withholding statements)
read from the same records.

## CNIC / NTN Validation Constraints

### Reference the shared formats; don't re-invent regexes per model

**WRONG:**
```python
@api.constrains("cnic")
def _check_cnic(self):
    for rec in self:
        if rec.cnic and not re.match(r"^\d{13}$", rec.cnic):  # wrong format
            raise ValidationError("Bad CNIC")
```

**CORRECT:**
```python
# identity_formats.json: cnic regex ^[0-9]{5}-[0-9]{7}-[0-9]$ (XXXXX-XXXXXXX-X)
CNIC_RE = re.compile(r"^[0-9]{5}-[0-9]{7}-[0-9]$")
NTN_RE = re.compile(r"^[0-9]{7}$|^[0-9]{13}$")  # 7-digit NTN or CNIC-based

@api.constrains("cnic")
def _check_cnic(self):
    for rec in self:
        if rec.cnic and not CNIC_RE.match(rec.cnic):
            raise ValidationError(
                self.env._("CNIC must use the format XXXXX-XXXXXXX-X."))
```

**Why:** Canonical patterns live in `identity_formats.json`. NOTE: when the
spec sets `localization: "pk"`, the `pakistan_hec` preprocessor already
injects CNIC and phone constraints on the fields it generates — add manual
constraints only for fields the preprocessor does not cover (e.g. NTN/STRN on
vendor models). Store CNIC in the dashed display format; normalize on input,
not on read.

## Fiscal Year (July–June)

### Derive the fiscal year from configuration, never the calendar year

**WRONG:**
```python
def _get_current_fiscal_year(self):
    return str(fields.Date.context_today(self).year)  # "2026" — wrong half the year
```

**CORRECT:**
```python
def _get_current_fiscal_year(self):
    """Pakistani FY runs 1 July – 30 June: June 2026 is FY 2025-26,
    July 2026 is FY 2026-27."""
    today = fields.Date.context_today(self)
    start_year = today.year if today.month >= 7 else today.year - 1
    return f"FY {start_year}-{str(start_year + 1)[-2:]}"
```

Pakistani public-sector fiscal year runs 1 July – 30 June. Configure, don't
assume the calendar year:

```xml
<record id="fiscal_year_2026" model="account.fiscal.year">
    <field name="name">FY 2025-26</field>
    <field name="date_from">2025-07-01</field>
    <field name="date_to">2026-06-30</field>
</record>
```

- Budget allocations (`crossovered.budget` or custom `university.budget`)
  span July–June; never default `date_from` to `01-01`.
- HEC annual returns and FBR tax certificates both reference the July–June
  year; derive "current FY" with a helper, not `date.today().year`.
- `res.company.fiscalyear_last_day = 30`, `fiscalyear_last_month = "6"`.

## Timezone & Locale

- Every generated user/cron defaults to `Asia/Karachi` (PKT, UTC+5, no DST):
  `<field name="tz">Asia/Karachi</field>` on `res.users` defaults and demo data.
- `ir.cron` `nextcall` is stored in UTC — a "6:00 AM PKT daily report" cron
  needs `nextcall` at 01:00 UTC. Compute with `pytz.timezone("Asia/Karachi")`,
  never by hardcoding the offset in business code.
- Currency: PKR with `decimal_places = 0` is common practice for fee
  documents (whole-rupee challans); keep `res.currency` precision at 2 and
  round at the report layer instead of mutating the currency.

## Changed in 18.0

- No integration-pattern changes; `read_group` signature unchanged for the
  aggregation usage shown here (see models.md "Changed in 18.0" for the
  general field/view changes).

## Changed in 19.0

- HTTP route type for JSON endpoints: `type="json"` becomes `type="jsonrpc"`
  (see controllers.md "Changed in 19.0"). The RAAST webhook above must use
  `type="jsonrpc"` on 19.0.
- `read_group()` replaced: use `_read_group()` / `formatted_read_group()` for
  the HEC aggregation pattern (see MASTER.md 19.0 table).
- Translations: `self.env._()` everywhere (already used in all examples here).

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `KeyError: 'ir.config_parameter'` in webhook | Used `self.env` in an `http.Controller` | Use `request.env["ir.config_parameter"].sudo()` |
| Webhook double-confirms a challan | No idempotency guard | Early-return when `payment_status == "paid"` |
| NADRA call raises `ConnectionError` and blocks admission | Verification inside `create()` | Move to workflow trigger + flagged status fallback |
| Tax computed wrong mid-year | Slabs hardcoded from an old Finance Act | Seed slabs from `payroll_deductions.json`; expose as editable data records |
| SMS silently undelivered | Number not E.164-normalized | `_normalize_pk_mobile` before `_message_sms` |
| Cron fires at the wrong hour | `nextcall` set in PKT not UTC | Convert with `pytz.timezone("Asia/Karachi")` |
| Budget report empty in January | Fiscal year assumed Jan–Dec | Configure July–June `account.fiscal.year` |
