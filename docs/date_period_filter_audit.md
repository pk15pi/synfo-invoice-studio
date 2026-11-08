# Date and Financial Period Filter Audit

**Audit date:** 7 November 2026  
**Status:** Audit completed; shared period filters implemented on compatible report/list pages.

## Audit method

Searched first-party HTML templates and their included partials for GET forms and controls using calendar dates, months, years, financial years, quarters, halves, durations, and date ranges. Cross-checked template includes and route names against app URL configurations. Entry/edit fields, statement import mapping fields, and non-filter dates are separated below so they are not mistaken for report filters.

## Pages with date or period filters

| Page / route | Current filter controls | Current behavior / implementation |
|---|---|---|
| Expense dashboard `/expenses/` | Calendar year, calendar month, duration (yearly/quarterly/half-yearly), quarter, half | Custom dashboard parser. Quarters and halves currently use calendar-year boundaries; this is the primary implementation to correct to FY semantics. |
| Expense book `/expenses/all/` | Period preset, FY, month, custom from/to | Shared expense `_period_filter`; backend `reports.parse_filters`. |
| Expense reports `/expenses/reports/` and `/expenses/reports/<key>/` | Period preset, FY, month, custom from/to | Same expense partial/parser, combined with type/category/vendor/employee/department filters. |
| Expense claims `/expenses/claims/` | Claim date from/to and expense date from/to | Dedicated claim filter partial; separate claim and expense date ranges. |
| Accounting journal `/accounting/journal/` | Period, FY, month, from/to | Accounting period partial; journal status and search are additional filters. |
| Accounting ledger `/accounting/ledger/`, `/accounting/accounts/<pk>/ledger/` | Period, FY, month, from/to | Accounting period partial; account selection is additional. |
| Bank transactions `/bank/accounts/<pk>/transactions/` | Period, FY, month, from/to | Bank period partial; status is additional. |
| Bank reconciliation `/bank/accounts/<pk>/reconciliation/` | Period, FY, month, from/to | Bank period partial; balance is shown as of the selected period end. |
| Bank books `/bank/book/<kind>/` | Period, FY, month, from/to | Bank period partial; bank account is additional. |
| Reports dashboard `/reports/` | Period, FY, FY part, month, from/to, prior-period mode | Reporting partial and reporting period service; comparison mode is additional. |
| Reports sections `/reports/section/<slug>/` and individual reports `/reports/r/<slug>/` | Period, FY, FY part, month, from/to, when report enables period filters | Shared reporting partial unless `no_period` is set. Report-specific dimensions remain separate. |
| Custom report builder `/reports/custom/` | Period, FY, FY part, month, from/to, when period-enabled | Shared reporting period partial; dataset/grouping/columns/dimensions are additional. |
| Financial reports dashboard `/financial-reports/` | Named period, financial year, financial-year part, month, from/to | `ReportingPeriodForm`; financial reporting dashboard uses its period object. |
| Financial report catalogue `/financial-reports/catalogue/` | Named period, financial year, financial-year part, month, from/to | Same form/period context as dashboard; search/category are additional. |
| Attendance daily `/attendance/` | Specific date | Operational single-day attendance selector, not a period report. |
| Attendance monthly summary `/attendance/summary/` | Calendar month and year | Shared `_ym.html` dropdown pair. |
| Attendance employee month grid `/attendance/employee/<pk>/` | Calendar month and year | Shared `_ym.html` dropdown pair. |
| Attendance reports `/attendance/reports/` | Calendar month and year | Report/employee plus `_ym.html`; report is monthly. |
| Attendance leave balances `/attendance/balances/` | Calendar year | Year dropdown used to choose balance year. |
| Attendance holidays `/attendance/holidays/` | Calendar year | Year dropdown for holiday list. |
| Attendance correction history `/attendance/corrections/` | Calendar month and year | Month/year dropdown plus employee. |
| Compliance check `/compliance/check/` | Start/end date range | Shared compliance `_range.html`; additional compliance selection fields. |
| GST input `/compliance/gst/input/` | Start/end date range | Shared compliance `_range.html`. |
| GST output `/compliance/gst/output/` | Start/end date range | Shared compliance `_range.html`. |
| Payroll statutory summary `/compliance/payroll/` | Start/end date range | Shared compliance `_range.html`. |
| TDS register `/compliance/tds/` | Start/end date range | Shared compliance `_range.html`. |
| Audit log `/audit/` | Date from/to | Audit activity range with model/object/action/source/search filters. |
| Invoice list `/invoices/` | Quick calendar windows (last 3, previous, last 6), calendar year/month, FY | Two separate GET forms and quick period links; invoice status remains independent. |
| Receipts `/receipts/` | From/to dates | Receipt date range; method and search remain additional. |
| Manpower billing report `/manpower/` | Billing year/month | Month report with a year dropdown; client is an additional filter. |
| Recruitment dashboard `/recruitment/` | Joining from/to and recruitment from/to | Two distinct business-date ranges; client/status/search remain additional. |
| Employee report `/employees/reports/` | Calendar month/year | Payroll/employee report with report/department and other dimensions. |
| Employee hub table `/employees/` (and hub variants) | Financial year | FY selector alongside employee table filters. |
| Performance dashboard `/performance/` | Calendar year, month, quarter, half, FY starting year, period and from/to | Performance period partial; recruiter and search filters are additional. |
| Performance records `/performance/records/` | Calendar year, month, quarter, half, FY starting year, period and from/to | Same performance period partial. |
| Performance reports `/performance/reports/` | Calendar year, month, quarter, half, FY starting year, period and from/to | Same partial plus export controls. |
| Daily Brief records `/performance/daily-brief/` | Calendar year/month, FY starting year, quarter/half, period, reference date, from/to | Dedicated Daily Brief period partial; recruiter/job-description/search/page-size remain additional. |

