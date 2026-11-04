# QuantBud accounting guide (Stage 9)

Applies to the solo, localhost-only install. **This is not tax or legal advice.** Have a Chartered Accountant check the GST, TDS, PF/ESI/PT and closing treatment before you rely on it for filings.

## 1. Module map
| App | What it owns | Ledger role |
|---|---|---|
| `invoice_app` | Customers, invoices, receipts, credit notes, PDF formats | Posts invoices on finalize, plus their receipts and credit notes (`accounting.postings`) |
| `expense_management` | Vendors, company expenses, vendor payments, employee claims, reimbursements | Posts bills on save, plus payments, approved claims and reimbursements (`expense_management.ledger`) |
| `employee_management` | Employees, payroll, salary payments, advances/loans, bank exports | Posts payroll per month on finalize, plus salary payments, advances, repayments and write-offs |
| `attendance` | Attendance, leave, holidays | No postings. It feeds payroll (LOP days) |
| `accounting` | Chart of accounts, journal entries, periods, mappings | The core: `services` (validate, post, reverse, close) and `postings` (`sync`, which is idempotent) |
| `bank_management` | Bank/cash accounts, opening balances, statement import, matching, transfers | Posts openings, transfers and manual bank entries. Reconciliation works on journal lines |
| `compliance` | Statutory rules, statutory payments, GST input claims | Posts payments of GST/TDS/PF/ESI/PT and the input-GST reclassification |
| `reporting` | Management reports and ledger reports (TB, GL, P&L, BS, cash flow, ageing, day book, reconciliation) | Read-only |
| `document_management` | Document register | None |
| `audit` | Append-only AuditEvent log (`/audit/`) | Records saves and deletes of the audited models, plus manual events |
| `backup` | ZIP backups, verify, restore, retention (`/backups/`) | None |

## 2. Posting rules (event → Dr / Cr)
"Bank" means the chosen BankAccount's ledger account. With no bank chosen, it is the module's `*.payment_bank` / `invoice.receipt_bank` mapping, or `cash.default` for cash.

| Event | Debit | Credit | Source type |
|---|---|---|---|
| Bank opening balance (positive) | Bank | `bank.opening_equity` | `bank_opening` |
| Invoice finalized | `invoice.ar` grand total (party customer) | `invoice.revenue` taxable value; `gst.output.cgst/sgst/igst` | `invoice` |
| Receipt | Bank (amount); `invoice.tds_receivable` (deductions − bank charges); `bank.charges` | `invoice.ar` (amount + deductions) | `invoice_receipt` |
| Credit note | `invoice.revenue` taxable; `gst.output.*` | `invoice.ar` total | `credit_note` |
| Invoice cancel | Reversal of the invoice entry (only when there are no receipts) | | |
| Company expense (bill) | `expense.category.<id>`, else `expense.default` (gross, GST included) | `expense.ap` (party vendor) | `company_expense` |
| Vendor payment | `expense.ap` (amount + TDS) | Bank (amount); `expense.tds_payable` (TDS) | `expense_payment` |
| Input GST marked eligible | `gst.input.cgst/sgst/igst` | Expense account of the bill | `gst_input_claim` |
| Claim approved | `claim.category.<id>`, else `claim.expense_default` | `claim.reimbursement_payable` (party employee) | `expense_claim` |
| Reimbursement | `claim.reimbursement_payable` | Bank | `reimbursement` |
| Payroll month finalized | `payroll.salary_expense` (gross + rounding); `payroll.employer_contribution` | `payroll.salary_payable` net (party employee); `payroll.pf/esi/pt/tds_payable`; `payroll.other_deductions_payable`; `payroll.advance` (advance recovery) | `payroll` |
| Salary payment | `payroll.salary_payable` | Bank | `salary_payment` |
| Advance disbursed | `payroll.advance` (principal + interest) | Bank (principal); `payroll.advance_interest` | `employee_advance` |
| Advance repaid in cash | Bank | `payroll.advance` | `advance_repayment` |
| Advance written off | `payroll.advance_writeoff` | `payroll.advance` | `advance_writeoff` |
| Statutory payment | The payable key for its kind (`gst.output.*`, `expense.tds_payable`, `payroll.*_payable`) | Bank | `statutory_payment` |
| Bank transfer | To-bank | From-bank | `bank_transfer` |
| Manual bank entry | Bank, or the counter account | Counter account, or bank | `bank_manual` |

Every source posts through `postings.sync(source_key, …)`:
- If nothing changed, it does nothing.
- If anything changed, it reverses the old entry and posts the new one.
- A cancelled source is reversed.

Entries are never edited in place, and finalized payslips are never rewritten; a revision posts a new entry.

## 3. Mappings
`/accounting/mappings/` maps each key to a posting account. Code always uses keys and never hard-coded account codes. The seeded keys are:

`invoice.ar, invoice.revenue, invoice.receipt_bank, invoice.tds_receivable, bank.charges, bank.opening_equity, gst.output.cgst/sgst/igst, gst.input.cgst/sgst/igst, expense.ap, expense.default, expense.payment_bank, expense.tds_payable, claim.expense_default, claim.payment_bank, claim.reimbursement_payable, payroll.salary_expense, payroll.employer_contribution, payroll.salary_payable, payroll.pf_payable, payroll.esi_payable, payroll.pt_payable, payroll.tds_payable, payroll.other_deductions_payable, payroll.advance, payroll.advance_interest, payroll.advance_writeoff, payroll.payment_bank, cash.default, income.other, equity.retained_earnings`.

You can add optional per-category overrides: `expense.category.<pk>` and `claim.category.<pk>`.

A changed mapping affects new postings only. To move history, re-sync it or post a journal.

