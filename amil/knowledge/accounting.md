# Odoo 17.0/18.0/19.0 Accounting Rules

> Loaded alongside MASTER.md. Covers journal entries, invoices, payments, taxes,
> multi-currency, fiscal positions, reconciliation, credit notes, bank statements,
> analytic accounting, and chart of accounts patterns.

## Journal Entry Creation

### Create account.move with invoice lines, not without

**WRONG:**
```python
# Creating an invoice without lines -- produces an empty, unusable record
invoice = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_date": fields.Date.today(),
})
# Then trying to add lines afterwards
self.env["account.move.line"].create({
    "move_id": invoice.id,
    "product_id": product.id,
    "quantity": 1,
    "price_unit": 100.0,
})
```

**CORRECT:**
```python
from odoo import Command

invoice = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_date": fields.Date.today(),
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 100.0,
        }),
    ],
})
```

**Why:** Journal entry lines must be created atomically with the move. Creating `account.move.line` records directly bypasses the ORM's balancing logic and tax computation. The `Command.create()` pattern ensures lines, taxes, and counterpart entries are computed in a single transaction.

### Create vendor bill with correct move_type

**WRONG:**
```python
# Using out_invoice for a vendor bill
bill = self.env["account.move"].create({
    "move_type": "out_invoice",  # Wrong! This is a customer invoice
    "partner_id": vendor.id,
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 500.0,
        }),
    ],
})
```

**CORRECT:**
```python
from odoo import Command

bill = self.env["account.move"].create({
    "move_type": "in_invoice",  # Correct: vendor bill
    "partner_id": vendor.id,
    "invoice_date": fields.Date.today(),
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 500.0,
        }),
    ],
})
```

**Why:** `move_type` determines the journal, account mappings, and tax directions. `out_invoice` = customer invoice (receivable), `in_invoice` = vendor bill (payable), `out_refund` = customer credit note, `in_refund` = vendor credit note. Using the wrong type creates entries in the wrong accounts and confuses the accounting flow.

## Tax Computation

### Use account.tax.compute_all(), not manual calculation

**WRONG:**
```python
# Manual tax calculation -- fragile, ignores tax groups and rounding
price = 100.0
tax_rate = 0.15
tax_amount = price * tax_rate
total = price + tax_amount
```

**CORRECT:**
```python
taxes = product.taxes_id.filtered(
    lambda t: t.company_id == self.env.company
)
tax_result = taxes.compute_all(
    price_unit=100.0,
    currency=self.env.company.currency_id,
    quantity=1.0,
    product=product,
    partner=partner,
)
total_included = tax_result["total_included"]
total_excluded = tax_result["total_excluded"]
tax_lines = tax_result["taxes"]  # List of dicts with tax details
```

**Why:** `compute_all()` handles compound taxes, tax-included prices, rounding per currency, tax groups, and repartition lines. Manual calculation breaks when taxes are stacked, included in price, or have complex repartition rules.

## Payment Creation

### Use account.payment wizard, not direct write to account.move.line

**WRONG:**
```python
# Directly writing to move lines to "pay" an invoice -- dangerous
for line in invoice.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable"):
    line.write({"amount_residual": 0.0})
```

**CORRECT:**
```python
payment = self.env["account.payment"].create({
    "payment_type": "inbound",
    "partner_type": "customer",
    "partner_id": invoice.partner_id.id,
    "amount": invoice.amount_residual,
    "currency_id": invoice.currency_id.id,
    "journal_id": bank_journal.id,
    "payment_method_line_id": bank_journal.inbound_payment_method_line_ids[:1].id,
})
payment.action_post()

# Reconcile payment with invoice
(payment.move_id.line_ids + invoice.line_ids).filtered(
    lambda l: l.account_id.account_type in ("asset_receivable", "liability_payable")
).reconcile()
```

**Why:** Directly modifying `amount_residual` on move lines corrupts the accounting ledger. The `account.payment` model creates proper journal entries, handles outstanding accounts, updates payment state, and creates the reconciliation entries needed for accurate financial reporting.

### Register payment from invoice using the payment wizard

**WRONG:**
```python
# Creating a payment without linking it to an invoice
payment = self.env["account.payment"].create({
    "payment_type": "inbound",
    "partner_type": "customer",
    "partner_id": partner.id,
    "amount": 1000.0,
})
payment.action_post()
# Payment exists but invoice remains unpaid -- no reconciliation
```

