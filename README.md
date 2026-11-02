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