## 4. Closing periods
- Periods are calendar months inside a financial year (1 Apr – 31 Mar). Manage them at `/accounting/periods/`.
- **Close** needs a reason. After closing, any posting dated in the period is rejected with a `ValidationError`, from every module: invoices, payments, payroll finalize, bank entries and statement-matched journals. `sync` checks this as well.
- **Reopen** needs a reason and is audited. Reopen, fix the problem, then close again. Don't back-date entries into closed months any other way.
- Reversals are dated in an open period: the reversal date defaults to today, or choose one.
- There is no year-end closing entry. The balance sheet shows "current year earnings" and "prior unclosed earnings" computed from P&L.

## 5. Backup and restore
See `docs/BACKUP_AND_AUDIT.md`. In short:
- `manage.py create_backup --note "..." --verify` writes a ZIP to `BACKUP_DIR` (default `E:\QuantBud_Backups`). The ZIP holds the DB, private_media, media and a manifest with SHA-256s.
- `verify_backup <name>` checks the ZIP and its integrity.
- `restore_backup <name> --phrase ...` first makes a safety backup, then restores. Stop the server first.
- `backup_retention` keeps the last 10 plus protected backups.
- Use the UI at `/backups/`, or `run_backup.bat`.
- Back up before every migrate.

## 6. Backfill commands (both dry-run unless `--apply`)
- `manage.py backfill_invoice_ledger [--as-of YYYY-MM-DD] [--apply]` posts existing finalized invoices and their receipts and credit notes.
- `manage.py backfill_expense_ledger [--apply]` posts existing expenses, vendor payments, approved claims and reimbursements.
- Payroll posts when a month is finalized. For old finalized months, `accounting.postings.post_payroll(period)` is idempotent.
- Bank openings post when the bank account is saved (`sync_opening`).

Run a backup first, run the dry-run, read the output, then `--apply`. Re-running is a no-op.

## 7. Compliance caveats (verify with a CA)
- **GST:** expenses post gross. Input credit appears only when you mark it eligible (compliance → GST input). Reverse charge, blocked credits (s.17(5)), RCM on imports and e-invoicing are not modelled. Export/LUT invoices post 0 GST.
- **TDS:**
  - Receivable TDS (from customer deductions) sits in `invoice.tds_receivable` until you offset it against Form 26AS. There is no automated 26AS reconciliation.
  - Vendor TDS posts to `expense.tds_payable` with no section-wise rates or thresholds; enter amounts yourself.
  - Salary TDS is whatever the payroll deduction says.
- **PF/ESI/PT:** they follow the statutory rules you configure. Examples load with `load_example_compliance_rules`. No rules means no deductions, as in the E2E test. Check the ceilings and state PT slabs.
- **Foreign-currency invoices** are not posted to the ledger. Foreign expenses are converted at the entered exchange rate. There is no forex gain/loss.
- The reports are management aids, not statutory financial statements (Schedule III).

## 8. Known limits
- **Bulk updates are not audited.** QuerySet `.update()` bypasses signals; `services.reverse` uses it to mark the original as reversed. The reversal entry itself is audited.
- **Recurring expenses are not built.** Enter each bill.
- **Legacy free-text bank fields:** older records (expense `bank_account`, reimbursement/payroll bank text) may hold free text rather than a BankAccount FK. Those post to the module's default bank mapping. `/bank/legacy-preview/` lists them.
- There are no users or logins; the app is guarded only by localhost. The audit operator is always "Local operator (no login)".
- Matching on bank statements is manual. Suggestions exist, but there is no auto-match.
- Pinned PDF SHA-256 tests depend on the installed fonts and fail on machines with different fonts.

## 9. Security settings (Stage 9)
- `DEBUG` is opt-in (`run_windows.bat` sets `QB_DEBUG=1`). WhiteNoise serves CSS/static files with either value; `collectstatic` prepares assets for deployments.
- `SECRET_KEY` comes from env `QB_SECRET_KEY`, else from `private_media/.secret_key`, which is auto-generated and git-ignored. If that file can't be read or written, a per-process key is used; the only effect is that flash cookies reset on restart.
- `ALLOWED_HOSTS = 127.0.0.1, localhost` (`testserver` is added only under `manage.py test`).
- `config.middleware.LocalOnlyMiddleware` returns 403 for any non-loopback client, on every page.
- Payroll bank-transfer exports mask account numbers to the last 4 digits. "Bank upload Excel (full A/c)" (`?full=1`) gives full numbers for the bank portal.
- `check --deploy` warnings that are not applicable to a localhost, HTTP-only app: W004 (HSTS), W008 (SSL redirect), W016 (CSRF cookie secure).

## 10. Migrations per app (final, Stage 9)
| App | Migrations |
|---|---|
| invoice_app | 0001_initial … 0012_invoice_formats (0002 options, 0003 billing_type, 0004 terms_type, 0005 active_flags, 0006 billing_month, 0007 receipts_tax, 0008/0009 description templates, 0010 stage4_ledger, 0011 gst_tax_invoice, 0012 invoice_formats) |
| employee_management | 0001_initial, 0002_employeepayroll_attendance_snapshot_and_more, 0003_salary_payment, 0004_employee_advances |
| attendance | 0001_initial, 0002_seed_leave_types, 0003_attendance_correction |
| expense_management | 0001_initial, 0002_seed_categories, 0003_stage4_ledger |
| accounting | 0001_initial, 0002_seed_chart_of_accounts, 0003_line_party_payroll_other, 0004_advance_mappings |
| bank_management | 0001_initial, 0002_opening_mapping |
| compliance | 0001_initial |
| document_management | 0001_initial |
| audit | 0001_initial |
| backup | 0001_initial |
| reporting | none (no models) |

Stage 9 adds **no migrations**.
