# Odoo 17.0/18.0/19.0 Education (University ERP) Rules

> Loaded alongside MASTER.md. Covers student/enrollment models, GPA/CGPA computation,
> attendance tracking, admission merit scoring, fee challans, admission state machines,
> university roles and record rules, affiliate-college patterns, OCA payroll (BPS/TTS),
> QEC/OBE outcome-based education, degree audit, and document issuance for Pakistani
> HEC universities.
>
> **Authoritative reference data:** `python/src/amil_utils/data/pakistan/` —
> `hec_grading.json`, `bps_scales.json`, `tts_scales.json`, `payroll_deductions.json`,
> `quota_categories.json`, `identity_formats.json`. Never invent grading bands, pay
> scales, or deduction rates: seed them from these files. Pakistan integration
> patterns (NADRA, RAAST, FBR, EOBI crons) live in `pakistan.md`.

## Student & Enrollment Models

### Use a dedicated student model with a partner link, not res.partner inheritance

**WRONG:**
```python
# Bolting student fields onto res.partner -- pollutes every partner record
class ResPartner(models.Model):
    _inherit = "res.partner"

    registration_number = fields.Char(string="Registration Number")
    program_id = fields.Many2one(comodel_name="university.program")
    cgpa = fields.Float(string="CGPA")
    admission_date = fields.Date(string="Admission Date")
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityStudent(models.Model):
    _name = "university.student"
    _description = "University Student"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"

    name = fields.Char(string="Full Name", required=True, tracking=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Related Partner",
        required=True,
        ondelete="restrict",
        help="Contact record for invoicing, mailing, and portal access.",
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Portal User",
        help="Set when the student is granted portal access.",
    )
    registration_number = fields.Char(
        string="Registration Number", readonly=True, copy=False
    )
    program_id = fields.Many2one(
        comodel_name="university.program", string="Program", required=True
    )
    enrollment_ids = fields.One2many(
        comodel_name="university.enrollment",
        inverse_name="student_id",
        string="Enrollments",
    )

    _registration_number_unique = models.Constraint(
        "unique(registration_number)",
        "Registration number must be unique.",
    )
```

**Why:** A student is a *role*, not a contact. Extending `res.partner` (whether via
`_inherit` field injection or `_inherits` delegation) puts academic fields on every
vendor, bank, and company in the database, breaks partner deduplication, and makes
record rules nearly impossible (a rule on `res.partner` affects all of accounting).
The dedicated model keeps academic state isolated while `partner_id` provides
invoicing/portal integration. Note the Odoo 19.0 `models.Constraint` class attribute
replacing the legacy `_sql_constraints` list (17.0/18.0: use
`_sql_constraints = [("registration_number_unique", "unique(registration_number)", "...")]`).

### Model enrollment as its own record, not Many2many or fields on the student

**WRONG:**
```python
# Courses as a bare Many2many -- no semester, no grade, no per-course state
class UniversityStudent(models.Model):
    _name = "university.student"
    _description = "University Student"

    course_ids = fields.Many2many(comodel_name="university.course")
    current_grade = fields.Char(string="Grade")  # Grade of *which* course?
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityEnrollment(models.Model):
    _name = "university.enrollment"
    _description = "Course Enrollment"
    _inherit = ["mail.thread"]

    student_id = fields.Many2one(
        comodel_name="university.student", required=True, ondelete="restrict"
    )
    course_offering_id = fields.Many2one(
        comodel_name="university.course.offering",
        string="Course Offering",
        required=True,
        ondelete="restrict",
        help="A course taught in a specific semester by a specific faculty member.",
    )
    semester_id = fields.Many2one(
        comodel_name="university.semester",
        related="course_offering_id.semester_id",
        store=True,
    )
    credit_hours = fields.Float(
        related="course_offering_id.credit_hours", store=True
    )
    grade_id = fields.Many2one(
        comodel_name="university.grade.scale", string="Grade", tracking=True
    )
    state = fields.Selection(
        selection=[
            ("enrolled", "Enrolled"),
            ("dropped", "Dropped"),
            ("graded", "Graded"),
        ],
        default="enrolled",
        tracking=True,
    )

    _student_offering_unique = models.Constraint(
        "unique(student_id, course_offering_id)",
        "A student cannot enroll twice in the same course offering.",
    )
```

**Why:** Enrollment is the join entity that carries semester, grade, credit hours,
and per-course lifecycle state. A `Many2many` cannot hold any of that, and storing a
mutable grade on the student loses history. Every GPA, attendance, and transcript
computation in this file depends on `university.enrollment` existing as a first-class
model. Never store a mutable GPA on the enrollment line either — GPA belongs on the
student/semester level as a stored computed field (next section).

## GPA / CGPA Computation

### Seed the grade scale from data, not a hardcoded Python dict

**WRONG:**
```python
# Hardcoded grading scheme -- diverges from HEC policy, untestable, unfixable by users
GRADE_POINTS = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}

def _get_points(self, percent):
    if percent >= 80:  # Invented band -- HEC A starts at 85
        return GRADE_POINTS["A"]
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityGradeScale(models.Model):
    _name = "university.grade.scale"
    _description = "HEC Grade Scale Band"
    _order = "grade_points desc"

    grade = fields.Char(required=True)
    grade_points = fields.Float(required=True, digits=(3, 2))
    min_percent = fields.Integer(required=True)
    max_percent = fields.Integer(required=True)
```