**CORRECT:**
```python
# Use the register payment wizard from the invoice context
payment_wizard = self.env["account.payment.register"].with_context(
    active_model="account.move",
    active_ids=invoice.ids,
).create({
    "journal_id": bank_journal.id,
    "payment_date": fields.Date.today(),
})
payment_wizard.action_create_payments()
# Invoice is now automatically reconciled and marked "In Payment"
```

**Why:** The `account.payment.register` wizard automatically links the payment to the invoice(s), handles partial payments, multi-invoice payments, and performs reconciliation. Creating standalone payments requires manual reconciliation.

## Invoice Posting

### Use action_post(), not direct state assignment

**WRONG:**
```python
# Setting state directly -- skips validation, sequence, and journal entries
invoice.write({"state": "posted"})
```

**CORRECT:**
```python
invoice.action_post()
```

**Why:** `action_post()` validates the move (balanced debit/credit, required fields, fiscal locks), assigns the sequence number, posts to the ledger, triggers workflow automations, and sends email notifications. Writing `state` directly skips all validation and leaves the journal entry in an inconsistent state with no sequence number.

## Multi-Currency

### Use currency_id.compute(), not manual conversion

**WRONG:**
```python
# Manual currency conversion -- ignores Odoo's rate tables and rounding
usd_amount = 100.0
exchange_rate = 1.12
eur_amount = usd_amount * exchange_rate
```

**CORRECT:**
```python
usd = self.env.ref("base.USD")
eur = self.env.ref("base.EUR")

eur_amount = usd._convert(
    from_amount=100.0,
    to_currency=eur,
    company=self.env.company,
    date=fields.Date.today(),
)
```

**Why:** `currency._convert()` uses Odoo's `res.currency.rate` table, respects the company's rate type, handles rounding per currency decimal places, and ensures consistency across all accounting entries. Manual conversion ignores configured rates and rounding rules.

## Fiscal Position

### Use fiscal_position.map_tax(), not hardcoded tax mappings

**WRONG:**
```python
# Hardcoding tax replacements -- breaks when fiscal positions change
if partner.country_id.code == "US":
    taxes = self.env.ref("l10n_generic_coa.sale_tax_15")
elif partner.country_id.code == "EU":
    taxes = self.env.ref("l10n_generic_coa.sale_tax_0")
```

**CORRECT:**
```python
fiscal_position = self.env["account.fiscal.position"]._get_fiscal_position(
    partner=partner,
)
taxes = product.taxes_id
if fiscal_position:
    taxes = fiscal_position.map_tax(taxes)
```

**Why:** Fiscal positions define tax and account mappings declaratively. `map_tax()` applies the correct substitution based on the partner's fiscal position (configured per country, state, or VAT status). Hardcoding bypasses the entire fiscal position system and breaks when tax rules change.

## Unbalanced Journal Entry

### Ensure debit equals credit in manual journal entries

**WRONG:**
```python
from odoo import Command

# Unbalanced entry -- will raise ValidationError
move = self.env["account.move"].create({
    "move_type": "entry",
    "journal_id": misc_journal.id,
    "line_ids": [
        Command.create({
            "account_id": expense_account.id,
            "debit": 1000.0,
            "credit": 0.0,
        }),
        Command.create({
            "account_id": bank_account.id,
            "debit": 0.0,
            "credit": 900.0,  # Unbalanced! 1000 != 900
        }),
    ],
})
```

**CORRECT:**
```python
from odoo import Command

move = self.env["account.move"].create({
    "move_type": "entry",
    "journal_id": misc_journal.id,
    "line_ids": [
        Command.create({
            "account_id": expense_account.id,
            "debit": 1000.0,
            "credit": 0.0,
        }),
        Command.create({
            "account_id": bank_account.id,
            "debit": 0.0,
            "credit": 1000.0,  # Balanced: total debit == total credit
        }),
    ],
})
```

**Why:** Double-entry bookkeeping requires total debits to equal total credits. Odoo enforces this with a SQL constraint on `account.move`. Unbalanced entries raise `ValidationError` on create or post, preventing the entry from being saved.

## Account Types

### Match account type to the operation

**WRONG:**
```python
# Using an expense account for a receivable operation
invoice = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_line_ids": [
        Command.create({
            "account_id": self.env["account.account"].search(
                [("account_type", "=", "expense")], limit=1
            ).id,
            "quantity": 1,
            "price_unit": 100.0,
        }),
    ],
})
```

