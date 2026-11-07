# Executive Dashboard Enhancement Analysis

**Review date:** 7 November 2026  
**Scope:** Existing `reporting` application and Executive Dashboard, with emphasis on useful analytics while preserving the current financial source-of-truth rules.

## Current implementation

- `reporting/periods.py` already resolves selected months, quarters, half-years, financial years, calendar years, rolling ranges and custom date ranges. Financial years run April to March, with Q1-Q4 and H1-H2 selections.
- `reporting/services.py` is the authoritative service layer for invoice-date taxable revenue, receipt-date collections, finalized/current payroll, approved reimbursements, company expenses, payment cash flows, receivables and payables.
- `reporting/reports.py` already registers detailed drill-downs for revenue, customers, invoices, payroll, employee and department cost, expenses, P&L, ratios, cash flow, balances and comparisons.
- The Executive Dashboard currently shows 11 KPI cards and 8 SVG charts. Existing cards compare the selected period with its previous equivalent period; charts use the monthly financial matrix and actual stored records.
- `reporting/exports.py` already provides XLSX and PDF output for detailed reports and a period-aware workbook builder. The dashboard route currently calls the full-FY workbook export even when a month, quarter or custom range is selected; this is a correctness gap.
- Shared report navigation already links into existing report sections. Route-specific help is registered in `invoice_app/page_help.py` and coverage is tracked in `docs/application_wide_help_manual.md`.

## Gaps against the requested enhancement

1. Salary is summarized as total cost; gross earnings, employee deductions, employer contributions, net salary and actual salary payments are not all visible together in dashboard trends.
2. The dashboard lacks clear monthly cash movement, employee cost versus revenue and expense composition trends, despite the underlying services and detailed reports existing.
3. Dashboard KPI cards do not show the absolute change alongside the percentage/new indicator.
4. The existing dashboard workbook export ignores the selected period and exports a financial year based on the period start date.
5. The dashboard needs a concise explanation of its mixed accrual basis and of the difference between accrual revenue/expense and cash movement.

## Data-source map and accounting safeguards

| Dashboard view | Authoritative source | Basis / safeguards |
|---|---|---|
| Revenue and invoice count | `services.invoice_revenue`, `services.monthly_matrix` | Invoice date, taxable value excluding GST; cancelled invoices follow service exclusions. |
| Customer receipts | `services.invoice_receipts`, `services.monthly_matrix` | Actual receipt date; never added to revenue. |
| Gross salary, deductions, employer cost, net | `services.payrolls`, `services.payroll_totals` | Finalized and current payroll only; deductions are included within gross, while employer contributions are additional cost. |
| Salary payment cash | `services.payroll_payments`, `services.monthly_matrix` | Actual payment date; cash movement only, not a second expense. |
| Company expenses and reimbursements | `services.pnl`, `services.monthly_matrix` | Existing cancellation, approval and accounting filters; do not re-sum ledger postings. |
| Cash movement | `services.cash_flow`, `services.monthly_matrix` | Actual recorded receipts and payment dates; untracked receipt sources remain explicitly unavailable. |
| Receivables / vendor and claim payables | `services.receivables_outstanding`, `services.vendor_payables`, `services.reimbursements_payable` | Point-in-time balance as of selected period end; existing source rules retained. |
| Salary payable | `services.payroll_payable` | Finalized/current net salary less recorded salary payments as of selected period end. |
| Employee / department analysis | Existing `emp_cost`, `payroll_department`, `payroll_summary` reports | Existing report registry remains the drill-down; no new parallel report engine. |

## Proposed implementation

- Keep the existing period resolver, dashboard route, detailed report registry and inline SVG chart library.
- Expand dashboard salary cards and monthly trends using finalized/current payroll data from the shared services.
- Add useful period-series charts for employee cost versus revenue, recorded cash inflow/outflow and company expense composition; link each chart to an existing detailed report.
- Show absolute as well as relative KPI comparisons and label selected-period accounting basis.
- Make the dashboard workbook honor the exact resolved period, and expand it with existing registered reports where those reports provide useful analysis.
- Update dashboard help/manual guidance and add focused tests for selected-period export, payroll inclusion rules, period ranges, comparison-zero behavior and dashboard-to-report reconciliation.

## Backup and safety

Before implementation, the active SQLite database was backed up with SQLite's online backup API to `backups/executive_dashboard_preimplementation_20261107_163053/db.sqlite3`. The backup returned `PRAGMA integrity_check = ok`, zero foreign-key violations, and 137 application tables. The configured `media` and `private_media` directories were copied into the same backup; all 44 copied files passed SHA-256 comparison against their sources. The manifest records file hashes and table row counts. No live financial record is to be modified by this dashboard-only work.

## Scope boundary

This enhancement is read-only and reuses current reporting calculations. Detailed vendor, customer, department, employee and invoice tables remain in their existing reports and are surfaced through dashboard drill-downs and workbook sheets; no duplicate report engine or financial data changes are planned.

## Implementation results

- **Completed:** Added gross salary, employee deductions, employer contributions, net salary payable, salary paid and outstanding salary payable cards. Payroll period amounts use finalized/current payroll; cash paid uses live salary payments dated in the selected period.
- **Completed:** Added payroll component, employee cost versus revenue, recorded cash inflow versus outflow, and expense composition charts. Existing revenue, P&L, category, customer, claim and department charts remain.
- **Completed:** KPI comparisons now show the previous equivalent period, absolute difference and relative change; zero prior values retain the existing `New`/`n/a` behavior.
- **Completed:** Dashboard Excel export now uses the exact selected range and contains 11 sheets built from the existing report registry. Dashboard PDF is the selected-period management P&L PDF.
- **Completed:** Updated route help, the application-wide manual and the dashboard user manual. No models or migrations changed.
- **Partially completed:** Existing reports provide employee-wise payroll, department cost, vendor-wise expense, customer concentration, invoice status, ageing and other drill-downs. This change links to them rather than adding duplicate detailed dashboard tables or new analytics pages.
- **Partially completed:** Dashboard PDF contains the P&L report only; it does not embed all dashboard charts or produce a full multi-section management pack. Cash data remains limited to payment/receipt sources tracked by existing services.
- **Verification:** `manage.py test reporting` passes 66 tests, including all supported period shapes, selected-month workbook scope and dashboard payroll reconciliation. `manage.py check` and `makemigrations --check --dry-run` pass. The live database still passes integrity and foreign-key checks, and all table row counts match the pre-change backup.