```xml
<!-- data/university_grade_scale_data.xml -- values from hec_grading.json -->
<odoo>
    <record id="grade_scale_a" model="university.grade.scale">
        <field name="grade">A</field>
        <field name="grade_points">4.0</field>
        <field name="min_percent">85</field>
        <field name="max_percent">100</field>
    </record>
    <record id="grade_scale_a_minus" model="university.grade.scale">
        <field name="grade">A-</field>
        <field name="grade_points">3.67</field>
        <field name="min_percent">80</field>
        <field name="max_percent">84</field>
    </record>
    <!-- ... remaining bands from hec_grading.json, see table below ... -->
    <record id="grade_scale_f" model="university.grade.scale">
        <field name="grade">F</field>
        <field name="grade_points">0.0</field>
        <field name="min_percent">0</field>
        <field name="max_percent">49</field>
    </record>
</odoo>
```

**Why:** HEC's semester-system scheme is policy data, not code. Universities adjust
bands; data records can be edited, exported, and audited — a Python dict cannot.
The authoritative bands (from `data/pakistan/hec_grading.json`):

| Grade | Points | Percent |
|-------|--------|---------|
| A | 4.0 | 85–100 |
| A- | 3.67 | 80–84 |
| B+ | 3.33 | 75–79 |
| B | 3.0 | 71–74 |
| B- | 2.67 | 68–70 |
| C+ | 2.33 | 64–67 |
| C | 2.0 | 61–63 |
| C- | 1.67 | 58–60 |
| D+ | 1.33 | 54–57 |
| D | 1.0 | 50–53 |
| F | 0.0 | 0–49 |

Academic standing (same file): passing CGPA **2.0**, Dean's List **3.5**,
good standing **≥ 2.0**, warning **1.5–1.99**, probation **≤ 1.49** (second
consecutive probation semester triggers rustication proceedings).

### Stored computed GPA with a full @api.depends chain

**WRONG:**
```python
# store=True with no depends -- value never recomputes when grades change
gpa = fields.Float(compute="_compute_gpa", store=True)

def _compute_gpa(self):
    for record in self:
        record.gpa = sum(e.grade_id.grade_points for e in record.enrollment_ids) / len(
            record.enrollment_ids
        )  # Unweighted average + ZeroDivisionError on new students
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversitySemesterResult(models.Model):
    _name = "university.semester.result"
    _description = "Student Semester Result"

    student_id = fields.Many2one(comodel_name="university.student", required=True)
    semester_id = fields.Many2one(comodel_name="university.semester", required=True)
    enrollment_ids = fields.One2many(
        comodel_name="university.enrollment",
        compute="_compute_enrollment_ids",
    )
    gpa = fields.Float(
        string="Semester GPA",
        digits=(3, 2),
        compute="_compute_gpa",
        store=True,
    )

    @api.depends(
        "student_id.enrollment_ids.state",
        "student_id.enrollment_ids.grade_id.grade_points",
        "student_id.enrollment_ids.credit_hours",
        "student_id.enrollment_ids.semester_id",
    )
    def _compute_gpa(self):
        for record in self:
            graded = record.student_id.enrollment_ids.filtered(
                lambda e: e.semester_id == record.semester_id
                and e.state == "graded"
                and e.grade_id
            )
            total_credits = sum(graded.mapped("credit_hours"))
            if not total_credits:
                record.gpa = 0.0
                continue
            record.gpa = (
                sum(e.grade_id.grade_points * e.credit_hours for e in graded)
                / total_credits
            )
```

**Why:** Semester GPA is **Σ(grade_points × credit_hours) / Σ(credit_hours)** — a
credit-weighted mean, never a flat average. The `@api.depends` chain must walk all
the way through `enrollment_ids.grade_id.grade_points` so that re-grading a single
enrollment (grade-change committee, recheck) invalidates the stored value. CGPA is
the same formula over *all* graded enrollments across semesters; compute it on
`university.student` with the identical depends chain minus the semester filter, and
compare against the `passing_cgpa` of **2.0** from `hec_grading.json` for promotion
and graduation checks.

## Attendance Percentage and Escalation

### Guard the division and compute per course offering

**WRONG:**
```python
# ZeroDivisionError before the first lecture; percentage stored by hand
attendance_percent = fields.Float()

def update_attendance(self):
    self.attendance_percent = (self.attended / self.held) * 100
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityAttendanceSummary(models.Model):
    _name = "university.attendance.summary"
    _description = "Per-Course Attendance Summary"

    enrollment_id = fields.Many2one(
        comodel_name="university.enrollment", required=True, ondelete="cascade"
    )
    lectures_held = fields.Integer(compute="_compute_counts", store=True)
    lectures_attended = fields.Integer(compute="_compute_counts", store=True)
    attendance_percent = fields.Float(
        compute="_compute_attendance_percent", store=True, digits=(5, 2)
    )
    exam_barred = fields.Boolean(
        compute="_compute_attendance_percent",
        store=True,
        help="Below 75% -- barred from the final exam of THIS course only.",
    )

    @api.depends("lectures_held", "lectures_attended")
    def _compute_attendance_percent(self):
        for record in self:
            if not record.lectures_held:
                record.attendance_percent = 100.0  # No lectures held yet
                record.exam_barred = False
                continue
            record.attendance_percent = (
                record.lectures_attended / record.lectures_held
            ) * 100
            record.exam_barred = record.attendance_percent < 75.0
```

**Why:** Attendance is `(attended / held) * 100` **per course offering** — a student
can be barred from one exam while sitting another, so the bar flag lives on the
per-course summary, never on the student. The zero-lecture guard prevents
`ZeroDivisionError` at semester start and avoids false alarms before teaching begins.

### Escalate shortages via cron + activity_schedule, not inside compute methods