**CORRECT:**
```python
# Let Odoo select the correct account from the product or journal
invoice = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 100.0,
            # account_id is auto-resolved from product category or fiscal position
        }),
    ],
})
```

**Why:** When a `product_id` is set on an invoice line, Odoo automatically resolves the income/expense account from the product's category (`property_account_income_categ_id` or `property_account_expense_categ_id`), then applies fiscal position mappings. Hardcoding `account_id` bypasses product category defaults and fiscal position account substitutions. Only use explicit `account_id` for manual journal entries (`move_type="entry"`).

## Reconciliation

### Use the reconciliation API, not manual matching

**WRONG:**
```python
# Manually setting reconciled flag -- corrupts accounting state
for line in invoice.line_ids:
    if line.account_id.reconcile:
        line.write({"reconciled": True})
```

**CORRECT:**
```python
# Reconcile receivable/payable lines between invoice and payment
lines_to_reconcile = (invoice.line_ids + payment.move_id.line_ids).filtered(
    lambda l: l.account_id.account_type in ("asset_receivable", "liability_payable")
    and not l.reconciled
)
lines_to_reconcile.reconcile()
```

**Why:** The `reconcile()` method on `account.move.line` properly creates `account.partial.reconcile` or `account.full.reconcile` records, updates residual amounts, handles currency exchange differences, triggers cash-basis tax entries, and marks invoices as paid. Writing `reconciled=True` directly does none of this and corrupts the ledger.

## Payment Terms

### Use payment_term_id.compute(), not manual date calculation

**WRONG:**
```python
from datetime import timedelta

# Manual due date calculation -- breaks with complex payment terms
invoice_date = fields.Date.today()
due_date = invoice_date + timedelta(days=30)
invoice.write({"invoice_date_due": due_date})
```

**CORRECT:**
```python
# Set payment terms on the invoice and let Odoo compute due dates
invoice = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_payment_term_id": self.env.ref("account.account_payment_term_30days").id,
    "invoice_date": fields.Date.today(),
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 1000.0,
        }),
    ],
})
# Due date is computed automatically from payment term
```

**Why:** Payment terms handle complex schedules: net 30, 2/10 net 30, 50% now + 50% in 60 days, end-of-month terms, etc. They also split the receivable/payable line into multiple due-date lines automatically. Manual date calculation cannot handle installment splitting or discount terms.

## Refund / Credit Note

### Use account.move.reversal wizard, not negative invoices

**WRONG:**
```python
from odoo import Command

# Creating a negative invoice to "reverse" -- incorrect accounting
credit_note = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": -1,  # Negative quantity hack
            "price_unit": 100.0,
        }),
    ],
})
```

**CORRECT:**
```python
# Use the reversal wizard to create a proper credit note
reversal_wizard = self.env["account.move.reversal"].with_context(
    active_model="account.move",
    active_ids=original_invoice.ids,
).create({
    "reason": "Product returned",
    "refund_method": "refund",  # "refund" = draft credit note, "cancel" = post and reconcile
    "journal_id": original_invoice.journal_id.id,
})
action = reversal_wizard.reverse_moves()
credit_note = self.env["account.move"].browse(action["res_id"])
```

**Why:** The reversal wizard creates a proper credit note (`out_refund` or `in_refund` move type) with correct account mappings, links it to the original invoice, and optionally reconciles them. Negative invoice lines produce incorrect tax reports, confuse the receivable/payable ledger, and are not recognized as credit notes by fiscal authorities.

## Bank Statement

### Use account.bank.statement for bank imports, not direct entries

**WRONG:**
```python
from odoo import Command

# Creating journal entries directly for bank transactions
self.env["account.move"].create({
    "move_type": "entry",
    "journal_id": bank_journal.id,
    "line_ids": [
        Command.create({
            "account_id": bank_account.id,
            "debit": 500.0,
        }),
        Command.create({
            "account_id": receivable_account.id,
            "credit": 500.0,
        }),
    ],
})
```

**CORRECT:**
```python
from odoo import Command

# Create bank statement lines for reconciliation
statement = self.env["account.bank.statement"].create({
    "name": "Bank Statement 2024-01",
    "journal_id": bank_journal.id,
    "date": fields.Date.today(),
    "balance_start": 1000.0,
    "balance_end_real": 1500.0,
    "line_ids": [
        Command.create({
            "date": fields.Date.today(),
            "payment_ref": "Customer Payment - INV/2024/001",
            "partner_id": partner.id,
            "amount": 500.0,
        }),
    ],
})
```

