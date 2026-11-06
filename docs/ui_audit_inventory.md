# UI/UX audit inventory

## Scope and review status

- Discovered 14 non-Django applications, 125 full-page templates (underscore-prefixed templates are partials), and 494 URL patterns including Django admin and action/download routes.
- Page templates inherit the shared invoice_app base directly or through a module base; module includes and inline CSS also occur.
- Opened 96 unique destinations exposed by shared navigation in headless Chromium at 1366 x 900; all returned HTTP 200. Full-page screenshots were reviewed as a contact sheet.
- Checked the home, expenses list, financial reports dashboard, and payslips pages at 1024 x 768 and 390 x 844. All eight renders returned HTTP 200 with no page-level horizontal overflow; the expense table scrolls inside its wrapper on mobile.
- Confirmed the first top navigation dropdown opens on hover. Forms were not submitted; destructive/action links and downloads were not invoked.

## Shared stylesheet and findings

| Area | Source review finding | CSS refinement applied | Status |
|---|---|---|---|
| Shared shell and navigation | Multi-module navigation with long dropdown menus. | Bounded dropdown height, enabled menu scrolling and text wrapping. | 96 destinations rendered; dropdown hover verified |
| Buttons and inputs | Shared controls were larger than compact sizing requested. | Reduced shared button height/padding; standardized control min-height to 38px while retaining taller hero CTA. | Desktop and responsive screenshots reviewed |
| Tables | Shared wrappers scroll horizontally; long expense table columns became too narrow on mobile. | Reduced shared cell padding; set readable widths for the expense table so it scrolls inside its wrapper. | Expense list re-rendered at mobile width |
| Responsive layout | Shared CSS already has tablet/mobile breakpoints. | Kept breakpoints and added safe min-width handling for main content. | Four routes at tablet and mobile; no page overflow |
| Forms, dashboards, modals, print layouts | Many module templates contain specialized or inline styling. | No structural or functional edits; detail forms and modals still need representative review. | Not all templates individually verified |

## Page inventory

All listed templates use the shared invoice_app/static/invoice_app/style.css. Expense pages also load expense_management/static/expense_management/expense.css; financial report dashboard pages load financial_reporting/static/financial_reporting/dashboard.css. Other module-specific inline styles remain local to templates.

| Module | Page template | Additional CSS | UI issue/change | Review status |
|---|---|---|---|---|
| accounting | accounting/templates/accounting/account_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/accounts.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/base_acc.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/home.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/journal_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/journal_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/journal_list.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/ledger.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/mappings.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| accounting | accounting/templates/accounting/periods.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| asset_management | asset_management/templates/asset_management/dashboard.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| asset_management | asset_management/templates/asset_management/placeholder.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| asset_management | asset_management/templates/asset_management/register.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/application_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/applications.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/balances.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/corrections.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/daily.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/holidays.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/import.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/leave_types.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/month_grid.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/reports.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| attendance | attendance/templates/attendance/summary.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| audit | audit/templates/audit/log.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| backup | backup/templates/backup/detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| backup | backup/templates/backup/home.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| backup | backup/templates/backup/merge_preview.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/account_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/account_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/accounts.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/base_bank.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/book.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/import_batch.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/import_done.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/import_upload.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/imports.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/legacy_preview.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/manual_entry.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/reconciliation.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/transactions.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/transfers.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| bank_management | bank_management/templates/bank_management/txn_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/base_c.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/check.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/due_dates.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/gst_input.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/gst_output.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/home.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/payments.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/payroll_stat.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/rule_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/rules.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| compliance | compliance/templates/compliance/tds.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/catalogue.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/dashboard.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/document_preview.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/generate_document.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/generated.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/placeholder.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/settings.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_generation | document_generation/templates/document_generation/template_preview.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_management | document_management/templates/document_management/check.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_management | document_management/templates/document_management/edit.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_management | document_management/templates/document_management/list.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| document_management | document_management/templates/document_management/upload.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/advance_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/advance_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/advance_ledger.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/advances.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/confirm.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/departments.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/document_history.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/employee_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/employee_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/employee_list.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/hr_dashboard.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/hub_table.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/payroll_dashboard.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/payroll_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/payroll_edit.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/reports.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/salary_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| employee_management | employee_management/templates/employee_management/settings.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/all_expenses.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/attachment_replace.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/categories.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/claim_detail.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/claim_form.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/claims.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/confirm.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/dashboard.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/expense_detail.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/expense_form.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/reports.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/review.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/simple_form.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/vendor_detail.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/vendors.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| expense_management | expense_management/templates/expense_management/xm_base.html | expense_management/expense.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| financial_reporting | financial_reporting/templates/financial_reporting/catalogue.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| financial_reporting | financial_reporting/templates/financial_reporting/dashboard.html | financial_reporting/dashboard.css | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/base.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/client_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/clients.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/dashboard.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/description_templates.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/form_page.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/format_template_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/format_templates.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/home.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/invoice_detail.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/invoice_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/invoice_list.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/payment_delete.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/payment_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/profile.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/project_form.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/projects.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| invoice_app | invoice_app/templates/invoice_app/receipts.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| reporting | reporting/templates/reporting/builder.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| reporting | reporting/templates/reporting/dashboard.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| reporting | reporting/templates/reporting/report.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| reporting | reporting/templates/reporting/section.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |
| reporting | reporting/templates/reporting/sources.html | ? | Shared compact controls, bounded menus, responsive content sizing | Inventory listed; route mapping incomplete |

## Safety and remaining review

Modified CSS files: invoice_app/static/invoice_app/style.css and expense_management/static/expense_management/expense.css. The audit inventory is documented in docs/ui_audit_inventory.md. No template markup, JavaScript, Python, models, migrations, routes, or database records were changed for this UI task.

The browser review covered 96 shared-navigation destinations plus responsive views for four key pages. The remaining templates (including record-specific detail and edit pages) still need route mapping and visual review; safe modal interactions also remain unreviewed.