**WRONG:**
```python
# Side effects inside a compute -- fires on every recompute, spams users
@api.depends("lectures_attended", "lectures_held")
def _compute_attendance_percent(self):
    for record in self:
        record.attendance_percent = ...
        if record.attendance_percent < 75:
            record.activity_schedule("mail.mail_activity_data_warning")  # WRONG place
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityAttendanceSummary(models.Model):
    _inherit = "university.attendance.summary"

    alert_level = fields.Selection(
        selection=[
            ("none", "None"),
            ("advisor", "Advisor Note (< 85%)"),
            ("warning", "Yellow Warning (< 80%)"),
            ("critical", "Critical / Exam Bar (< 75%)"),
        ],
        default="none",
        copy=False,
    )

    @api.model
    def _cron_attendance_shortage_alerts(self):
        """Daily cron: escalate attendance shortages. Idempotent per level."""
        summaries = self.search(
            [
                ("attendance_percent", "<", 85.0),
                ("enrollment_id.state", "=", "enrolled"),
            ]
        )
        for record in summaries:
            pct = record.attendance_percent
            level = "critical" if pct < 75 else "warning" if pct < 80 else "advisor"
            if record.alert_level == level:
                continue  # Already escalated at this level
            record.alert_level = level
            advisor = record.enrollment_id.student_id.advisor_id.user_id
            record.enrollment_id.activity_schedule(
                "mail.mail_activity_data_warning",
                user_id=advisor.id or self.env.ref("base.user_admin").id,
                summary=self.env._("Attendance shortage: %(pct).1f%%", pct=pct),
                note=self.env._(
                    "Student %(student)s is below the %(threshold)s%% threshold "
                    "in %(course)s.",
                    student=record.enrollment_id.student_id.name,
                    threshold=85 if level == "advisor" else 80 if level == "warning" else 75,
                    course=record.enrollment_id.course_offering_id.display_name,
                ),
            )
```

```xml
<record id="cron_attendance_shortage" model="ir.cron">
    <field name="name">University: Attendance Shortage Alerts</field>
    <field name="model_id" ref="model_university_attendance_summary"/>
    <field name="state">code</field>
    <field name="code">model._cron_attendance_shortage_alerts()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">days</field>
</record>
```

**Why:** Compute methods must be pure — they run on every cache invalidation, in
onchange sandboxes, and during imports, so scheduling activities there floods the
chatter. The cron + `alert_level` latch escalates exactly once per threshold:
**85%** advisor note → **80%** yellow warning → **75%** critical with course-specific
exam bar. Translations in model methods use `self.env._()` (Odoo 19.0; bare `_()`
imports trigger W8161).

## Admission Merit Score

### Configurable weights with zero-division guards and data-driven tie-breakers

**WRONG:**
```python
# Hardcoded weights, no guard against unfilled marks
def _compute_merit_score(self):
    for record in self:
        record.merit_score = (
            record.matric_obtained / record.matric_total * 10
            + record.inter_obtained / record.inter_total * 40
            + record.test_obtained / record.test_total * 50
        )
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityAdmissionApplication(models.Model):
    _inherit = "university.admission.application"

    merit_score = fields.Float(
        compute="_compute_merit_score", store=True, digits=(6, 4)
    )

    @api.model
    def _get_merit_weights(self):
        """Weights configurable per admission policy; defaults: 10/40/50."""
        icp = self.env["ir.config_parameter"].sudo()
        return (
            float(icp.get_param("university.merit_weight_matric", "10")),
            float(icp.get_param("university.merit_weight_inter", "40")),
            float(icp.get_param("university.merit_weight_test", "50")),
        )

    @api.depends(
        "matric_obtained", "matric_total",
        "inter_obtained", "inter_total",
        "test_obtained", "test_total",
    )
    def _compute_merit_score(self):
        w_matric, w_inter, w_test = self._get_merit_weights()
        for record in self:
            def ratio(obtained, total):
                return (obtained / total) if total else 0.0

            record.merit_score = (
                ratio(record.matric_obtained, record.matric_total) * w_matric
                + ratio(record.inter_obtained, record.inter_total) * w_inter
                + ratio(record.test_obtained, record.test_total) * w_test
            )

    def _sort_for_merit_list(self):
        """Tie-breaker order from quota_categories.json:
        matric_percent desc, older date_of_birth first, earlier submission first."""
        return self.sorted(
            key=lambda a: (
                -a.merit_score,
                -(a.matric_obtained / a.matric_total if a.matric_total else 0.0),
                a.date_of_birth or fields.Date.today(),
                a.submission_timestamp or fields.Datetime.now(),
            )
        )
```

**Why:** Default weighting is **matric 10% + intermediate 40% + entry test 50%**,
but admission committees change it yearly — `ir.config_parameter` makes it policy,
not a deploy. Total-marks fields arrive empty on draft applications, so every ratio
needs a zero guard. Tie-breakers come from `data/pakistan/quota_categories.json`
(`matric_percent`, `date_of_birth_older`, `submission_timestamp`); quota seats fill
in order open_merit → sports → disabled (2%) → minorities → provincial/domicile,
with unfilled quota seats transferring back to open merit.

## Fee Challan Lifecycle

### Webhook-driven payment confirmation with audit fields, never a bare "Mark Paid" button

**WRONG:**
```python
# A button anyone can click -- no payment evidence, no audit trail, not idempotent
def action_mark_paid(self):
    self.state = "paid"
    self.application_id.state = "fee_paid"
```

