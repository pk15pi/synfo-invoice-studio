# QuantBud Invoice Studio

A small local-only Django monolith for maintaining business/customer/project details and generating invoice PDFs styled after the supplied QuantBud invoice.

## Run on Windows

1. Install Python 3.10+.
2. Extract this folder.
3. Open Command Prompt/PowerShell in the folder.
4. Run:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py manage.py migrate
py manage.py runserver
```

5. Open http://127.0.0.1:8000

## Run on macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Included

- No login, users, admin panel, payment gateway or external service.
- SQLite database stored locally as `db.sqlite3`.
- Business details, bank details, invoice prefix/sequence, terms and colors are saved in one settings screen.
- Multiple customers, each with reusable project templates.
- Fixed monthly or variable daily project rates.
- Fixed-monthly or prorated monthly billing; proration can use actual calendar days or a 30-day month.
- Invoice date, due date and payment terms.
- PDF generation laid out against the supplied QB020 page: five exact columns (`Consultant -Project Name`, `Memo/Description`, `Qty`, `Unit Price`, `Amount`), blue header, alternating blue/white rows, vertical column dividers, date range inside Memo/Description, total, bank details, signature and footer.
- Optional one-time invoice number override. The database keeps the automatic invoice number and the sequence continues independently.

## Billing rules

- Variable/daily: daily rate × entered billable days.
- Fixed monthly, prorated: monthly amount ÷ selected divisor × entered days.
- Fixed monthly, full-month mode: the monthly amount is charged whenever billable days are greater than zero.
- Divisor: either 30 or the actual number of days in the invoice month.

## Notes

- The first automatic number is QB021 because the uploaded reference invoice is QB020. Change prefix and next sequence in Business details if needed.
- The PDF renderer uses fixed US Letter page coordinates to preserve the reference invoice positioning and proportions. The reference contains a handwritten signature image; this version retains editable signature text until a signature-image upload field is added.
- Back up `db.sqlite3` periodically. This app intentionally has no authentication and should be run only on a trusted local machine, not exposed to the public internet.