**Why:** Bank statement lines feed into the reconciliation workflow, matching with open invoices, payments, and other ledger entries. Creating journal entries directly bypasses bank reconciliation, makes it impossible to match bank feeds with accounting records, and leaves the bank balance unverifiable.

## Analytic Accounting

### Use analytic distribution dict, not hardcoded analytic accounts

**WRONG:**
```python
from odoo import Command

# Hardcoding analytic account on move lines (old One2many API)
invoice = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 100.0,
            "analytic_account_id": analytic_account.id,  # Old field, removed
        }),
    ],
})
```

**CORRECT:**
```python
from odoo import Command

# Use analytic distribution (dict mapping analytic account ID -> percentage)
analytic_plan = self.env["account.analytic.plan"].search([], limit=1)
analytic_account = self.env["account.analytic.account"].create({
    "name": "Project Alpha",
    "plan_id": analytic_plan.id,
})

invoice = self.env["account.move"].create({
    "move_type": "out_invoice",
    "partner_id": partner.id,
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 100.0,
            "analytic_distribution": {str(analytic_account.id): 100.0},
        }),
    ],
})
```

**Why:** Since Odoo 17, analytic accounting uses a JSON `analytic_distribution` field (dict of `{analytic_account_id: percentage}`) instead of the old `analytic_account_id` Many2one field. The new system supports multi-plan analytics and percentage-based distribution (e.g., 60% to Project A, 40% to Project B). The old field no longer exists.

## Withholding Tax

### Use tax with correct type_tax_use, not manual deduction

**WRONG:**
```python
# Manually deducting withholding tax from payment amount
invoice_amount = 1000.0
withholding_rate = 0.10
withholding_amount = invoice_amount * withholding_rate
payment_amount = invoice_amount - withholding_amount

payment = self.env["account.payment"].create({
    "payment_type": "outbound",
    "partner_type": "supplier",
    "partner_id": vendor.id,
    "amount": payment_amount,  # 900.0 -- withholding "lost" from ledger
})
```

**CORRECT:**
```python
from odoo import Command

# Define withholding tax with correct configuration
withholding_tax = self.env["account.tax"].create({
    "name": "Withholding Tax 10%",
    "type_tax_use": "purchase",
    "amount_type": "percent",
    "amount": -10.0,  # Negative = withholding
    "tax_group_id": withholding_tax_group.id,
})

# Apply withholding tax on the vendor bill line
bill = self.env["account.move"].create({
    "move_type": "in_invoice",
    "partner_id": vendor.id,
    "invoice_line_ids": [
        Command.create({
            "product_id": product.id,
            "quantity": 1,
            "price_unit": 1000.0,
            "tax_ids": [Command.set(product.supplier_taxes_id.ids + [withholding_tax.id])],
        }),
    ],
})
```

**Why:** Withholding taxes must be tracked in the tax ledger for reporting to tax authorities. A negative-amount tax creates proper repartition lines, posts to the withholding liability account, appears in tax reports, and correctly reduces the amount payable. Manual deduction loses the tax audit trail.

## Chart of Accounts

### Use account.chart.template, not hardcoded account codes

**WRONG:**
```python
# Hardcoding account codes -- breaks across localizations
receivable_account = self.env["account.account"].search(
    [("code", "=", "1100")], limit=1
)
expense_account = self.env["account.account"].search(
    [("code", "=", "6000")], limit=1
)
```

**CORRECT:**
```python
# Search by account_type, which is consistent across localizations
receivable_account = self.env["account.account"].search(
    [
        ("account_type", "=", "asset_receivable"),
        ("company_id", "=", self.env.company.id),
    ],
    limit=1,
)
expense_account = self.env["account.account"].search(
    [
        ("account_type", "=", "expense"),
        ("company_id", "=", self.env.company.id),
    ],
    limit=1,
)
```

**Why:** Account codes vary by country localization (e.g., French COA uses 411xxx for receivables, US uses 1100). The `account_type` field is standardized across all localizations and is the correct way to find accounts programmatically. Hardcoded codes break when the module is installed in a different country.

### Valid account_type values

