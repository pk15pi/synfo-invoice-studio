# Application-Wide Page Help / User Manual

**Implementation date:** 7 November 2026  
**Scope:** User-facing Django application screens and Django Admin pages.  
**Initial help-system data changes:** None; the help component itself introduced no schema migrations or business-data edits.

**Follow-up update (7 November 2026):** Bank statement import guidance now covers the staged PDF/CSV/XLSX workflow, explicit draft cancellation and versioned regeneration. Migrations `0003`-`0009` add review rows, source lineage and cancellation audit fields; they are additive and do not edit existing bank transaction records. See [bank_statement_import_upgrade.md](bank_statement_import_upgrade.md) and [bank_draft_management_gap_analysis.md](bank_draft_management_gap_analysis.md).

## Overview

The shared page-help component adds a compact circular `?` button to the current page heading, at the far right of the page title/action row. On narrow screens it stays at the heading's upper-right corner. Screens without a standard heading get a right-aligned fallback at the start of the content area.

Clicking the button opens a native modal dialog without navigating away. It has a fixed header and close button; the dialog supports keyboard focus, closes with Escape, and closes when the backdrop is clicked. The page remains in place after closing.

### Date and financial period filters

The shared period controls now provide calendar year/month, April–March financial year with monthly/quarterly/half-yearly/full-year selection, and custom inclusive dates. Quarter and half-year boundaries follow the Indian financial year. Search and other non-date controls remain independent. See the [Date and Financial Period Filters guide](date_period_filter_guide.md) for detailed page coverage and examples. The page-help registry includes these instructions on each page using the shared selector.

## Architecture

- `invoice_app/templates/invoice_app/_page_help_dialog.html` is the shared dialog markup used by the main application shell and Django Admin.
- `invoice_app/templates/invoice_app/_page_help_script.html` places the shared help button, opens/closes the dialog, and reads current page controls.
- `invoice_app/templates/invoice_app/_page_help_styles.html` provides responsive button and dialog styling.
- `invoice_app/page_help.py` contains route-specific user guidance, separate from view and business logic.
- `invoice_app/context_processors.py` resolves the current route guide. Unknown routes show an explicit "manual not added" fallback and a development console warning; they never display a different page's guide. Django Admin uses model/action-specific guidance.

The page title and written guidance come from the route guide. The field list is read from the visible form: label, required/optional state, input type, short select choices, and inline help text where present. The action list is read from visible page buttons and action links, including their existing tooltip descriptions where available. This keeps labels, required status and available actions aligned with the current template.

### Executive dashboard guide

The Reports & Analytics dashboard uses the shared period selector for month, quarter, half-year, financial-year, calendar-year and custom views. Cards compare the selected range with the previous equivalent range and show the absolute difference and relative change. If the comparison period has no value, the indicator is `New` or `n/a` rather than a misleading percentage.

Revenue is taxable invoice value excluding GST on invoice date; receipts and recorded payments follow their actual cash dates. The dashboard P&L is a mixed accrual management view. Payroll trends include finalized current payroll only: deductions are part of gross salary, employer contributions are additional employee cost, net salary is not another expense, and salary payments are cash movement. Vendor, customer, employee, department, expense, payroll, cash-flow and profitability details remain available from chart/card drill-downs and report navigation.

The selected-period workbook includes 11 sheets sourced from existing reports. The dashboard PDF button exports the selected-period management P&L. Use the workbook and linked reports for detailed salary, expense, revenue, cash-flow and balance analysis.

## Coverage report

The table lists each user-facing page route pattern with a contextual guide. Dynamic detail/edit routes require an existing record; report and placeholder routes use their route parameters. Create/edit screens using the same Django form intentionally share instructions while the dialog still reflects the current page title, fields and actions.