**CORRECT:**
```python
from odoo import api, fields, models
from odoo.exceptions import UserError


class UniversityFeeChallan(models.Model):
    _name = "university.fee.challan"
    _description = "Fee Challan"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Challan Number", readonly=True, copy=False, default="New"
    )
    state = fields.Selection(
        selection=[
            ("unpaid", "Unpaid"),
            ("paid", "Paid"),
            ("expired", "Expired"),
            ("refunded", "Refunded"),
        ],
        default="unpaid",
        tracking=True,
    )
    amount = fields.Monetary(required=True, currency_field="currency_id")
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.ref("base.PKR"),
    )
    due_date = fields.Date(required=True)
    application_id = fields.Many2one(comodel_name="university.admission.application")
    move_id = fields.Many2one(comodel_name="account.move", readonly=True, copy=False)
    # Payment evidence (audit fields) -- written ONLY by the webhook handler
    bank_ref = fields.Char(string="Bank Transaction Ref", readonly=True, copy=False)
    paid_datetime = fields.Datetime(readonly=True, copy=False)
    payment_channel = fields.Selection(
        selection=[("1link", "1-Link"), ("raast", "RAAST"), ("otc", "Over the Counter")],
        readonly=True,
        copy=False,
    )

    _challan_number_unique = models.Constraint(
        "unique(name)", "Challan number must be unique."
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "university.fee.challan"
                )
        return super().create(vals_list)

    def action_confirm_payment(self, bank_ref, paid_datetime, channel):
        """Called by the verified payment webhook (see pakistan.md). Idempotent:
        safe under gateway redelivery of the same notification."""
        self.ensure_one()
        if self.state == "paid":
            if self.bank_ref != bank_ref:
                raise UserError(
                    self.env._(
                        "Challan %(name)s already paid with ref %(ref)s.",
                        name=self.name, ref=self.bank_ref,
                    )
                )
            return True  # Redelivery of the same confirmation: no-op
        self.write({
            "state": "paid",
            "bank_ref": bank_ref,
            "paid_datetime": paid_datetime,
            "payment_channel": channel,
        })
        self._reconcile_with_move()
        if self.application_id and self.application_id.state == "submitted":
            self.application_id.state = "fee_paid"  # Advance linked workflow
        return True
```

**Why:** A paid challan is a financial fact: it needs *evidence* (bank reference,
paid timestamp, channel) and must link to an `account.move` so the ledger agrees
with the admission system. The handler is idempotent — payment gateways redeliver
webhooks, and double-advancing the application or double-posting the move corrupts
both systems. The states are `unpaid → paid / expired / refunded`; expiry runs from
a daily cron on `due_date`, and refunds go through the `account.move.reversal`
wizard (see `accounting.md`), never a state flip. The webhook controller itself
(signature verification, challan lookup) is specified in `pakistan.md`.

## State-Machine Workflows

### Gated state transitions with tracking, groups, and scheduled activities

**WRONG:**
```python
# Arbitrary state writes -- no transition validation, no audit, anyone can do anything
state = fields.Selection([...])

def jump_to_any_state(self, new_state):
    self.state = new_state
```

**CORRECT:**
```python
from odoo import api, fields, models
from odoo.exceptions import UserError


class UniversityAdmissionApplication(models.Model):
    _name = "university.admission.application"
    _description = "Admission Application"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("fee_paid", "Fee Paid"),
            ("eligible", "Eligible"),
            ("selected", "Selected"),
            ("confirmed", "Confirmed"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        copy=False,
    )

    def action_submit(self):
        for record in self:
            if record.state != "draft":
                raise UserError(self.env._("Only draft applications can be submitted."))
            record.state = "submitted"

    def action_mark_eligible(self):
        for record in self:
            if record.state != "fee_paid":
                raise UserError(
                    self.env._("Eligibility check requires a paid application fee.")
                )
            record.state = "eligible"
            record.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=record.admission_officer_id.id,
                summary=self.env._("Review merit position for selection"),
            )
```

```xml
<!-- Buttons gated by role groups defined in university_base -->
<header>
    <button name="action_submit" type="object" string="Submit"
            invisible="state != 'draft'" class="oe_highlight"/>
    <button name="action_mark_eligible" type="object" string="Mark Eligible"
            invisible="state != 'fee_paid'"
            groups="university_base.group_admission_officer"/>
    <button name="action_select" type="object" string="Select"
            invisible="state != 'eligible'"
            groups="university_base.group_admission_committee"/>
    <field name="state" widget="statusbar"
           statusbar_visible="draft,submitted,fee_paid,eligible,selected,confirmed"/>
</header>
```

**Why:** The admission lifecycle is
`draft → submitted → fee_paid → eligible → selected → confirmed / rejected / cancelled`.
Each transition validates its precondition server-side (the `invisible=` on buttons is
UX, not security), `tracking=True` writes every change to the chatter, `groups=` on
buttons plus the same check in record rules restricts who advances the workflow, and
`activity_schedule` drives approval steps instead of out-of-band emails. The
`fee_paid` transition is *only* performed by the challan webhook
(`action_confirm_payment` above) — there is no manual button for it.

## Roles & Record Rules for Universities

### Define the 26 global roles once in university_base; reference them everywhere else

**WRONG:**
```xml
<!-- university_exams/security/security.xml -- redefining a role that already exists -->
<record id="group_registrar" model="res.groups">
    <field name="name">Registrar</field>
</record>
<!-- Now there are TWO Registrar groups; users assigned to one lack the other's ACLs -->
```

**CORRECT:**
```xml
<!-- university_base/security/security.xml -- the ONLY module defining role groups -->
<record id="privilege_university" model="res.groups.privilege">
    <field name="name">University</field>
</record>
<record id="group_registrar" model="res.groups">
    <field name="name">Registrar</field>
    <!-- 19.0 uses privilege_id; 17.0/18.0 use category_id -> ir.module.category -->
    <field name="privilege_id" ref="university_base.privilege_university"/>
</record>

<!-- university_exams/security/ir.model.access.csv -- referencing, not redefining -->
<!-- access_exam_registrar,exam.registrar,model_university_exam,university_base.group_registrar,1,1,1,0 -->
```

