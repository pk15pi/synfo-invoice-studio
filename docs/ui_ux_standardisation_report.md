# UI/UX Standardisation Report

**Scope:** project-wide presentation standardisation based on the supplied task. No models, migrations, routes, permissions, business logic, database records, or user-facing workflow logic were changed.

## Status

- **Completed:** reviewed all 128 full-page HTML templates and standardized shared table, filter, form, and page-header rules in `invoice_app/static/invoice_app/style.css`.
- **Implemented:** shared table zebra/hover/divider treatment, compact wrapping filters, full-width form cards, responsive form grids, header action alignment, report catalogue row hierarchy, and mobile invoice-list filter wrapping.
- **Verification:** 98 navigation destinations returned HTTP 200 at desktop and mobile widths (196 route/viewport checks) with no page-level horizontal overflow. Nine representative pages were reviewed at 1366, 1024, and 390px widths. Django system check passed. The full suite ran 612 tests; 611 passed and one existing accessibility test failed (details below).
- **Scope:** no models, migrations, routes, permissions, business logic, database records, or workflow logic changed. CSS and presentational template markup changed where required.

## Shared styles introduced

- Design tokens for spacing, control height/radius, table header/zebra/hover colors, and dividers.
- A shared table system applied to non-PDF tables: consistent header type and background, cell padding/line height, alternating rows, hover feedback, dividers, numeric alignment, and compact action cells. `.table-wrap` remains the responsive horizontal-scroll container.
- Shared filter toolbar behavior for existing `.filter-toolbar`, `.compact-filters`, `.filters`, and GET filter forms with direct `.field` controls. Controls wrap cleanly and action buttons stay with filters.
- Form cards and compact entry forms now align to the full content/header width. Existing form grids remain multi-column where suitable and collapse at responsive breakpoints.
- Split page headers use a flexible title/action arrangement; the payroll toolbar retains its specialized distribution. Actions already in page headings remain grouped to the right; filter actions remain with filter forms.

## Template inventory

Inspected **128 full-page templates** (underscore partials and base templates excluded). Flags are static markup classification: `Table`, `Filter`, `Form`, `Header`.

