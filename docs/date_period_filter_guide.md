# Date and Financial Period Filters

**Updated:** 7 November 2026

## Choosing a date range

The shared period selector has three modes:

- **Calendar year:** choose a year and optionally a month. Leaving Month on **All months** covers 1 January through 31 December.
- **Financial year:** choose the FY start year and a period. Financial years run from 1 April through 31 March. Monthly periods select a named calendar month inside that FY; January–March are in the following calendar year.
- **Custom dates:** choose inclusive From and To dates. If only one date is supplied to a compatible report, it is used as both boundaries.

Financial-year selections use these boundaries:

| Period | Date range |
|---|---|
| Q1 | 1 April–30 June |
| Q2 | 1 July–30 September |
| Q3 | 1 October–31 December |
| Q4 | 1 January–31 March |
| H1 | 1 April–30 September |
| H2 | 1 October–31 March |
| Full Financial Year | 1 April–31 March |

Changing the period type reveals only its relevant selector: month, quarter, half-year, or no additional selector for the full FY. Apply refreshes the page. Search, status, account, employee, comparison, and other non-date filters remain separate.

## Pages using the shared period selector

- Expense dashboard, expense book, and expense reports.
- Accounting journal and ledger.
- Bank transactions, reconciliation, and bank/cash books.
- Reports & Analytics dashboard, section reports, individual reports, and custom report builder.
- Financial Reports dashboard and catalogue.
- Compliance checks, GST input/output, payroll statutory summary, and TDS register.
- Audit log.
- Invoice list, receipts, and manpower billing report.
- Performance Review dashboard, records, reports, and Daily Brief records/analytics.

Pages with multiple independent business-date ranges retain separate ranges: expense claims distinguish claim date from expense date; recruitment distinguishes joining date from recruitment/approval date. These ranges may be applied together, so combining them into one period selector would change their meaning. Attendance daily entry is an operational single-day selector; attendance month grids/reports, holiday year, leave-balance year, payroll processing month, and payslip ZIP controls keep their calendar/operational semantics.

The period component changes query filtering only. It does not change financial calculations, record status, or stored data.