## Date-like fields intentionally excluded from period-filter conversion

| Page / field | Reason |
|---|---|
| `/attendance/` POST attendance date | Entry date for attendance submission, not a GET filter. |
| `/bank/imports/<pk>/review/` confirmed from/to dates | Bank statement metadata confirmation and transaction-column mapping, not a report filter. |
| Invoice form billing-period start/end | Service period stored on an invoice, not a list/report filter. |
| Expense/claim form prior-period fields | Accounting metadata on a record, not a query filter. |
| Journal and payment form dates | Transaction entry values. |
| Recruitment candidate/placement dates in forms | Record fields; only dashboard date ranges are filters. |

## Standardization plan

1. Introduce a shared date-period parser and reusable filter component with mutually exclusive calendar-year/month and financial-year/period modes, plus custom date range support for pages that currently need arbitrary dates.
2. Keep operational single-day selectors (daily attendance), dual-domain audit/recruitment ranges, and date-entry forms semantically separate while aligning them to the shared date input/dropdown conventions where appropriate.
3. Preserve existing non-date query parameters, comparison options, page-size controls, and report dimensions.
4. Use April–March FY boundaries consistently, with Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec, Q4 Jan–Mar, H1 Apr–Sep, and H2 Oct–Mar.

No database or live financial records are part of this UI/filter audit.

## Implementation coverage and intentional exclusions

The shared component/parser is used by expense dashboard/book/reports; accounting journal/ledger; bank transactions/reconciliation/books; Reports & Analytics; Financial Reports; compliance registers/checks; audit log; invoice list/receipts/manpower billing; and Performance Review/Daily Brief. Compatible report pages accept the new `period_mode`, `calendar_year`, `calendar_month`, `fy`, `period_type`, `period_value`, and custom `date_from`/`date_to` parameters. Existing legacy parameters remain accepted by their original resolvers.

The following date controls remain separate because they describe independent data dimensions or record-entry periods rather than a single report range:

- Expense claims: claim-date and expense-date ranges are independently applied together.
- Recruitment dashboard: joining-date and recruitment/fee-approval date ranges are independent and may be combined.
- Attendance daily entry, monthly employee grids/reports, holiday year and leave-balance year: operational date/month/year controls determine one attendance or balance period; aggregate FY ranges would not match their data model.
- Payroll processing month and payslip month/year ZIP selectors: these select one payroll period or a downloadable month/year package rather than an arbitrary report window.
- Bank-import confirmed statement dates, invoice form service dates, and journal/payment/expense entry dates: these are record fields, not filters.

The date filter guide documents all included pages and these exclusions. UI-only filter changes create no database migration and do not write live business records.