| Module | Template | Table | Filter | Form | Header |
|---|---|:---:|:---:|:---:|:---:|
| accounting | `accounting/templates/accounting/account_form.html` | no | no | yes | yes |
| accounting | `accounting/templates/accounting/accounts.html` | yes | no | no | yes |
| accounting | `accounting/templates/accounting/home.html` | no | no | no | yes |
| accounting | `accounting/templates/accounting/journal_detail.html` | yes | no | yes | yes |
| accounting | `accounting/templates/accounting/journal_form.html` | yes | no | yes | yes |
| accounting | `accounting/templates/accounting/journal_list.html` | yes | yes | yes | yes |
| accounting | `accounting/templates/accounting/ledger.html` | yes | yes | yes | yes |
| accounting | `accounting/templates/accounting/mappings.html` | yes | no | yes | yes |
| accounting | `accounting/templates/accounting/periods.html` | yes | yes | yes | yes |
| asset_management | `asset_management/templates/asset_management/dashboard.html` | no | no | no | yes |
| asset_management | `asset_management/templates/asset_management/placeholder.html` | no | no | no | yes |
| asset_management | `asset_management/templates/asset_management/register.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/application_form.html` | no | no | yes | yes |
| attendance | `attendance/templates/attendance/applications.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/balances.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/corrections.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/daily.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/holidays.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/import.html` | no | no | yes | yes |
| attendance | `attendance/templates/attendance/leave_types.html` | yes | no | no | yes |
| attendance | `attendance/templates/attendance/month_grid.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/reports.html` | yes | yes | yes | yes |
| attendance | `attendance/templates/attendance/summary.html` | yes | no | no | yes |
| audit | `audit/templates/audit/log.html` | yes | yes | yes | yes |
| backup | `backup/templates/backup/detail.html` | yes | no | yes | yes |
| backup | `backup/templates/backup/home.html` | yes | no | yes | yes |
| backup | `backup/templates/backup/merge_preview.html` | yes | no | yes | yes |
| bank_management | `bank_management/templates/bank_management/account_detail.html` | no | no | no | yes |
| bank_management | `bank_management/templates/bank_management/account_form.html` | no | no | yes | yes |
| bank_management | `bank_management/templates/bank_management/accounts.html` | yes | no | no | yes |
| bank_management | `bank_management/templates/bank_management/book.html` | yes | yes | yes | yes |
| bank_management | `bank_management/templates/bank_management/import_batch.html` | yes | no | yes | yes |
| bank_management | `bank_management/templates/bank_management/import_done.html` | no | no | no | yes |
| bank_management | `bank_management/templates/bank_management/import_upload.html` | no | no | yes | yes |
| bank_management | `bank_management/templates/bank_management/imports.html` | yes | no | no | yes |
| bank_management | `bank_management/templates/bank_management/legacy_preview.html` | yes | no | no | yes |
| bank_management | `bank_management/templates/bank_management/manual_entry.html` | no | no | yes | yes |
| bank_management | `bank_management/templates/bank_management/reconciliation.html` | yes | yes | yes | yes |
| bank_management | `bank_management/templates/bank_management/transactions.html` | yes | yes | yes | yes |
| bank_management | `bank_management/templates/bank_management/transfers.html` | yes | no | yes | yes |
| bank_management | `bank_management/templates/bank_management/txn_detail.html` | yes | yes | yes | yes |
| compliance | `compliance/templates/compliance/check.html` | yes | yes | yes | yes |
| compliance | `compliance/templates/compliance/due_dates.html` | yes | no | yes | yes |
| compliance | `compliance/templates/compliance/gst_input.html` | yes | yes | yes | yes |
| compliance | `compliance/templates/compliance/gst_output.html` | yes | no | no | yes |
| compliance | `compliance/templates/compliance/home.html` | yes | no | no | yes |
| compliance | `compliance/templates/compliance/payments.html` | yes | no | yes | yes |
| compliance | `compliance/templates/compliance/payroll_stat.html` | yes | no | no | yes |
| compliance | `compliance/templates/compliance/rule_form.html` | yes | no | yes | yes |
| compliance | `compliance/templates/compliance/rules.html` | yes | yes | yes | yes |
| compliance | `compliance/templates/compliance/tds.html` | yes | no | no | yes |
| document_generation | `document_generation/templates/document_generation/catalogue.html` | yes | yes | yes | yes |
| document_generation | `document_generation/templates/document_generation/dashboard.html` | yes | no | no | yes |
| document_generation | `document_generation/templates/document_generation/document_preview.html` | no | no | yes | yes |
| document_generation | `document_generation/templates/document_generation/generate_document.html` | no | yes | yes | yes |
| document_generation | `document_generation/templates/document_generation/generated.html` | no | no | no | yes |
| document_generation | `document_generation/templates/document_generation/placeholder.html` | no | no | no | yes |
| document_generation | `document_generation/templates/document_generation/settings.html` | no | no | yes | yes |
| document_generation | `document_generation/templates/document_generation/template_preview.html` | no | no | no | yes |
| document_management | `document_management/templates/document_management/check.html` | yes | no | yes | yes |
| document_management | `document_management/templates/document_management/edit.html` | no | no | yes | yes |
| document_management | `document_management/templates/document_management/list.html` | yes | yes | yes | yes |
| document_management | `document_management/templates/document_management/upload.html` | no | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/advance_detail.html` | yes | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/advance_form.html` | no | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/advance_ledger.html` | yes | no | no | yes |
| employee_management | `employee_management/templates/employee_management/advances.html` | yes | yes | yes | yes |
| employee_management | `employee_management/templates/employee_management/confirm.html` | no | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/departments.html` | yes | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/document_history.html` | yes | no | no | yes |
| employee_management | `employee_management/templates/employee_management/employee_detail.html` | yes | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/employee_form.html` | no | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/employee_list.html` | yes | yes | yes | yes |
| employee_management | `employee_management/templates/employee_management/hr_dashboard.html` | yes | no | no | yes |
| employee_management | `employee_management/templates/employee_management/hub_table.html` | yes | yes | yes | yes |
| employee_management | `employee_management/templates/employee_management/payroll_dashboard.html` | yes | yes | yes | yes |
| employee_management | `employee_management/templates/employee_management/payroll_detail.html` | yes | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/payroll_edit.html` | no | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/reports.html` | yes | yes | yes | yes |
| employee_management | `employee_management/templates/employee_management/salary_form.html` | no | no | yes | yes |
| employee_management | `employee_management/templates/employee_management/settings.html` | yes | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/all_expenses.html` | yes | yes | yes | yes |
| expense_management | `expense_management/templates/expense_management/attachment_replace.html` | no | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/categories.html` | yes | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/claim_detail.html` | yes | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/claim_form.html` | no | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/claims.html` | yes | no | no | yes |
| expense_management | `expense_management/templates/expense_management/confirm.html` | no | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/dashboard.html` | yes | yes | yes | yes |
| expense_management | `expense_management/templates/expense_management/expense_detail.html` | yes | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/expense_form.html` | no | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/reports.html` | yes | yes | yes | yes |
| expense_management | `expense_management/templates/expense_management/review.html` | yes | no | no | yes |
| expense_management | `expense_management/templates/expense_management/simple_form.html` | no | no | yes | yes |
| expense_management | `expense_management/templates/expense_management/vendor_detail.html` | yes | no | no | yes |
| expense_management | `expense_management/templates/expense_management/vendors.html` | yes | yes | yes | yes |
| expense_management | `expense_management/templates/expense_management/xm_base.html` | no | no | no | no |
| financial_reporting | `financial_reporting/templates/financial_reporting/catalogue.html` | yes | yes | yes | yes |
| financial_reporting | `financial_reporting/templates/financial_reporting/dashboard.html` | no | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/client_detail.html` | yes | no | no | yes |
| invoice_app | `invoice_app/templates/invoice_app/clients.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/dashboard.html` | yes | no | no | yes |
| invoice_app | `invoice_app/templates/invoice_app/description_templates.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/form_page.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/format_template_form.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/format_templates.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/home.html` | no | no | no | yes |
| invoice_app | `invoice_app/templates/invoice_app/invoice_detail.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/invoice_form.html` | yes | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/invoice_list.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/manpower_deployment_form.html` | yes | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/manpower_deployments.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/payment_delete.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/payment_form.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/profile.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/project_form.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/projects.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/receipts.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/recruitment_agreement_form.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/recruitment_candidate_form.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/recruitment_dashboard.html` | yes | yes | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/recruitment_placement_form.html` | no | no | yes | yes |
| invoice_app | `invoice_app/templates/invoice_app/recruitment_requisition_form.html` | no | no | yes | yes |
| reporting | `reporting/templates/reporting/builder.html` | no | yes | yes | yes |
| reporting | `reporting/templates/reporting/dashboard.html` | no | yes | yes | yes |
| reporting | `reporting/templates/reporting/report.html` | no | yes | yes | yes |
| reporting | `reporting/templates/reporting/section.html` | no | no | no | yes |
| reporting | `reporting/templates/reporting/sources.html` | yes | no | no | yes |

## Special treatment and preservation

- PDF-format tables (`.pdfcols`) are excluded from the shared application table rules to preserve document preview formatting.
- Existing bespoke page layouts remain in place where they support a distinct workflow, including the payroll toolbar and reporting dashboard panels.
- Existing control names, IDs, URLs, form methods, sorting hooks, export links, and table actions were preserved.
- No header action was moved across a form boundary in this CSS-only pass; existing actions remain attached to their current pages and data context.

## Verification results

- **Source inventory:** scanned all 128 full-page templates for local tables, filters, forms, and page headings. The inventory flags below describe markup in each template; shared navigation and shell elements are inherited from the base template.
- **CSS application:** all application pages load the shared invoice stylesheet through `invoice_app/templates/invoice_app/base.html`; expense and financial reporting retain their additional stylesheets. Ran `manage.py collectstatic --noinput`; the current source CSS is collected into `staticfiles/`.
- **Django system check:** `manage.py check` passed with no issues.
- **Test suite:** 612 tests ran; 611 passed and one failed: `invoice_app.tests.IconActionTests.test_customer_row_actions_are_accessible_icons` expects a customer-row Delete action (`title="Delete" aria-label="Delete"`), but that row exposes deactivate/edit actions. This is an existing test/template expectation mismatch unrelated to the CSS and templates changed in this pass.
- **Route and responsive check:** crawled the 98 unique navigation destinations at 1366px and 390px; all returned HTTP 200 and no page-level horizontal overflow. This includes the invoice list after the mobile filter toolbar was changed to wrap. Nine representative routes were also inspected at 1366px, 1024px, and 390px. Tables with wide data retain horizontal scrolling inside their table wrapper.
- **Visual review:** inspected representative screens for customer forms/radios, attendance filters, financial report catalogue rows, employee list, journal list, expense list, compliance payment form, and bank import heading. This was not a pixel-by-pixel screenshot review of all 98 routes; the complete inventory and route-width sweep provide broader coverage, while bespoke layouts may still need targeted review.
- **Local server note:** `collectstatic` refreshed the served static files. If the existing development server still shows the prior CSS, restart it or hard-refresh the browser so its static-file cache reloads.

## Remaining review

No known page-level overflow remains in the 98-route desktop/mobile sweep. Pages with bespoke inline styles and specialized report/document layouts may still benefit from focused visual review when those workflows are next changed. No form submissions, exports, destructive actions, or database writes were used for this UI pass.
