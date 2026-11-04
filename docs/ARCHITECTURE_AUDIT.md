# QuantBud Business Management - Architecture Audit (Stage 1)

Date: 2026-11-03 (IST). Scope: audit and backups only. No code or schema changes were made in this stage.

## 0. Backup and baseline
- Backup folder: `E:\QuantBud_Backups\pre_accounting_20261103_073941\`
  - `db.sqlite3` is a copy made with the sqlite backup API. It is 643,072 bytes. `integrity_check` returned ok and `foreign_key_check` found 0 violations.
  - `media.zip` holds 1 entry, `invoice_app/assets/signature.png`. The media/, private_media/, uploads/ and signatures/ folders do not exist, so they contributed 0 files.
  - `source.zip` holds 202 files. It excludes .venv, caches, media, the DB and .git.
  - `manifest.json` records a sha256 for every file, the table counts and the ZIP verification. `testzip()` found no problems in either ZIP.
- Live data:
  - Invoices: 0 invoices, invoice lines and payments; 3 clients; 1 BusinessProfile.
  - Employees and payroll: 1 employee; 0 payroll periods and 0 payslips.
  - Attendance: 30 attendance records.
  - Expenses: 5 company expenses with 4 payments; 6 claims with 2 reimbursements.
- Migrations: all applied. attendance 0001-0002, employee_management 0001-0002, expense_management 0001-0002, invoice_app 0001-0007.
- Baseline `manage.py test`: **230 tests, OK.** One earlier run failed on a flaky test, explained below.
  - The test is `employee_management.tests.DocumentTests.test_valid_upload_private_and_controlled`.
  - It asserts that `'a.pdf'` does not appear in a random uuid filename.
  - The test fails whenever the uuid hex ends in `a`, which happens about 1 time in 16.
  - The fix (to be made later) is to assert that the original name is not the basename.

## 1. Existing functionality
- **invoice_app**
  - Records: BusinessProfile (the single company profile, including bank details and invoice numbering), Client, ProjectTemplate, Invoice and InvoiceLine.
  - InvoicePayment was added in 0007 to hold receipts and TDS.
  - Output: ReportLab PDF (the layout is frozen) and Excel export.
  - Numbering uses `select_for_update` (services.py:201-213).
- **employee_management**
  - Records: Department, Employee (with a change log), EmployeeBankAccount and EmployeeDocument (private storage).
  - Salary: SalaryStructure and SalaryComponent.
  - Payroll: PayrollSettings, PayrollPeriod and EmployeePayroll. Payroll runs draft -> finalize, and a finalized payslip is frozen with snap_* fields. Revisions are made through `revises` and `is_current`.
  - Other: payslip PDF, bank-file export, and a `backup_data` command.
- **attendance**: attendance records, leave types, applications and balances, holidays and settings. It feeds payroll through `attendance_inputs`.
- **expense_management**
  - Company expenses: vendors, categories and subcategories, CompanyExpense and CompanyExpensePayment. A payment status is derived through `refresh_status`.
  - Employee claims: EmployeeExpenseClaim, ClaimReview and Reimbursement.
  - Also attachments in private storage and ExpenseSettings, which holds counters.
- **reporting**: an app with no models that holds cross-module reports.
- **config**
  - Landing page with a local-only check.
  - No authentication. Employee, attendance and expense URLs are wrapped in `local_only`. invoice_app views are not wrapped; they rely on runserver being bound to 127.0.0.1.

## 2. Missing functionality vs the brief
- **Accounting core is missing entirely:** no Chart of Accounts, journal, double entry, posting, reversal, account mappings, financial periods or period locks.
- **No bank_management:**
  - no company bank or cash accounts, bank transactions or reconciliation;
  - payment `bank_account` fields are free text.
- **No compliance app:**
  - Rates live in code and settings.
  - StatutoryRule exists but has 0 rows and no effective-dated engine.
  - There is no GST ITC register. ITC must never be claimed automatically.
- **No salary payment records:** only `PayrollPeriod.payment_date` exists. There are no advances or loans and no payroll liability tracking.
- **No shared document_management or audit app.** The audit app must not claim to identify the operator, because there is no auth.
- **Backup:** there is no restore at all, and `backup_data` makes a file copy instead of using the sqlite backup API.
- **Reports missing:** CoA, journal, GL, TB, P&L, BS, cash flow, AR/AP ageing from the ledger, bank and cash book, and party ledgers.

## 3. Duplicate models and concepts
- **Settings:** four singletons (BusinessProfile, PayrollSettings, ExpenseSettings, AttendanceSettings) each hold some company or financial config.
- **Bank details exist in three places:**
  - BusinessProfile, as the company bank shown on the invoice;
  - EmployeeBankAccount, as payee data;
  - Vendor bank fields.
  Free-text `bank_account` also appears on CompanyExpensePayment and Reimbursement.
- **Party records:** Client and Vendor are separate party models. That is acceptable, but both will need ledger accounts.
- **Numbering counters** are spread across BusinessProfile, ExpenseSettings and employee numbering.
- **Private document handling** is shared through `private_storage`, but each app has its own attachment model.

## 4. Inconsistent calculations
There are five separate rounding helpers:
| Where | Behaviour |
|---|---|
| invoice billing | rupee_down / paise |
| `employee_management/payroll.q2` | ROUND_HALF_UP, with net pay rounded to the nearest rupee by default (configurable to 2 decimals) |
| `expense_management/models.Q2` | quantize with the default context, i.e. **ROUND_HALF_EVEN** |
| `reporting.q2` | HALF_UP |
| payslip | its own quantize |

Expense totals at .xx5 can differ by 1 paisa from the report figures. The fix is to centralise rounding in a single `financial/money.py` service with explicit modes. Existing stored values must not be changed.

## 5. Missing relationships
- CompanyExpensePayment.bank_account and Reimbursement.bank_account are free text. Both should become a FK to bank_management.BankAccount.
- No FK links any document to a journal entry (there are no journal entries yet).
- InvoicePayment has no link to a bank transaction.
- EmployeePayroll has no link to salary payments or liabilities.
- Client and Vendor have no links to ledger accounts.
- Expense categories have no mapping to GL accounts.

## 6. Missing payment tracking
- Invoices: InvoicePayment exists (0 rows), with no bank linkage.
- Payroll: nothing beyond a single date per period. Net pay, statutory dues (PF, ESI, PT, TDS) and employer contributions are not tracked as payable or paid.
- Expenses: payments and reimbursements exist, but they point to no bank and record no TDS deducted at source on vendor bills.

## 7. Double-counting risks
- A claim reimbursed to an employee could also be entered as a CompanyExpense with an employee link. Postings must come from one source only.
- Reporting mixes bases (accrual invoice dates against cash payment dates). Once the GL exists, reports must come from the GL alone.
- GST on expenses must not be counted both as cost and as input credit.
- An invoice receipt and a later imported bank transaction for the same money must be matched, not posted twice.
- Payroll employer contributions must not be added both to gross and as a separate expense.
- Posting must be idempotent, with a unique key of (source_type, source_id, event) and reversal rather than edit.

## 8. Data migration risks
- Converting the free-text bank_account fields to FKs needs a mapping pass. There are 4 expense payments and 2 reimbursements, so it is small but needs manual review.
- Backfilling journal entries from historical expenses (5) and claims (6) requires choosing an opening-balance date.
- Payroll and invoices have 0 rows, so their risk is low.
- The live DB is the only copy of real data. Every migration stage must take a sqlite-API backup first and run a dry run on a copy.
- Payroll snapshots must stay frozen. No migration may rewrite finalized rows.

## 9. Performance
- SQLite on a single local user is adequate at current volumes.
- The GL and TB need indexes on (account, date) and on (source_type, source_id).
- Use aggregate queries, not Python loops, in reports.
- Snapshot balances per period lock if the volume grows.
- There are 101 indexes today, which is fine.

## 10. Security
- `SECRET_KEY` is environment/private-file based; `DEBUG` is opt-in. WhiteNoise serves static assets, while MEDIA is served only during debug.
- There is no authentication.
  - `local_only` is a guard based on REMOTE_ADDR, applied to the employee, attendance and expense URLs (expense views use the decorator).
  - invoice_app views have no guard.
  - The app must never be bound beyond 127.0.0.1.
- Aadhaar and PAN are stored in plain text, though masked in the UI and left out of reports.
- The bank export (`exports.py:88`) contains full account numbers. Bank uploads require this, but the file must be treated as sensitive.
- The company account number on the invoice PDF is intended.
- Private files are served with no-store, nosniff and a sandbox CSP. This is good.
- The audit trail cannot attribute actions to a person. Record only "local session" or machine context and never claim to identify the operator.

## Decisions
- **Payroll app:** **no separate app.**
  - employee_management already owns periods, payslips, revisions and snapshots.
  - Add SalaryPayment, PayrollLiability and Advance/Loan models there.
  - Posting to the GL goes through accounting's posting service using the account mappings.
  - Splitting the app would duplicate the employee FKs and move tables for no gain.
- **Documents:** **wrap, do not move.**
  - `document_management` will be a registry (a Document index using a GenericFK, with sha256, type and the source app) pointing at the existing `private_storage` paths.
  - Existing attachment models remain.
  - Files stay where they are, so there is no file migration.

## Phased implementation plan (stages 2-9)
Each stage has the same entry and exit gates:
- **Before:** take a timestamped sqlite-API backup.
- **After:**
  - the full test suite must be green;
  - the invoice PDF must be byte-compared against a baseline render;
  - no edits to finalized payroll rows.

### Stage 2 - Foundations: money and periods
- **Apps:** new `accounting` app, with a `money` service, FinancialYear, FinancialPeriod and a period lock.
- **Files:** accounting/*, config/settings.py (INSTALLED_APPS).
- **Migrations:** accounting 0001.
- **Risks:** rounding parity with existing code. Do not refactor the other apps' q2 helpers yet; wrap them.
- **Tests:**
  - a rounding-mode matrix;
  - period overlap and lock tests.
- **Exit:** the services are usable; nothing else changed.

### Stage 3 - CoA and double-entry engine
- **Models:** Account (a tree with type and normal balance), JournalEntry, JournalLine, PostingKey (unique), AccountMapping.
- **Rules:**
  - an entry is immutable after posting;
  - corrections are made by reversal;
  - debits must equal credits;
  - posting into a locked period is rejected.
- **Data:** seed an Indian SME CoA through a data migration that can be re-run.
- **Risks:** it must not be possible to edit a posted entry, including through the admin.
- **Tests:**
  - balance;
  - idempotent repost;
  - reversal;
  - lock;
  - attempts to edit a posted entry.
- **Exit:** a TB of a synthetic book balances.

### Stage 4 - bank_management
- **Models:** BankAccount (company, cash and bank, linked to a GL account), BankTransaction, Reconciliation.
- **Optional:** link BusinessProfile to a BankAccount through a nullable FK, without changing the PDF.
- **Migrations:** convert the free-text bank_account fields on CompanyExpensePayment and Reimbursement to FKs.
  - Add a nullable FK.
  - Run a mapping script with a dry-run report.
  - Keep the old text column until verified.
- **Tests:** mapping and reconciliation matching.
- **Exit:** every existing payment is mapped or flagged.

### Stage 5 - compliance
- **Models:**
  - Effective-dated rules for GST rates, TDS sections, PF, ESI and PT, reusing or seeding StatutoryRule.
  - GSTITCRegister, where an ITC claim requires a manual "claim" action. ITC is never claimed automatically.
- **Approach:** remove hardcoded rates behind a lookup service, with the current values as seed data.
- **Risks:** a payroll calculation drifting. Golden-test the current payslip outputs before any change.
- **Exit:** identical payroll results under the seeded rules.

### Stage 6 - Integration: posting from invoices, payroll and expenses
- **Posting events:**
  - invoice issue and receipt;
  - payroll finalize and salary payment;
  - expense bill and payment;
  - claim approval and reimbursement.
- **Hooks:** explicit service calls at the existing transition points (not signals), with idempotent PostingKey values.
- **New payroll models:** SalaryPayment, PayrollLiability and Advance/Loan, in employee_management.
- **Data:** backfill postings for the existing expenses and claims from an opening date, with a dry run.
- **Risks:** double counting (see section 7).
- **Tests:** the event-to-journal matrix, plus repost being a no-op.
- **Exit:** the GL agrees with the module subledgers.

### Stage 7 - document_management and audit
- **document_management:** the Document registry over existing files, plus a backfill of the index.
- **audit:** an append-only AuditEvent recording what changed and when, with context "local". No user identity is claimed.
- **Tests:** registry integrity and sha256 checks; the audit log is append-only.
- **Exit:** every attachment is indexed and no files have moved.

### Stage 8 - Reports
- **Reports:** CoA, journal, GL, TB, P&L, BS, cash flow (indirect), AR/AP ageing, bank and cash book, and party ledgers.
- **Basis:** all built from the GL, with PDF and Excel export.
- **Changes to reporting:** existing reports in `reporting` are kept; the GL reports are added alongside them.
- **Tests:**
  - TB is balanced;
  - BS satisfies A = L + E;
  - P&L ties to retained earnings;
  - cash flow ties to the bank balance.
- **Exit:** the reports reconcile on fixtures.

### Stage 9 - Backup and restore, hardening, end-to-end
- **Backup and restore:**
  - the sqlite backup API, media and private-media ZIP, and manifest;
  - verified restore into a new path, with no overwrite unless confirmed.
- **Hardening:**
  - SECRET_KEY and DEBUG read from the environment;
  - `local_only` applied to invoice_app;
  - fix the flaky document test.
- **End-to-end:** the 15-step scenario.
- **Exit:** the end-to-end test is green and a restore drill succeeds.

## Critical blockers
None. Notes:
- The live DB has 0 invoices and 0 payrolls, so integration can only be verified on fixtures.
- No media or private files exist yet.
- The bank export contains full account numbers.
- SECRET_KEY and DEBUG defaults must be fixed in Stage 9.