**Why:** The 26 global roles (Registrar, Admission Officer, Controller of
Examinations, HOD, Dean, Faculty, Student, Affiliate Admin, QEC Officer, …) live in
`university_base` and are referenced cross-module as `university_base.group_*`.
Redefining a group in a second module creates a *different* group with the same
label: users end up in one and fail ACL checks on the other, and uninstalling
either module orphans assignments. In Odoo 19.0 groups are organized under
`res.groups.privilege` via `privilege_id` (17.0/18.0: `category_id` pointing at an
`ir.module.category`).

### Scope data with record rules, not Python filtering

**WRONG:**
```python
# "Security" implemented in a controller -- bypassed by any other entry point
def get_my_results(self):
    results = self.env["university.semester.result"].sudo().search([])
    return [r for r in results if r.student_id.user_id == self.env.user]
```

**CORRECT:**
```xml
<!-- Student: own records only -->
<record id="rule_student_own_results" model="ir.rule">
    <field name="name">Student: own semester results</field>
    <field name="model_id" ref="model_university_semester_result"/>
    <field name="domain_force">[('student_id.user_id', '=', user.id)]</field>
    <field name="groups" eval="[(4, ref('university_base.group_student'))]"/>
</record>

<!-- Faculty: only students enrolled in their own course offerings -->
<record id="rule_faculty_own_students" model="ir.rule">
    <field name="name">Faculty: own students</field>
    <field name="model_id" ref="model_university_student"/>
    <field name="domain_force">
        [('enrollment_ids.course_id.faculty_id.user_id', '=', user.id)]
    </field>
    <field name="groups" eval="[(4, ref('university_base.group_faculty'))]"/>
</record>

<!-- HOD: records of their own department -->
<record id="rule_hod_own_department" model="ir.rule">
    <field name="name">HOD: own department</field>
    <field name="model_id" ref="model_university_student"/>
    <field name="domain_force">
        [('program_id.department_id.hod_user_id', '=', user.id)]
    </field>
    <field name="groups" eval="[(4, ref('university_base.group_hod'))]"/>
</record>

<!-- Affiliate admin: records of their own college only -->
<record id="rule_affiliate_own_college" model="ir.rule">
    <field name="name">Affiliate: own college</field>
    <field name="model_id" ref="model_university_student"/>
    <field name="domain_force">[('college_id.admin_user_id', '=', user.id)]</field>
    <field name="groups" eval="[(4, ref('university_base.group_affiliate_admin'))]"/>
</record>
```

**Why:** Record rules are enforced by the ORM on every read/write/search regardless
of entry point (views, RPC, portal, reports); Python filtering after a `sudo()`
search is trivially bypassed and leaks counts via `search_count`. The four canonical
domains — student-own `[('user_id','=',user.id)]` (through the student link),
faculty-own-students through the enrollment chain, HOD-own-department, and
affiliate-own-college `[('college_id.admin_user_id','=',user.id)]` — cover the vast
majority of university scoping. Keep rules per-group and additive (rules of multiple
groups OR together).

## Affiliate-College Patterns

### college_id on every affiliate-scoped model + constrained submission windows

**WRONG:**
```python
# No college dimension -- affiliates see each other's data; deadline checked in JS only
class UniversityResultSubmission(models.Model):
    _name = "university.result.submission"
    _description = "Affiliate Result Submission"

    session_id = fields.Many2one(comodel_name="university.session")
    line_ids = fields.One2many(comodel_name="university.result.line",
                               inverse_name="submission_id")
```

**CORRECT:**
```python
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class UniversityResultSubmission(models.Model):
    _name = "university.result.submission"
    _description = "Affiliate Result Submission"
    _inherit = ["mail.thread"]

    college_id = fields.Many2one(
        comodel_name="university.college",
        required=True,
        index=True,
        default=lambda self: self.env.user.college_id,
    )
    session_id = fields.Many2one(comodel_name="university.session", required=True)
    submission_date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    line_ids = fields.One2many(
        comodel_name="university.result.line", inverse_name="submission_id"
    )

    @api.constrains("submission_date", "session_id")
    def _check_submission_window(self):
        for record in self:
            session = record.session_id
            if not (
                session.window_open <= record.submission_date <= session.window_close
            ):
                raise ValidationError(
                    self.env._(
                        "Submission window for %(session)s is %(open)s to %(close)s.",
                        session=session.display_name,
                        open=session.window_open,
                        close=session.window_close,
                    )
                )
```

```xml
<!-- Affiliate rule also requires an ACTIVE affiliation -->
<record id="rule_affiliate_submission" model="ir.rule">
    <field name="name">Affiliate: own college, active affiliation</field>
    <field name="model_id" ref="model_university_result_submission"/>
    <field name="domain_force">
        [('college_id.admin_user_id', '=', user.id),
         ('college_id.affiliation_state', '=', 'active')]
    </field>
    <field name="groups" eval="[(4, ref('university_base.group_affiliate_admin'))]"/>
</record>
```

**Why:** Every model an affiliate college touches needs an indexed `college_id` —
it is the tenancy dimension for record rules, reporting, and quotas. The active
affiliation check belongs in the rule domain so a lapsed college loses access the
moment its affiliation expires, with no code change. Submission deadlines are
enforced with `@api.constrains` against the session's open/close datetimes —
server-side, so RPC and CSV imports obey them too, not just the web form.