| Module | Page / route pattern | Help Available | Content Verified |
|---|---|:---:|:---:|
| Invoice / Shared | Application home (`/`) | Yes | Yes |
| Invoice / Shared | Business profile (`/business/`) | Yes | Yes |
| Invoice / Shared | Customer list (`/customers/`) | Yes | Yes |
| Invoice / Shared | Project list (`/customers/<int:client_id>/projects/`) | Yes | Yes |
| Invoice / Shared | Add or edit project (`/customers/<int:client_id>/projects/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Add or edit project (`/customers/<int:client_id>/projects/add/`) | Yes | Yes |
| Invoice / Shared | Customer details (`/customers/<int:pk>/`) | Yes | Yes |
| Invoice / Shared | Add or edit customer (`/customers/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Add or edit customer (`/customers/add/`) | Yes | Yes |
| Invoice / Shared | Invoice description templates (`/description-templates/`) | Yes | Yes |
| Invoice / Shared | Invoice description templates (`/description-templates/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Invoice description templates (`/description-templates/add/`) | Yes | Yes |
| Invoice / Shared | Invoice format templates (`/format-templates/`) | Yes | Yes |
| Invoice / Shared | Invoice format templates (`/format-templates/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Invoice format templates (`/format-templates/add/`) | Yes | Yes |
| Invoice / Shared | Invoice details (`/invoice/<int:pk>/`) | Yes | Yes |
| Invoice / Shared | Add invoice credit note (`/invoice/<int:pk>/credit-notes/add/`) | Yes | Yes |
| Invoice / Shared | Create or edit invoice (`/invoice/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Delete invoice payment (`/invoice/<int:pk>/payments/<int:payment_pk>/delete/`) | Yes | Yes |
| Invoice / Shared | Record or edit invoice payment (`/invoice/<int:pk>/payments/<int:payment_pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Record or edit invoice payment (`/invoice/<int:pk>/payments/add/`) | Yes | Yes |
| Invoice / Shared | Create or edit invoice (`/invoice/new/`) | Yes | Yes |
| Invoice / Shared | Invoice list (`/invoices/`) | Yes | Yes |
| Invoice / Shared | Invoice management dashboard (`/invoicing/`) | Yes | Yes |
| Invoice / Shared | Manpower deployments (`/manpower/`) | Yes | Yes |
| Invoice / Shared | Add or edit manpower deployment (`/manpower/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Add or edit manpower deployment (`/manpower/add/`) | Yes | Yes |
| Invoice / Shared | Project list (`/projects/`) | Yes | Yes |
| Invoice / Shared | Receipts (`/receipts/`) | Yes | Yes |
| Invoice / Shared | Recruitment placements (`/recruitment/`) | Yes | Yes |
| Invoice / Shared | Add or edit recruitment agreement (`/recruitment/agreements/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Add or edit recruitment agreement (`/recruitment/agreements/add/`) | Yes | Yes |
| Invoice / Shared | Add or edit recruitment candidate (`/recruitment/candidates/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Add or edit recruitment candidate (`/recruitment/candidates/add/`) | Yes | Yes |
| Invoice / Shared | Add or review candidate placement (`/recruitment/placements/<int:pk>/edit/`) | Yes | Yes |
| Performance Review | Dashboard (`/performance/`) | Yes | Yes |
| Performance Review | Record recruiter activity (`/performance/activity/`) | Yes | Yes |
| Performance Review | Edit candidate outcome (`/performance/applications/<int:pk>/edit/`) | Yes | Yes |
| Performance Review | Edit summary-only activity (`/performance/activities/<int:pk>/edit/`) | Yes | Yes |
| Performance Review | Edit job description (`/performance/jds/<int:pk>/edit/`) | Yes | Yes |
| Performance Review | Recruiter-wise records (`/performance/records/`) | Yes | Yes |
| Performance Review | Performance reports (`/performance/reports/`) | Yes | Yes |
| Performance Review | Performance settings (`/performance/settings/`) | Yes | Yes |
| Performance Review | Edit configured stage or reason (`/performance/settings/stages/<int:pk>/edit/`) | Yes | Yes |
| Performance Review | Daily Brief entry and records (`/performance/daily-brief/`) | Yes | Yes |
| Performance Review | Daily Brief details (`/performance/daily-brief/<int:pk>/`) | Yes | Yes |
| Performance Review | Edit Daily Brief (`/performance/daily-brief/<int:pk>/edit/`) | Yes | Yes |
| Performance Review | Daily Brief Analytics (`/performance/daily-brief/analytics/`) | Yes | Yes |
| Invoice / Shared | Add or review candidate placement (`/recruitment/placements/add/`) | Yes | Yes |
| Invoice / Shared | Add or edit client requisition (`/recruitment/requisitions/<int:pk>/edit/`) | Yes | Yes |
| Invoice / Shared | Add or edit client requisition (`/recruitment/requisitions/add/`) | Yes | Yes |
| Reports & Analytics | Executive dashboard (`/reports/`) | Yes | Yes |
| Reports & Analytics | Custom report builder (`/reports/custom/`) | Yes | Yes |
| Reports & Analytics | Business report (`/reports/r/<slug:key>/`) | Yes | Yes |
| Reports & Analytics | Report section (`/reports/section/<slug:slug>/`) | Yes | Yes |
| Reports & Analytics | Report data sources (`/reports/sources/`) | Yes | Yes |
| Accounting | Accounting dashboard (`/accounting/`) | Yes | Yes |
| Accounting | Chart of accounts (`/accounting/accounts/`) | Yes | Yes |
| Accounting | Add or edit ledger account (`/accounting/accounts/<int:pk>/edit/`) | Yes | Yes |
| Accounting | Account ledger (`/accounting/accounts/<int:pk>/ledger/`) | Yes | Yes |
| Accounting | Add or edit ledger account (`/accounting/accounts/new/`) | Yes | Yes |
| Accounting | Journal register (`/accounting/journal/`) | Yes | Yes |
| Accounting | Journal entry details (`/accounting/journal/<int:pk>/`) | Yes | Yes |
| Accounting | Create journal entry (`/accounting/journal/new/`) | Yes | Yes |
| Accounting | Account ledger (`/accounting/ledger/`) | Yes | Yes |
| Accounting | Accounting mappings (`/accounting/mappings/`) | Yes | Yes |
| Accounting | Financial periods (`/accounting/periods/`) | Yes | Yes |
| Bank Management | Bank and cash accounts (`/bank/`) | Yes | Yes |
| Bank Management | Bank account details (`/bank/accounts/<int:pk>/`) | Yes | Yes |
| Bank Management | Add or edit bank account (`/bank/accounts/<int:pk>/edit/`) | Yes | Yes |
| Bank Management | Manual bank entry (`/bank/accounts/<int:pk>/entry/`) | Yes | Yes |
| Bank Management | Bank statement import (`/bank/accounts/<int:pk>/import/`) | Yes | Yes |
| Bank Management | Bank reconciliation (`/bank/accounts/<int:pk>/reconciliation/`) | Yes | Yes |
| Bank Management | Bank statement lines (`/bank/accounts/<int:pk>/transactions/`) | Yes | Yes |
| Bank Management | Add or edit bank account (`/bank/accounts/new/`) | Yes | Yes |
| Bank Management | Bank or cash book (`/bank/book/<str:kind>/`) | Yes | Yes |
| Bank Management | Bank statement import (`/bank/imports/`) | Yes | Yes |
| Bank Management | Bank statement import (`/bank/imports/<int:pk>/`) | Yes | Yes |
| Bank Management | Statement draft review (`/bank/imports/<int:pk>/review/`) | Yes | Yes |
| Bank Management | Accounting classification review (`/bank/imports/<int:pk>/accounting-review/`) | Yes | Yes |
| Bank Management | Legacy bank text preview (`/bank/legacy-preview/`) | Yes | Yes |
| Bank Management | Bank transaction details (`/bank/transactions/<int:pk>/`) | Yes | Yes |
| Bank Management | Bank transfers (`/bank/transfers/`) | Yes | Yes |
| Compliance | Compliance dashboard (`/compliance/`) | Yes | Yes |
| Compliance | Compliance check (`/compliance/check/`) | Yes | Yes |
| Compliance | Compliance due dates (`/compliance/due-dates/`) | Yes | Yes |
| Compliance | Compliance due dates (`/compliance/due-dates/<int:pk>/`) | Yes | Yes |
| Compliance | GST register (`/compliance/gst/input/`) | Yes | Yes |
| Compliance | GST register (`/compliance/gst/output/`) | Yes | Yes |
| Compliance | Statutory payments (`/compliance/payments/`) | Yes | Yes |
| Compliance | Payroll statutory liabilities (`/compliance/payroll/`) | Yes | Yes |
| Compliance | Statutory rules (`/compliance/rules/`) | Yes | Yes |
| Compliance | Add or edit statutory rule (`/compliance/rules/<int:pk>/`) | Yes | Yes |
| Compliance | Add or edit statutory rule (`/compliance/rules/new/`) | Yes | Yes |
| Compliance | TDS register (`/compliance/tds/`) | Yes | Yes |
| Document Management | Document index (`/doc-index/`) | Yes | Yes |
| Document Management | Edit document metadata (`/doc-index/<int:pk>/edit/`) | Yes | Yes |
| Document Management | Document integrity check (`/doc-index/check/`) | Yes | Yes |
| Document Management | Upload document (`/doc-index/upload/`) | Yes | Yes |
| Audit | Audit log (`/audit/`) | Yes | Yes |
| Audit | Record history (`/audit/object/<str:app_label>/<str:model>/<str:object_id>/`) | Yes | Yes |
| Backup | Backup management (`/backups/`) | Yes | Yes |
| Backup | Backup details (`/backups/<int:pk>/`) | Yes | Yes |
| Backup | Import merge preview (`/backups/import/<uuid:package_id>/`) | Yes | Yes |
| Asset Management | Asset dashboard and register (`/assets/`) | Yes | Yes |
| Asset Management | Asset management section (`/assets/assignments/`) | Yes | Yes |
| Asset Management | Asset management section (`/assets/leases/`) | Yes | Yes |
| Asset Management | Asset management section (`/assets/maintenance/`) | Yes | Yes |
| Asset Management | Asset dashboard and register (`/assets/register/`) | Yes | Yes |
| Asset Management | Asset management section (`/assets/register/new/`) | Yes | Yes |
| Financial Reports | Financial reports and audit centre (`/financial-reports/`) | Yes | Yes |
| Financial Reports | Financial report catalogue (`/financial-reports/catalogue/`) | Yes | Yes |
| Document Generation | Document generation dashboard (`/documents/`) | Yes | Yes |
| Document Generation | Document module page (`/documents/<slug:section>/`) | Yes | Yes |
| Document Generation | Document catalogue (`/documents/catalogue/`) | Yes | Yes |
| Document Generation | Generate document (`/documents/generate/`) | Yes | Yes |
| Document Generation | Standard document library (`/documents/library/`) | Yes | Yes |
| Document Generation | Document category library (`/documents/library/categories/`) | Yes | Yes |
| Document Generation | Clause library (`/documents/library/clauses/`) | Yes | Yes |
| Document Generation | Clause library (`/documents/library/clauses/<int:clause_id>/edit/`) | Yes | Yes |
| Document Generation | Clause library (`/documents/library/clauses/new/`) | Yes | Yes |
| Document Generation | Document templates (`/documents/library/templates/`) | Yes | Yes |
| Document Generation | Document template details (`/documents/library/templates/<int:template_id>/`) | Yes | Yes |
| Document Generation | Create or edit document template (`/documents/library/templates/<int:template_id>/edit/`) | Yes | Yes |
| Document Generation | Create or edit document template (`/documents/library/templates/new/`) | Yes | Yes |
| Document Generation | Document type library (`/documents/library/types/`) | Yes | Yes |
| Document Generation | Template versions and review (`/documents/library/versions/`) | Yes | Yes |
| Document Generation | Template versions and review (`/documents/library/versions/<int:version_id>/edit/`) | Yes | Yes |
| Document Generation | Document settings (`/documents/settings/`) | Yes | Yes |
| Document Generation | Template preview (`/documents/template-preview/<int:version_id>/`) | Yes | Yes |
| Employee & Payroll | Employee records hub (`/bank-details/`) | Yes | Yes |
| Employee & Payroll | Departments (`/departments/`) | Yes | Yes |
| Employee & Payroll | Add or edit department (`/departments/<int:pk>/edit/`) | Yes | Yes |
| Employee & Payroll | Add or edit department (`/departments/new/`) | Yes | Yes |
| Employee & Payroll | Employee list (`/employees/`) | Yes | Yes |
| Employee & Payroll | Employee details (`/employees/<int:pk>/`) | Yes | Yes |
| Employee & Payroll | Add employee bank details (`/employees/<int:pk>/bank/new/`) | Yes | Yes |
| Employee & Payroll | Add or edit employee contact (`/employees/<int:pk>/contacts/<int:cid>/edit/`) | Yes | Yes |
| Employee & Payroll | Add or edit employee contact (`/employees/<int:pk>/contacts/new/`) | Yes | Yes |
| Employee & Payroll | Employee document history (`/employees/<int:pk>/documents/<int:did>/history/`) | Yes | Yes |
| Employee & Payroll | Upload or replace employee document (`/employees/<int:pk>/documents/<int:replace_id>/replace/`) | Yes | Yes |
| Employee & Payroll | Upload or replace employee document (`/employees/<int:pk>/documents/new/`) | Yes | Yes |
| Employee & Payroll | Add or edit employee (`/employees/<int:pk>/edit/`) | Yes | Yes |
| Employee & Payroll | Add or revise employee salary structure (`/employees/<int:pk>/salary/<int:from_id>/revise/`) | Yes | Yes |
| Employee & Payroll | Add or revise employee salary structure (`/employees/<int:pk>/salary/new/`) | Yes | Yes |
| Employee & Payroll | Add or edit employee (`/employees/new/`) | Yes | Yes |
| Employee & Payroll | HR dashboard (`/hr/`) | Yes | Yes |
| Employee & Payroll | Payroll dashboard (`/payroll/`) | Yes | Yes |
| Employee & Payroll | Employee advances and loans (`/payroll/advances/`) | Yes | Yes |
| Employee & Payroll | Employee advance details (`/payroll/advances/<int:pk>/`) | Yes | Yes |
| Employee & Payroll | Employee advance ledger (`/payroll/advances/employee/<int:pk>/`) | Yes | Yes |
| Employee & Payroll | Record employee advance (`/payroll/advances/new/`) | Yes | Yes |
| Employee & Payroll | Payslips (`/payroll/payslips/`) | Yes | Yes |
| Employee & Payroll | Payroll record (`/payroll/record/<int:rid>/`) | Yes | Yes |
| Employee & Payroll | Payroll record (`/payroll/record/<int:rid>/edit/`) | Yes | Yes |
| Employee & Payroll | Payroll reporting (`/payroll/reports/`) | Yes | Yes |
| Employee & Payroll | Employee records hub (`/payroll/salary-structures/`) | Yes | Yes |
| Employee & Payroll | Payroll settings (`/payroll/settings/`) | Yes | Yes |
| Employee & Payroll | Add or edit payroll rule (`/payroll/settings/rules/<int:pk>/edit/`) | Yes | Yes |
| Employee & Payroll | Add or edit payroll rule (`/payroll/settings/rules/new/`) | Yes | Yes |
| Employee & Payroll | Employee records hub (`/personal-details/`) | Yes | Yes |
| Employee & Payroll | Employee document storage (`/storage/`) | Yes | Yes |
| Attendance & Leave | Daily attendance (`/attendance/`) | Yes | Yes |
| Attendance & Leave | Leave balances (`/attendance/balances/`) | Yes | Yes |
| Attendance & Leave | Edit or carry forward leave balance (`/attendance/balances/<int:pk>/edit/`) | Yes | Yes |
| Attendance & Leave | Attendance reports and corrections (`/attendance/corrections/`) | Yes | Yes |
| Attendance & Leave | Employee monthly attendance (`/attendance/employee/<int:pk>/`) | Yes | Yes |
| Attendance & Leave | Holiday calendar (`/attendance/holidays/`) | Yes | Yes |
| Attendance & Leave | Add or edit holiday (`/attendance/holidays/<int:pk>/edit/`) | Yes | Yes |
| Attendance & Leave | Add or edit holiday (`/attendance/holidays/new/`) | Yes | Yes |
| Attendance & Leave | Import attendance (`/attendance/import/`) | Yes | Yes |
| Attendance & Leave | Leave types (`/attendance/leave-types/`) | Yes | Yes |
| Attendance & Leave | Add or edit leave type (`/attendance/leave-types/<int:pk>/edit/`) | Yes | Yes |
| Attendance & Leave | Add or edit leave type (`/attendance/leave-types/new/`) | Yes | Yes |
| Attendance & Leave | Leave applications (`/attendance/leave/`) | Yes | Yes |
| Attendance & Leave | Submit leave application (`/attendance/leave/new/`) | Yes | Yes |
| Attendance & Leave | Attendance reports and corrections (`/attendance/reports/`) | Yes | Yes |
| Attendance & Leave | Attendance summary (`/attendance/summary/`) | Yes | Yes |
| Attendance & Leave | Weekly-off settings (`/attendance/weekly-off/`) | Yes | Yes |
| Expense Management | Expense dashboard (`/expenses/`) | Yes | Yes |
| Expense Management | All expenses (`/expenses/all/`) | Yes | Yes |
| Expense Management | Expense categories (`/expenses/categories/`) | Yes | Yes |
| Expense Management | Add or edit expense category (`/expenses/categories/<int:cat_pk>/sub/<int:pk>/edit/`) | Yes | Yes |
| Expense Management | Add or edit expense category (`/expenses/categories/<int:cat_pk>/sub/new/`) | Yes | Yes |
| Expense Management | Add or edit expense category (`/expenses/categories/<int:pk>/edit/`) | Yes | Yes |
| Expense Management | Add or edit expense category (`/expenses/categories/new/`) | Yes | Yes |
| Expense Management | Employee expense claims (`/expenses/claims/`) | Yes | Yes |
| Expense Management | Employee claim details (`/expenses/claims/<int:pk>/`) | Yes | Yes |
| Expense Management | Submit or edit employee expense claim (`/expenses/claims/<int:pk>/edit/`) | Yes | Yes |
| Expense Management | Submit or edit employee expense claim (`/expenses/claims/new/`) | Yes | Yes |
| Expense Management | Review employee claims (`/expenses/claims/review/`) | Yes | Yes |
| Expense Management | Expense details (`/expenses/company/<int:pk>/`) | Yes | Yes |
| Expense Management | Add or edit company expense (`/expenses/company/<int:pk>/edit/`) | Yes | Yes |
| Expense Management | Add or edit company expense (`/expenses/company/new/`) | Yes | Yes |
| Expense Management | Expense report (`/expenses/reports/`) | Yes | Yes |
| Expense Management | Expense report (`/expenses/reports/<str:key>/`) | Yes | Yes |
| Expense Management | Expense vendors (`/expenses/vendors/`) | Yes | Yes |
| Expense Management | Vendor details (`/expenses/vendors/<int:pk>/`) | Yes | Yes |
| Expense Management | Add or edit expense vendor (`/expenses/vendors/<int:pk>/edit/`) | Yes | Yes |
| Expense Management | Add or edit expense vendor (`/expenses/vendors/new/`) | Yes | Yes |

**Custom route patterns covered:** 191. A pattern may serve several actual screens through path values (for example, the bank/cash book and report sections). File download, draft cancellation and draft regeneration POST endpoints are intentionally non-page routes.

### Django Admin coverage

Admin uses the same button, dialog and live field/action discovery. Guides are generated per model and action (list, add, edit, delete confirmation, history, sign-in and password pages).

| Module | Admin page family | Help Available | Content Verified |
|---|---|:---:|:---:|
| System administration | Admin home, sign-in/out and password pages | Yes | Yes |
| asset_management | Assets: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset acknowledgements: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset assignments: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset categories: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset events: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset leases: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset locations: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset maintenances: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| asset_management | Asset settings: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| auth | Groups: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| auth | Users: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Company document profiles: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Company document signatorys: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document acknowledgements: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document activity logs: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document approvals: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document categories: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document clauses: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document field definitions: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document number sequences: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document partys: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document signatures: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document templates: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document template clauses: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document template versions: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Document types: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| document_generation | Generated documents: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Audit checklists: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Audit checklist results: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Compliance submissions: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Compliance templates: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Export manifests: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Export packages: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Export package items: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Financial periods: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Reconciliation issues: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Reconciliation runs: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Report categories: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Report definitions: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |
| financial_reporting | Report runs: list, add, edit, delete confirmation and history (where enabled) | Yes | Yes |

**Registered Django Admin models covered:** 40. Admin actions remain subject to Django Admin permissions.

## Shared and intentionally limited help

- Pages using the same form/view share the appropriate workflow guide; the displayed heading, labels, required status, choices and actions still come from the current page.
- Asset assignment, lease and maintenance sections and some document-management sections currently render limited/placeholder screens. Their help text describes only visible functionality and does not present planned features as available.
- File download, PDF/CSV/XLSX, JSON and POST-only action endpoints do not render a page with a help dialog. The screen where an action is launched has help.
- The employee document hub URL `/documents/` is shadowed by the document-generation dashboard due to existing URL ordering; the help describes the page Django actually renders. Routing behavior was not changed.

## Testing and verification

- Pre-implementation database backup: `backups/application_help_preimplementation_20261107_111944/db.sqlite3` with `manifest.json`. SQLite integrity check: `ok`; 129 tables recorded; SHA-256: `d7596fb8b5ae76927f3cc09698551d47cc345f821f068792bb6d9b7798194f2f`.
- Django test-client smoke pass: 165 accessible HTML paths, including app/module screens, valid report sections, bank/cash books, document sections and Admin login. Each contained the shared modal and a non-null route guide.
- Bank import regression coverage: upload and review pages, separate draft and CSV export, safe import and classification-review paths now have bank-module tests. The two new page routes use the same shared help dialog and the updated bank-specific guide.
- Headless Chromium smoke pass: 15 representative routes returned HTTP 200 and each displayed exactly one help button with guide data. Samples covered home, invoice/customer forms, employee/payroll, attendance, expenses, bank imports, accounting, compliance, document library and Admin login.
- After moving the assets into shared template includes to avoid stale static-file caching, repeated the browser check on the customer form, bank imports and Admin login; the modal opened, Escape closed it, and no page-level horizontal overflow or JavaScript errors appeared.
- Modal interaction: opened on the customer form; page fields appeared; Escape and backdrop clicks closed it. At 390px viewport the modal fit without page-level horizontal overflow. Bank imports also had no page-level horizontal overflow at 390px.
- `manage.py check`: passed. The inline JavaScript was exercised in headless Chromium on representative routes.
- Focused page-help suite: 6 tests passed. The full project suite ran 625 tests: 624 passed; one pre-existing failure remains in `invoice_app.tests.IconActionTests.test_customer_row_actions_are_accessible_icons`, which expects a Delete icon although the current customer list provides Deactivate and Edit. This is unrelated to page help.

## Missing or incomplete content

- No accessible custom HTML route in the 165-path smoke run lacked a guide. All registered administrator models use model/action-specific administrator help.
- The field list explains the exact current control label, required state, type and select choices. Where a field has no inline help and its business meaning is not obvious from its label, additional field-specific prose could still improve the manual; no guessed field definitions were added.
- Placeholder screens are clearly identified in the guides and remain functionally limited as before.

## Data safety

Only help templates, styles, JavaScript, route help configuration, context wiring, tests and this report were added or changed. No financial records, documents, model relationships, permissions or business rules were modified. No migration was created. The pre-implementation database snapshot is retained at the backup path above.

### Recruiter Performance Review follow-up (7 November 2026)

The separate recruiter performance implementation adds page-help guides for dashboard, activity, records, reports, settings and their edit screens. All nine HTML routes are listed in the coverage table above and were checked with the Django test client. The central manual for this module is [recruiter_performance_user_manual.md](recruiter_performance_user_manual.md). Unlike the original help-system change summarized above, this feature adds an Employee recruiter flag and performance tracking models; it does not alter payroll, accounting or financial records. Its pre-implementation database and employee private-file backup is `backups/recruiter_performance_preimplementation_20261107_151328/`. See [recruiter_performance_implementation.md](recruiter_performance_implementation.md) for the migration and test result.

The home page module cards also include Performance Review, Asset Management, and Document & Agreement Management shortcuts. The document card summarizes generated documents, active templates and indexed private documents; the home-page guide includes the module links.

### Daily Brief module (7 November 2026)

Daily Brief is linked under the existing Performance Review menu. The Entry & Records page stores manually entered recruiter/date reports and plain-text JD Number/resume-count rows; Analytics aggregates only these two Daily Brief tables. The only external business foreign key is to the existing Employee recruiter record. JD details and correction history link internally to the Daily Brief parent. It does not connect to operational JD, candidate, client, placement, invoice, payroll, expense or accounting records. The entry, detail and edit screens retain before/after JSON snapshots when corrections are saved.

The user-facing pages are `/performance/daily-brief/` and `/performance/daily-brief/analytics/`; both use the shared page-help dialog and have page-specific instructions. Detail and Edit records also have contextual guides. The standalone [Daily Brief User Manual](daily_brief_user_manual.md) documents weekly, trailing-15-day, calendar month/year, configured financial quarter/half/year and inclusive custom-date semantics, the six KPIs and seven charts. The existing [Recruiter Performance Review manual](recruiter_performance_user_manual.md) links to it.

The additive migration `performance_review.0005_dailybrief_dailybriefhistory_dailybriefjddetail_and_more` was first applied to the isolated database snapshot and then to the active database. Pre-migration backup archive: `backups/backup-20261107-181335.zip`; verified working snapshot and manifest: `backups/daily_brief_preimplementation_20261107/`. The SQLite backup passed `integrity_check` with zero foreign-key violations and included 28 private-media and 16 media files. The isolated migration left existing business-table row counts unchanged; the expected additions were three empty Daily Brief tables plus Django content-type, permission and migration metadata.

Verification: focused Daily Brief tests **14 passed**; full Performance Review tests **26 passed**; `manage.py check` and `makemigrations --check --dry-run` passed. The repository-wide run executed 671 tests: 670 passed and the known unrelated `invoice_app.tests.IconActionTests.test_customer_row_actions_are_accessible_icons` failed because it expects a Delete icon instead of the existing Deactivate/Edit actions. No live QA records were created. Browser verification returned HTTP 200 with no JavaScript errors at 1440px for the records page and 390px for analytics; the entry form also fit a 390px viewport with no horizontal overflow. The mobile breakpoints stack form controls and preserve horizontal scrolling for wide data tables.

### Shared date and financial period filters (7 November 2026)

The help registry and coverage guidance now explain calendar-year/month selection, FY monthly/quarterly/half-yearly/full-year selection, custom inclusive ranges, and exact Indian FY boundaries on pages using the shared selector. Page-specific search and status controls remain independent. The detailed page list and exclusions are in [date_period_filter_guide.md](date_period_filter_guide.md), with the initial audit inventory in [date_period_filter_audit.md](date_period_filter_audit.md). Operational single-day/month workflows and pages with two independent business-date ranges remain separately documented because they have distinct data semantics.