| `account_type` | Description | Typical Use |
|----------------|-------------|-------------|
| `asset_receivable` | Receivable | Customer invoices |
| `asset_cash` | Bank and Cash | Bank/cash journals |
| `asset_current` | Current Assets | Inventory, prepayments |
| `asset_non_current` | Non-current Assets | Fixed assets |
| `asset_prepayments` | Prepayments | Advance payments |
| `asset_fixed` | Fixed Assets | Property, equipment |
| `liability_payable` | Payable | Vendor bills |
| `liability_credit_card` | Credit Card | Credit card journals |
| `liability_current` | Current Liabilities | Short-term debt |
| `liability_non_current` | Non-current Liabilities | Long-term debt |
| `equity` | Equity | Capital, retained earnings |
| `equity_unaffected` | Current Year Earnings | Auto-computed |
| `income` | Income | Revenue accounts |
| `income_other` | Other Income | Interest, misc income |
| `expense` | Expenses | Operating expenses |
| `expense_depreciation` | Depreciation | Asset depreciation |
| `expense_direct_cost` | Cost of Revenue | COGS |
| `off_balance` | Off-Balance Sheet | Memo accounts |

## Changed in 18.0

| What Changed | Before (17.0) | Now (18.0) | Impact |
|-------------|---------------|------------|--------|
| `analytic_distribution` | JSON field on `account.move.line` | Extended with analytic plan validation | **Enhancement** -- plans are now enforced |
| `account.payment` fields | `payment_method_id` used directly | `payment_method_line_id` replaces it as primary field | **Breaking** -- code using `payment_method_id` for creation may fail |
| Tax computation | `compute_all()` returns dict | Same API, but repartition lines now include `tax_tag_invert` | **Silent change** -- custom tax processing must handle new keys |
| Bank reconciliation | Widget-based reconciliation | Refactored reconciliation model with `account.bank.statement.line` as primary | **Enhancement** -- statement lines are now the core entity |
| `account.move` `state` | `draft` / `posted` / `cancel` | Same values, but `cancel` flow uses `button_draft()` then re-post | **Workflow change** -- cancellation is now a two-step process |

## Changed in 19.0

| What Changed | Before (18.0) | Now (19.0) | Impact |
|-------------|---------------|------------|--------|
| `account.move.line` creation | Direct `create()` on move lines allowed in some cases | **Blocked** -- always create lines via `account.move` with `Command` | **Breaking** -- standalone `account.move.line.create()` raises error |
| `name_get()` on `account.move` | Deprecated | **Removed** -- use `_compute_display_name()` | **Breaking** -- see models.md |
| Tax lock date | `tax_lock_date` on company | Moved to `account.fiscal.year` scope | **Breaking** -- code reading `company.tax_lock_date` needs update |
| Payment tokens | `payment.token` model | Refactored with `payment.provider` integration | **Enhancement** -- stored payment methods streamlined |
| `read_group()` on accounting models | Standard `read_group()` | Use `_read_group()` or `formatted_read_group()` | **Breaking** -- see models.md |

## Common Errors

### `ValidationError: The move is not balanced`

The total debit does not equal total credit. Check all `line_ids` entries and ensure they sum to zero (total debit == total credit). Most common cause: manually creating `move_type="entry"` with incorrect amounts.

### `UserError: You cannot modify a posted journal entry`

Posted moves are immutable. To modify: use `button_draft()` to reset to draft (if fiscal period is not locked), make changes, then `action_post()` again. For invoices, prefer creating a credit note instead.

### `UserError: The journal has no account configured for outstanding payments/receipts`

The bank/cash journal is missing outstanding payment/receipt accounts. Configure them in Accounting > Configuration > Journals > Bank > "Outstanding Receipts Account" and "Outstanding Payments Account".

### `ValidationError: The partner is required on receivable/payable lines`

All lines with `account_type` of `asset_receivable` or `liability_payable` require a `partner_id`. Ensure the invoice has a partner set, or explicitly set `partner_id` on manual journal entry lines using receivable/payable accounts.

### `UserError: You cannot create a move line directly`

In Odoo 19.0, `account.move.line` records cannot be created independently. Always create them via `account.move` using `Command.create()` in the `line_ids` or `invoice_line_ids` field.

### `UserError: This entry is locked by a tax lock date`

The entry date falls within a tax-locked period. Either change the entry date to after the lock date or (with proper authorization) update the lock date in Accounting > Configuration > Settings.