## Payroll (OCA)

### Depend on OCA payroll, never the Enterprise module

**WRONG:**
```python
# __manifest__.py -- Enterprise-only dependency; breaks on Community installs
{
    "name": "University Payroll PK",
    "depends": ["hr_payroll"],
    "license": "LGPL-3",
}
```

**CORRECT:**
```python
# __manifest__.py -- OCA payroll (repo: OCA/payroll), works on Community
{
    "name": "University Payroll PK",
    "version": "19.0.1.0.0",
    "depends": [
        "payroll",          # OCA/payroll: structures, rules, payslips
        "payroll_account",  # OCA/payroll: payslip -> account.move posting
    ],
    "license": "LGPL-3",
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/hr_payroll_structure_bps_data.xml",
        "data/hr_salary_rule_deductions_data.xml",
        "views/hr_payslip_views.xml",
        "views/menu.xml",
    ],
}
```

**Why:** Pakistani public universities run Odoo Community; the Enterprise payroll
module is OEEL-licensed and unavailable to them. The OCA `payroll` module
(github.com/OCA/payroll) provides `hr.payroll.structure`, `hr.salary.rule`,
`hr.salary.rule.category`, and `hr.payslip` with the same rule-engine concepts.
This is a locked architecture decision for every university ERP this factory
generates — `amil-utils check-edition` flags the Enterprise dependency as an error.
Note the manifest load order: security → data → wizard views → model views →
dashboard → menu.

### Seed BPS/TTS structures and statutory deductions from the reference JSON

**WRONG:**
```xml
<!-- Invented pay figures -- BPS-17 minimum is NOT 50,000 -->
<record id="rule_basic_bps17" model="hr.salary.rule">
    <field name="code">BASIC</field>
    <field name="amount_select">fix</field>
    <field name="amount_fix">50000</field>
</record>
```

**CORRECT:**
```xml
<!-- data/hr_payroll_structure_bps_data.xml -- figures from bps_scales.json
     (BPS-2022 base scales, operative through FY 2025-26) -->
<record id="structure_bps" model="hr.payroll.structure">
    <field name="name">BPS (Basic Pay Scale) Staff</field>
    <field name="code">BPS</field>
</record>

<!-- Example seeded scale rows (university.pay.scale, seeded from bps_scales.json):
     BPS-1:  min 13,550  max 26,450   increment 430    (30 stages)
     BPS-17: min 45,070  max 113,470  increment 3,420  (20 stages)
     BPS-22: min 122,190 max 244,130  increment 8,710  (14 stages)
     TTS (tts_scales.json, lump-sum, no separate allowances):
     Assistant Professor: 175,500 - 356,475, increment 12,065 (15 stages) -->

<record id="rule_eobi_employee" model="hr.salary.rule">
    <field name="name">EOBI Employee Contribution</field>
    <field name="code">EOBIEE</field>
    <field name="category_id" ref="payroll.DED"/>
    <field name="struct_id" ref="structure_bps"/>
    <field name="amount_select">code</field>
    <field name="amount_python_compute">
# payroll_deductions.json: 1% employee share on the statutory minimum wage
# (Rs 40,000/month FY 2025-26), NOT on actual salary => Rs 400/month
result = -(40000 * 0.01)
    </field>
</record>

<record id="rule_gp_fund" model="hr.salary.rule">
    <field name="name">GP Fund Subscription</field>
    <field name="code">GPF</field>
    <field name="category_id" ref="payroll.DED"/>
    <field name="struct_id" ref="structure_bps"/>
    <field name="amount_select">code</field>
    <field name="amount_python_compute">
# payroll_deductions.json fixed monthly bands (O.M. 31-08-2022), e.g.
# BPS-1: 600, BPS-11: 1,920, BPS-17: 6,350, BPS-22: 14,660.
# Not applicable to TTS faculty.
result = -contract.employee_id.gp_fund_band_amount
    </field>
</record>
```

**Why:** All payroll figures are statutory and live in
`data/pakistan/bps_scales.json`, `tts_scales.json`, and `payroll_deductions.json` —
never hardcode invented numbers. EOBI is **5% employer / 1% employee on the
Rs 40,000 statutory minimum wage** (Rs 2,000 / Rs 400 per month, FY 2025-26 — not a
percentage of actual salary). GP Fund uses fixed monthly amounts per BPS (derived
from 3% of scale mean for BPS-1, 5% for BPS-2–11, 8% for BPS-12–22) and does **not**
apply to TTS faculty, whose lump-sum packages exclude separate allowances. FBR
income-tax withholding (FY 2025-26 slabs) is specified in `pakistan.md`. HRA on BPS
is 45% (big cities) / 30% (other stations) of the 2008-scale minimum — keep these
as data, with the JSON `_meta.source` notes as the audit trail.

## QEC / OBE (Module 31 — university_qec)

### Compute CLO/PLO attainment from assessment results, never store it manually

