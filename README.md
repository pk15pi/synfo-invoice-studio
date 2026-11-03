# QuantBud Invoice Studio

A local-only Django monolith (no login, no admin, no payment gateway) for keeping business,
customer and project details and generating invoice PDFs that reproduce the QuantBud sample
invoice on US Letter.

## Run on Windows

Double-click `run_windows.bat`, or in PowerShell from this folder:

```powershell
py -m venv .venv                      # first time only
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000. `run_windows.bat` copies `db.sqlite3` to a timestamped
`db.sqlite3.bak-*` file before migrating. The database is never deleted.

Tests: `.venv\Scripts\python.exe manage.py test`

Sample PDF (QB020 reference data; does **not** consume an invoice number):
`.venv\Scripts\python.exe manage.py make_sample_invoice`, which writes `test_output\QB020_test.pdf`.
Add `--defaults` to ignore the saved Business profile.

## Screens

* **Business profile**: company, address, contact, GSTIN, bank details, signature image
  (stored under `media/signatures/`), name under the signature, footer, invoice prefix,
  next sequence number, padding, default terms, amount rounding, and the five PDF colours
  (primary navy, alternate row, white row, border, labels). Colours never change layout.
* **Customers**: list, detail, add and edit. Fields: name, address (one PDF line per line),
  country, contact, phone, email, tax info, default terms and notes.
* **Project templates** for each customer (see the billing rules below).
* **Invoices**: pick a customer. Its terms load and the due date is calculated (you can edit it).
  You can add or remove rows; the dates fill in the days and the description. You can
  override the unit price, amount or description. A live five-column preview is shown; the
  server recalculates everything. The buttons are **Preview PDF**, **Download PDF** (unsaved,
  no number used) and **Save**. There is also an invoice list, detail, edit/reopen, and a
  **PDF number override** that changes only the number printed on the PDF.

## Billing rules (Decimal throughout)

| Type | Unit price | Amount |
|---|---|---|
| A: Variable | daily rate | daily rate x days |
| B: Prorated | monthly / divisor | monthly x days / divisor |
| C: Full monthly | monthly / days | monthly |

The divisor is 30, or the actual number of days in the month of the period **end** date.
For example, 25 Sep to 31 Oct is 37 days, divided by 31.

Rounding policy: unit prices are rounded ROUND_HALF_UP to 2 dp. With the default
`rupee_down` setting, amounts are truncated to whole rupees, which reproduces the sample:
100000 x 23 / 31 = 74193.548 becomes 74,193.00. Switch to `paise` to round amounts HALF_UP
to 2 dp instead. The total is the sum of the line amounts. The PDF prints it with Indian
grouping (4,09,030.00); rows use Western grouping, as in the sample.

## Numbering

A number is taken only when a new invoice is saved, inside `transaction.atomic()` with
`select_for_update()` on the profile row. Previews, downloads, edits and the sample command
never consume a number.

## PDF

The PDF code is in `invoice_app/pdf/`. Every coordinate and font size is in `layout.py`, in
points measured from the top of the page against the reference PDF. `render.py` draws it with
ReportLab. Fonts are Trebuchet MS and Arial from `C:\Windows\Fonts`; set `QB_FONT_DIR` to
override them, otherwise Helvetica is used. Text is never shrunk. Over-long cell text is
shortened with an ellipsis, and the invoice save shows a warning. More than 9 rows continue
on extra pages; the total, payment details and signature go on the last page.

No business data is hard-coded in the PDF code; it all comes from the database.


## Invoice for (month)
The New/Edit invoice page has **Invoice for: Year** and **Month** dropdowns, saved together as
`Invoice.billing_month` (first day of the month). Years run from the current year -3 to +1, plus any older saved year.
If either one is blank, the month is not set.
* New invoices default to the **previous calendar month** (today in Asia/Kolkata): 2 Nov 2026 -> 2026 / October;
  10 Jan 2027 -> 2026 / December.
* Each row's Memo period from/to defaults to that month's first and last day. Qty defaults to the days
  in that month (it is calculated from the dates, as before, for every billing type) unless you typed a Qty.
* When you change the month, rows whose dates are empty or still equal the previous month's full range
  are moved to the new month. Rows with a custom range you typed are left unchanged. All fields stay editable.
* The month is shown on the invoice detail page and in the invoice lists. It is **not printed on the PDF**,
  so the measured PDF layout is unchanged.
* Invoices saved before this change show "month not set" until you pick a month and save. A POST without
  the field (scripts) uses the month before the invoice date.

## Project-wise Memo/Description

* **Description templates** (`/description-templates/`, linked from the Projects and customer pages):
  a master list of reusable texts (add / edit / activate / deactivate). Seeded: Monthly Support,
  Consulting, Development, General Services. A template assigned to a project or recorded on a saved
  invoice line cannot be deleted; deactivate it instead (inactive templates cannot be picked for new
  selections, existing assignments keep working).
* **Project edit page**: *Invoice Memo/Description* = Fixed Template (pick a template), Custom Text
  (the project's custom description) or Blank. Existing projects were migrated as Custom Text
  `'<old description> - {start_date} - {end_date}'` (identical output to before) or Blank if empty.
* Placeholders: `{start_date}` `{end_date}` (the row's Memo period, e.g. `October 01` / `October 31, 2026`),
  `{month}` `{year}` (the invoice's *Invoice for* month), `{project_name}` `{client_name}`. Use `{{ }}`
  for literal braces. Anything else (unknown names, `{x:fmt}`, `{x.attr}`, `{x[0]}`, unbalanced braces)
  is rejected on save. Missing values render empty and dangling ` - ` separators are removed.
  Rendering lives in `invoice_app/descriptions.py` only; the invoice page asks the server
  (`GET /invoice/line-description/`) for each row's default.
* **Invoice page**: picking a project fills its Memo/Description; it is re-rendered when the row's dates
  or the billing month change *until you type in it*. Qty / price changes never touch it. Re-picking a
  project from the list (or switching to another project) restores that project's default. A project row
  submitted with an empty Memo/Description gets the project's default on the server.
* The final text is saved on the invoice line; the PDF prints only that saved text, so later project
  or template edits never change existing invoices.

# Employee Management & Payroll (`employee_management` app)

A second Django app in the same project. It uses the same SQLite database, the same look and
the Business Profile (company name, address, contact, GSTIN, signature) for payslips.
`invoice_app`, its PDF and its migrations were not changed, apart from grouped navigation in `base.html`.

**Security: there is no login.** Every employee/payroll URL refuses requests that are not from
127.0.0.1 / ::1, and responses are `no-store`. That is a safety net, not access control. Keep
`runserver 127.0.0.1:8000` and do NOT expose this app to a network or the internet without first
adding authentication, HTTPS and permissions.

## Screens (top navigation)
* **Employees -> HR dashboard** `/hr/`: head-counts by status, document expiry alerts, missing bank or salary.
* **Employees** `/employees/`: search/filter; add/edit; a profile with tabs Overview, Emergency
  contacts, Documents, Bank, Salary, Payroll history, Change log. Employees are never deleted:
  set the status to Resigned/Terminated/Left and a date of leaving.
* **Departments** `/departments/`: CRUD and deactivation. Delete is blocked while employees are linked.
* **Payroll** `/payroll/`: choose the month/year, generate drafts, adjust each employee's inputs,
  finalize, revise, view payslips, ZIP all payslips (optional department filter), salary register (Excel),
  bank transfer CSV/Excel (finalized only, full account numbers: confidential).
* **Payroll -> Reports** `/payroll/reports/`: monthly summary, department-wise, employee history,
  joining/leaving. Each can be exported to Excel.
* **Business Settings -> Payroll settings** `/payroll/settings/`: ID prefix/sequence, divisor,
  rounding, payslip footer/signature, upload limit, expiry alert window, statutory rules.

## Employee IDs
Blank ID = next `<prefix><number>` (default EMP001, EMP002 ...), taken atomically. A manual ID is
allowed if it is unique; the generator skips taken IDs.

## Sensitive data
Aadhaar, PAN, bank account and document numbers are masked on screens (`XXXXXXXX1234`). Edit
forms never print the stored value; leave the field blank to keep it. They are not logged. Full bank
numbers appear only in the bank transfer export.

## Documents
PDF/JPG/JPEG/PNG only. Both the extension and the file's magic bytes are checked, and the size
limit is set in Payroll settings (default 5 MB). Files are stored as
`private_media/employees/<id>/documents/<random>.<ext>`. `QB_PRIVATE_ROOT` overrides the location.
They are never served from `/media/` or `/static/`; they open only through the View/Download links.
Replace keeps the old version in History. Delete asks for confirmation.

## Salary calculation
* Every amount is `Decimal`, rounded ROUND_HALF_UP to 2 dp per component. Optional: round net to
  the rupee, with the adjustment shown.
* **Monthly fixed**: `component x payable days / divisor`. The divisor is the actual calendar days
  (28-31) or a fixed 30, set per month (or per structure).
  Default payable days = days in service in the month - LWP - unpaid leave. A full month always
  pays the full component, even with a 30-day divisor in a 31-day month. "Pay full month" pays
  the full salary minus `monthly / divisor x (LWP + unpaid)`. Bonus/incentive/overtime components are not prorated.
* **Daily** = rate x payable days. **Hourly** = rate x approved hours. **Contract** = the fixed amount.
* Then add overtime, incentives and additional earnings, and subtract fixed deductions or statutory
  rules and additional deductions.
* **Statutory rules** (PF/ESI/PT/TDS/other) are not built in. Add them in Payroll settings: percent or
  fixed amount, base basic or gross, optional base cap, gross eligibility band, effective dates, and
  employee deduction vs employer contribution (shown separately; never reduces net). **Verify every
  rate with your accountant.** A structure uses the rules only if "Calculate from Statutory Rules" is ticked.
* A salary change creates a new structure from an effective date. The previous one is closed the day before.

## Finalize and revisions
Finalize is atomic. It freezes the inputs, line items and a snapshot of the employee, department,
designation, masked PAN and bank, so later edits never change a finalized payslip. A finalized
record cannot be edited or deleted (enforced in `save()`/`delete()`). "Create revision" makes a draft
revision 2, 3 ... The original stays current until the revision is finalized, and both are kept.
The database has one current payroll per employee per month.

## Commands
```
.venv\Scripts\python.exe manage.py make_sample_employee            # sample employee + test_output\EMP###_November_2026_Payslip.pdf
.venv\Scripts\python.exe manage.py make_sample_employee --save-payroll  # also saves a draft payroll
.venv\Scripts\python.exe manage.py backup_data                     # backups\backup-<time>.zip (DB + private_media + media)
.venv\Scripts\python.exe manage.py test
```
`run_windows.bat` installs requirements (now including openpyxl), runs `backup_data` and copies the
db before migrating.

## Backups
Back up `db.sqlite3` and the `private_media` folder together, because documents live in that folder,
not in the DB. `backup_data` does both. Keep backups off-machine and encrypted; they contain personal data.

# Attendance & Leave

Find it in the nav under **Attendance & Leave**, or on its home-page card. It is localhost-only like the rest of the app.

- **Attendance** (`/attendance/`): a daily screen listing every active employee. "Mark all unmarked as Present" fills the blank rows. Each employee also has a month grid (`/attendance/employee/<id>/`) showing that month's summary.
  - Statuses: P Present, A Absent, HD Half Day, L Leave (needs a leave type), H Holiday, WO Week Off. In and out times are optional.
  - An unmarked working day counts as Present in the summary. Unmarked holidays and weekly offs count as such.
- **Holidays**: dated holidays for each year. **Weekly off** is a list of weekdays (0=Mon … 6=Sun; the default is `6`, Sunday). You can also set numbered Saturdays, e.g. `2,4` for the 2nd and 4th Saturday.
- **Leave types**: CL, SL, EL (carry-forward, capped at 30), LWP (unpaid) and CO are seeded. You can edit each type's paid/unpaid flag, annual quota, carry-forward and cap, and active flag.
- **Leave applications**: you record them yourself, as Pending or approved immediately. Rejecting or cancelling is also yours to do. A half day is allowed for a single date.
  - Approving marks the attendance as L or HD and adds to the balance's "used".
  - Cancelling an approved leave reverses both.
  - Overlaps are blocked. Insufficient balance is also blocked, except for unpaid types such as LWP.
  - Holidays and weekly offs inside the leave range are not counted.
- **Leave balances**: one row per employee, leave type and year. Balance = opening + accrued (the quota) − used.
  - **Year-end carry forward** sets next year's opening to min(balance, cap). Types that don't carry forward get 0.
  - Re-running it recalculates.
- **Monthly summary**: working days, present, half days, paid leave, LWP, absent, holidays, week offs, and payable days.
  - Payable = days in service − absent − LWP.
  - A half day counts 0.5 present plus 0.5 absent, or 0.5 of the leave type if one is set.
- **CSV import**: download the month template, fill it and upload. Columns: employee_id, date, status, leave_type, in_time, out_time, note.
  - The import is all-or-nothing, and rows with a blank status are skipped.
  - Imported leave does not change balances.
- **Reports**: monthly attendance register (Excel and CSV), leave balance report (Excel), and leave history per employee.

## Payroll integration

When you **Generate** payroll for a month, every employee who has attendance records that month is prefilled through the existing input point:
- `lwp_days` = LWP
- `unpaid_leave_days` = absent
- `paid_leave_days` = paid leave

Working and payable days are left for the engine to compute, so the month's divisor (actual or fixed 30) still applies.

Each payroll row records its **input source** (Manual / From attendance / overridden) and keeps the attendance summary used at prefill. Both are shown on the payroll detail and adjust screens.
- Editing the inputs by hand marks the row as overridden. The original attendance figures stay visible.
- "Refresh from attendance" re-pulls the figures into a draft.
- **A month with no attendance records stays manual entry, exactly as before.**

Once an employee's payroll for a month is finalized, that month's attendance is **locked**: marks, imports and leave approvals touching it are refused. To change it, create a payroll revision. That unlocks attendance until the revision is finalized; then use "Refresh from attendance" on the revision draft.

## Commands

- `python manage.py seed_sample_attendance [--employee EMP001 --year 2026 --month 11]`: sample month with 1 CL, 1 LWP, 1 half-day CL and 1 absent day. Re-running it resets the sample.
- `python manage.py make_sample_employee` now seeds that attendance and builds the sample payslip from it: `test_output/EMP001_November_2026_Payslip.pdf` (payable 28 of 30, LWP 1, paid/unpaid leave 1.50 / 1).
## Expense Management

App `expense_management` (all pages under `/expenses/`, nav menu "Expense Management"; local-only, no login).

- Company expenses: vendor bills with GST/TDS, partial payments (atomic), status from payments, attachments (PDF/JPG/PNG) stored privately under `EMPLOYEE_PRIVATE_ROOT/expenses/` and included in `manage.py backup_data`.
- Employee claims: Draft -> Submitted -> Approved/Rejected/Returned -> Reimbursed (partial reimbursements supported), review queue at `/expenses/claims/review/`.
- Categories/subcategories (seeded by migration 0002), vendors, All Expenses list, reports with CSV/XLSX/PDF export; Indian financial-year (Apr-Mar) filters.
- Money is stored as Decimal in INR; foreign currency uses the stored exchange rate.

Setup: back up `db.sqlite3`, then `.venv\Scripts\python.exe manage.py migrate`.
Sample data: `manage.py make_sample_expenses` (tagged `[sample]`), remove with `manage.py make_sample_expenses --remove`.
Tests: `manage.py test expense_management`.

## Reports & Analytics

App: `reporting` (no models, no migrations; read-only over invoice_app, employee_management, attendance, expense_management). URL: `/reports/` (localhost only).

- Executive dashboard: KPI cards + inline SVG charts, filtered by Financial Year (Apr-Mar), FY part (H1/H2/Q1-Q4), month, or custom From/To dates, compared with the previous period.
- Sections: Revenue, Employees, Payroll, Expenses, Financial, Cash Flow, Outstanding, Profitability, Custom builder, Data sources.
- Every report page exports with `?format=xlsx` or `?format=pdf`. The FY workbook (7 sheets) is at "Download FY workbook".
- Data sources page: where each figure comes from, plus items the app does not track (e.g. customer receipts). Those show as "Not tracked", never as 0.
- Samples: `python manage.py reporting_samples --fy 2026 --report pnl --out test_output`
- Tests: `python manage.py test reporting`



## Receipts, GST and cancelled invoices

* **Receipts** (`InvoicePayment`): record payments on the invoice page (Record Payment) or see all of them under
  Invoice Management -> Receipts. Each receipt has a date, amount, mode, reference/UTR, TDS/charges written off and
  notes. If a receipt is dated before the invoice date, you must tick a confirmation box. If it overpays the
  invoice, you see a warning.
* **Payment status** is worked out from the receipts and never stored: Unpaid, Partially Paid, Paid, Overpaid or
  Cancelled. **Outstanding** = grand total - received - TDS/charges, counting receipts dated on or before the
  as-at date. Ageing runs from the due date (the invoice date when there is no due date). Overpayments show as
  customer credits and are not netted into receivables.
* **GST fields** (taxable value, None / CGST+SGST / IGST, rate) are shown and calculated live on the invoice form
  and detail page. The server recomputes them on save using the business rounding rule. The invoice PDF layout is
  unchanged and does not print the GST breakdown.
* **Cancelled** invoices are kept but left out of revenue, receivables and registers. They are listed separately
  in the Revenue Summary.
* **Reporting**: Revenue = taxable value excluding GST, by invoice date. Receipts are cash, by receipt date, and
  never add revenue. Period presets: Today, Yesterday, This/Last Week (Mon-Sun), This/Previous Month,
  Current/Previous Quarter, H1/H2, Current/Previous FY, Calendar/Previous Calendar Year, Last 3/6/12 Months
  (the first of the month N-1 months back through today), Financial Year (choose, with Q1-Q4 / H1 / H2),
  Month (choose), and Custom range. All of them resolve through `reporting.periods.resolve`.

## GST tax invoices (Indian clients)

* **Customer tax treatment** (Customers > edit): *Export / No tax* (default, unchanged USA flow), *Domestic GST*
  or *Export under LUT* (optional LUT number). GSTIN is optional, validated (format + checksum) and fills the
  state code automatically; Domestic GST needs a state.
* **Projects** carry a SAC code (default 998314) and GST rate (default 18%); every invoice line snapshots both.
* **Invoice form**: treatment and place of supply default from the customer and can be overridden; the tax
  section (with a live preview) appears only for Domestic GST.
* **Calculation** (`invoice_app/gst.py`): supplier state = first 2 digits of the Business GSTIN. Same state as
  place of supply -> CGST + SGST (half rate each), otherwise IGST. Each tax head is rounded half-up to paise;
  no round-off on the grand total. Results are written to the existing `taxable_value`, `cgst/sgst/igst_amount`,
  `grand_total` fields the ledger already posts. Drafts recompute on save; finalized invoices never do.
* **PDF**: Export is byte-identical to before; Export under LUT adds one LUT note line; Domestic GST prints a
  *TAX INVOICE* with customer GSTIN/state, place of supply, a SAC row per line, the tax summary and the amount
  in words (lakh/crore).
* Migration: `invoice_app/migrations/0011_gst_tax_invoice.py`. Tests: `python manage.py test invoice_app.test_gst`
  (PDF text tests need PyMuPDF and are skipped without it).

## Invoice PDF formats (invoice_app)

- Master page: `/format-templates/` (URL name `format_templates`). Create, edit, activate/deactivate, preview sample PDFs (Export / GST). Previews never consume an invoice number.
- Six built-ins are seeded by migration `invoice_app.0012_invoice_formats`: QuantBud Standard (default, original layout, US Letter), Modern Minimal, Corporate Classic, Compact One-Page, Bold Header, Timesheet / Service Detail (A4 by default, Letter and landscape are options).
- Customers can set a default format and a vendor code. Invoices can override the format and carry a PO number. The format settings are snapshotted on the invoice and locked once it is finalized.
- The invoice detail page has "Download in another format" (`?format=<pk>`). Amounts, numbering and the Standard / GST Tax Invoice bytes are unchanged.
- Formats in use are PROTECTed, so deactivate them instead of deleting.