**WRONG:**
```python
# Attainment typed in by hand -- defeats the entire OBE evidence chain
class UniversityCLO(models.Model):
    _name = "university.clo"
    _description = "Course Learning Outcome"

    attainment_percent = fields.Float(string="Attainment %")  # Manually edited
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityCLO(models.Model):
    _name = "university.clo"
    _description = "Course Learning Outcome"

    code = fields.Char(required=True)  # e.g. CLO-1
    name = fields.Char(string="Statement", required=True)
    course_id = fields.Many2one(comodel_name="university.course", required=True)
    bloom_level = fields.Selection(
        selection=[
            ("remember", "Remember"), ("understand", "Understand"),
            ("apply", "Apply"), ("analyze", "Analyze"),
            ("evaluate", "Evaluate"), ("create", "Create"),
        ],
        required=True,
    )
    plo_mapping_ids = fields.One2many(
        comodel_name="university.clo.plo.map", inverse_name="clo_id"
    )
    assessment_line_ids = fields.One2many(
        comodel_name="university.assessment.result.line", inverse_name="clo_id"
    )
    attainment_percent = fields.Float(
        compute="_compute_attainment", store=True, digits=(5, 2)
    )

    @api.depends("assessment_line_ids.obtained", "assessment_line_ids.total")
    def _compute_attainment(self):
        """% of students scoring >= the attainment threshold on items mapped
        to this CLO (KPI threshold configurable, default 50%)."""
        threshold = float(
            self.env["ir.config_parameter"].sudo().get_param(
                "university.clo_attainment_threshold", "50"
            )
        )
        for record in self:
            lines = record.assessment_line_ids.filtered("total")
            if not lines:
                record.attainment_percent = 0.0
                continue
            achieved = lines.filtered(
                lambda l: (l.obtained / l.total) * 100 >= threshold
            )
            record.attainment_percent = (len(achieved) / len(lines)) * 100


class UniversityCloPloMap(models.Model):
    _name = "university.clo.plo.map"
    _description = "CLO-PLO Mapping Matrix Cell"

    clo_id = fields.Many2one(comodel_name="university.clo", required=True,
                             ondelete="cascade")
    plo_id = fields.Many2one(comodel_name="university.plo", required=True,
                             ondelete="restrict")
    emphasis = fields.Selection(
        selection=[("1", "Low"), ("2", "Medium"), ("3", "High")], default="2"
    )

    _clo_plo_unique = models.Constraint(
        "unique(clo_id, plo_id)", "Each CLO-PLO pair maps once."
    )
```

**Why:** `university_qec` (Module 31) ships in **every** Pakistani university ERP —
QEC/OBE compliance is mandatory for HEC accreditation, so the decomposer always
includes it. The model chain is PEO (program educational objectives) ← PLO (program
learning outcomes) ← CLO (course learning outcomes) ← assessment result lines; PLO
attainment aggregates mapped CLO attainments weighted by `emphasis`. Attainment must
be *computed from evidence* (assessment results) for HEC's Institutional Performance
Evaluation — a manually-typed float fails the audit. Course self-assessment reports
(SAR) reference these computed values, and the HEC IPE checklist items are seeded as
`university.qec.checklist.item` data records (one record per IPE standard) so the
QEC officer ticks evidence against data, not against a PDF.

## Degree Audit & Graduation

### Audit credit hours against program requirements; clear no-dues per department

**WRONG:**
```python
# One boolean, writable by anyone -- no per-department accountability
class UniversityStudent(models.Model):
    _inherit = "university.student"

    is_cleared = fields.Boolean(string="No Dues Cleared")

    def action_graduate(self):
        if self.is_cleared:
            self.state = "graduated"
```

**CORRECT:**
```python
from odoo import api, fields, models
from odoo.exceptions import UserError


class UniversityClearance(models.Model):
    _name = "university.clearance"
    _description = "Graduation No-Dues Clearance"
    _inherit = ["mail.thread"]

    student_id = fields.Many2one(comodel_name="university.student", required=True)
    line_ids = fields.One2many(
        comodel_name="university.clearance.line", inverse_name="clearance_id"
    )
    state = fields.Selection(
        selection=[("pending", "Pending"), ("cleared", "Cleared")],
        compute="_compute_state", store=True, tracking=True,
    )

    @api.depends("line_ids.state")
    def _compute_state(self):
        for record in self:
            lines = record.line_ids
            record.state = (
                "cleared" if lines and all(l.state == "cleared" for l in lines)
                else "pending"
            )


class UniversityClearanceLine(models.Model):
    _name = "university.clearance.line"
    _description = "Departmental Clearance Line"

    clearance_id = fields.Many2one(comodel_name="university.clearance",
                                   required=True, ondelete="cascade")
    department_id = fields.Many2one(comodel_name="university.department",
                                    required=True)  # Library, Hostel, Accounts, Lab
    state = fields.Selection(
        selection=[("pending", "Pending"), ("cleared", "Cleared")],
        default="pending",
    )
    cleared_by_id = fields.Many2one(comodel_name="res.users", readonly=True)

    def action_clear(self):
        for record in self:
            group = record.department_id.clearance_group_id
            if group and not self.env.user.has_group(
                f"{group.get_external_id()[group.id]}"
            ):
                raise UserError(
                    self.env._("Only %(dept)s staff can clear this line.",
                               dept=record.department_id.name)
                )
            record.write({"state": "cleared", "cleared_by_id": self.env.user.id})


class UniversityStudent(models.Model):
    _inherit = "university.student"

    credits_earned = fields.Float(compute="_compute_credits_earned", store=True)

    @api.depends("enrollment_ids.state", "enrollment_ids.credit_hours",
                 "enrollment_ids.grade_id.grade_points")
    def _compute_credits_earned(self):
        for record in self:
            record.credits_earned = sum(
                record.enrollment_ids.filtered(
                    lambda e: e.state == "graded"
                    and e.grade_id.grade_points > 0  # F earns no credit
                ).mapped("credit_hours")
            )

    def action_check_graduation(self):
        self.ensure_one()
        errors = []
        if self.credits_earned < self.program_id.required_credit_hours:
            errors.append(self.env._(
                "Credits: %(earned).1f of %(required).1f",
                earned=self.credits_earned,
                required=self.program_id.required_credit_hours,
            ))
        if self.cgpa < 2.0:  # passing_cgpa from hec_grading.json
            errors.append(self.env._("CGPA %(cgpa).2f below required 2.00",
                                     cgpa=self.cgpa))
        if errors:
            raise UserError("\n".join(errors))
        return True
```

**Why:** Graduation is a multi-criteria audit: earned credit hours (F grades earn
none) vs. the program's required total, CGPA ≥ the HEC passing threshold of **2.0**,
plus a no-dues clearance where *each* department (library, hostel, accounts, labs)
owns its own line — a One2many of clearance lines, each clearable only by that
department's group. A single boolean has no accountability trail and lets one office
clear another's dues. The parent clearance state is computed from the lines, never
written directly.

## Document Issuance (Transcripts, Degrees, Certificates)

### QWeb with ir.sequence serials, QR verification, and an issuance audit log

**WRONG:**
```python
# Hand-rolled serial + no verification path -- forgeable, unauditable
def action_print_transcript(self):
    self.transcript_no = f"TR-{self.id}"  # IDs leak row counts; not gap-free policy
    return self.env.ref("university_exams.report_transcript").report_action(self)
```

**CORRECT:**
```python
from odoo import api, fields, models


class UniversityDocumentIssue(models.Model):
    _name = "university.document.issue"
    _description = "Issued Document Audit Log"
    _order = "create_date desc"

    name = fields.Char(string="Serial", readonly=True, copy=False, default="New")
    student_id = fields.Many2one(comodel_name="university.student", required=True)
    doc_type = fields.Selection(
        selection=[
            ("transcript", "Transcript"),
            ("degree", "Degree"),
            ("provisional", "Provisional Certificate"),
        ],
        required=True,
    )
    issued_by_id = fields.Many2one(
        comodel_name="res.users", default=lambda self: self.env.user, readonly=True
    )
    verification_token = fields.Char(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        import secrets
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "university.document.issue"
                )
            vals.setdefault("verification_token", secrets.token_urlsafe(16))
        return super().create(vals_list)

    def _get_verification_url(self):
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return f"{base_url}/verify/document/{self.verification_token}"
```

```xml
<record id="seq_document_issue" model="ir.sequence">
    <field name="name">University Document Serial</field>
    <field name="code">university.document.issue</field>
    <field name="prefix">DOC/%(year)s/</field>
    <field name="padding">6</field>
</record>

<!-- QWeb: QR code rendered via the barcode controller -->
<template id="report_degree_document">
    <t t-call="web.external_layout">
        <div class="page">
            <h2 t-field="doc.student_id.name"/>
            <p>Serial: <span t-field="doc.name"/></p>
            <img t-att-src="'/report/barcode/?barcode_type=QR&amp;value=%s&amp;width=120&amp;height=120'
                            % doc._get_verification_url()"
                 alt="Verification QR"/>
        </div>
    </t>
</template>
```

**Why:** Degree fraud is the threat model: every issued document needs an
`ir.sequence` serial (year-prefixed, padded, gap-policy auditable), a random
verification token (never the database ID — IDs are enumerable), a QR code pointing
at a public verification controller that shows name/program/serial for a valid
token, and an immutable audit-log record of who issued what and when. The QR image
uses Odoo's built-in `/report/barcode/?barcode_type=QR&value=...` controller — no
extra Python dependency. Use `t-field`/`t-out` in QWeb (19.0 deprecates `t-esc`).

## Changed in 18.0

| What Changed | Before (17.0) | Now (18.0) | Impact |
|-------------|---------------|------------|--------|
| List views for enrollments/results | `<tree>` | `<list>` | Update all list views and `view_mode` |
| `group_operator` on numeric fields (credits, GPA) | `group_operator="avg"` | `aggregator="avg"` | Rename in field definitions |
| `states=` on workflow fields | Available | **Removed** | Use `readonly="state != 'draft'"` in views |

## Changed in 19.0

| What Changed | Before (18.0) | Now (19.0) | Impact |
|-------------|---------------|------------|--------|
| SQL constraints | `_sql_constraints` list | `models.Constraint` class attribute | All uniqueness constraints above use the new form |
| Translations in methods | `from odoo import _` | `self.env._()` | All examples above; W8161 otherwise |
| Group categories | `category_id` (ir.module.category) | `privilege_id` (res.groups.privilege) | university_base security XML |
| Domain composition | `expression.OR/AND` | `Domain` class (`from odoo.fields import Domain`) | Merit-list and dashboard domain building |
| Internal helpers | `_`-prefix convention only | `@api.private` decorator for non-RPC methods | Apply to webhook/cron helpers with public-looking names |
| `name_get()` | Deprecated | **Removed** | Use `_compute_display_name()` on student/enrollment |

## Common Errors

### `ValidationError: A student cannot enroll twice in the same course offering`

The `unique(student_id, course_offering_id)` constraint fired. Re-enrollment after
an F grade requires a *new course offering* (next semester's), not a second
enrollment in the same one.

### GPA stays stale after a grade change

The `@api.depends` chain is incomplete — it must reach
`enrollment_ids.grade_id.grade_points` (three hops), not stop at
`enrollment_ids.grade_id`. Stored computes only invalidate along declared paths.

### `AccessError` for faculty viewing their own class list

The faculty record rule traverses
`enrollment_ids.course_id.faculty_id.user_id`; if course offerings link faculty via
a different field name, the rule domain silently matches nothing. Keep the chain
field names canonical.

### Payslip computes Rs 0 for EOBI

EOBI rules compute on the statutory minimum wage constant (Rs 40,000 FY 2025-26),
not `contract.wage`. If the rule references a missing helper field, the
`amount_python_compute` sandbox returns 0 silently — check the payslip computation
log.
