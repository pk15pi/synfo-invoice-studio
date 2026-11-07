# Project Page, Filter and Back Navigation Audit

**Audit date:** 7 November 2026

## Scope and method

Inventory covers first-party HTML pages in the project URL configuration (excluding Django Admin-generated CRUD route patterns from the route table). It includes static and record-specific HTML page routes. Navigation links were traversed on an isolated database copy; only GET requests were made. Destructive POST actions, exports and downloads were not submitted. Django Admin login and authenticated Admin-generated model pages were checked in a separate pass, summarized in Results.

## Results

- Traversed **1,099** linked destinations; **1,053** returned HTML and the rest were successful file responses. One normal redirect was followed to its final page. All destinations finished at HTTP 200.
- The crawl covered **252** normalized route shapes and found **111** distinct GET forms. Each filter field was exercised individually and each form was exercised with combined values: **776** requests, all HTTP 200.
- Every rendered HTML page in the crawl contained the global Back control. A browser check from a filtered employee list to an employee record and back returned to the exact prior URL, including `q`, `status`, and `department` query parameters.
- Authenticated Django Admin review covered 123 generated route shapes and 1,432 filter/list query requests. All filter/list requests returned HTTP 200. The Admin Add page for Document Template Versions initially failed because its preview link tried to reverse a URL with no saved object ID; it now renders correctly and shows the existing save-first message. The route sweep returned HTTP 200 for accessible screens and expected HTTP 403 for 17 Add screens disallowed by their model permissions; no unexpected server errors remain. Admin Back control is present.
- Checks ran against a temporary copy of the local database; no live records were created or changed.

## Filter inventory

| Module / page | GET filter or selector fields checked |
|---|---|
| Accounting journal register | `q`, `status`, `period`, `fy`, `month`, `date_from`, `date_to` |
| Accounting ledger / account ledger | `account`, `period`, `fy`, `month`, `date_from`, `date_to` |
| Bank transactions / reconciliation / books | `status` (transactions), `account` (books), `period`, `fy`, `month`, `date_from`, `date_to` |
| Compliance checks and registers | `start`, `end`, `which`, `status`, `kind` as applicable |
| Document index / catalogue / template library | `q`, `category`, `sensitivity`, `state`, `status`; document generation selectors `category`, `type` |
| Employee list | `q`, `status`, `department` |
| Attendance daily / monthly / summaries / reports | `date`, `month`, `year`, `report`, `employee`, `status` as applicable |
| Expense dashboard / expense book / claims / vendors / reports | `period`, `fy`, `month`, `date_from`, `date_to`, `q`, `type`, `category`, `vendor`, `employee`, `department`, `payment_status`, `claim_status`, `status`, `claim_date_from`, `claim_date_to`, `expense_date_from`, `expense_date_to` as applicable |
| Financial report dashboard / catalogue | `period`, `financial_year`, `financial_year_part`, `month`, `date_from`, `date_to`, `q`, `category` |
| Invoice list / receipts / manpower deployments / recruitment placements | `period`, `financial_year`, `year`, `month`, `from`, `to`, `client`, `candidate`, `designation`, `status`, `invoice_status`, `method`, `billing_year`, `billing_month`, `joining_from`, `joining_to`, `recruitment_from`, `recruitment_to`, `mode`, `q` as applicable |
| Payroll / payslips / advances / payroll reports | `month`, `year`, `department`, `fy`, `employee`, `status`, `report` as applicable |
| Performance reports / Daily Brief | `year`, `fy_year`, `from_date`, `to_date`, `period`, `month`, `reference_date`, `quarter`, `half`, `recruiter`, `recruiter_search`, `jd_search`, `page_size` as applicable |
| Reports & Analytics dashboard / custom reports / individual reports | `period`, `fy`, `fy_part`, `month`, `date_from`, `date_to`, `prior`, `dataset`, `group_by`, `cols`, `employee`, `department`, `account`, and report-specific dimensions |
| Other observed selectors | Asset register `q`; projects customer selector `c`; audit log `model`, `object_id`, `action`, `source`, date range and `q` |

The Bank Imports and Transfers pages do not expose GET filters; their lists support sorting. Filter forms were checked with representative valid dropdown/date values and a no-match search string where applicable. HTTP status confirms rendering and parameter handling; it does not replace a business-owner review of report totals.

The Daily Brief recruiter selector had no selectable options in the local data snapshot, so that filter was inventoried but could not be exercised with a recruiter ID. Its other date and search filters were exercised.

## Back navigation

A global **? Back** control is present on application pages and Django Admin pages. When the browser has an in-app referrer, it uses browser history so query parameters and scroll position are restored. If a page is opened directly or from outside the app, the control falls back to the project home page.

## Page route inventory

### Accounting

| URL path pattern | Route name | View |
|---|---|---|
| `/accounting/` | `home` | `home` |
| `/accounting/accounts/` | `accounts` | `accounts` |
| `/accounting/accounts/<int:pk>/edit/` | `account_edit` | `account_form` |
| `/accounting/accounts/<int:pk>/ledger/` | `ledger` | `ledger_view` |
| `/accounting/accounts/new/` | `account_new` | `account_form` |
| `/accounting/journal/` | `journal` | `journal_list` |
| `/accounting/journal/<int:pk>/` | `journal_detail` | `journal_detail` |
| `/accounting/journal/new/` | `journal_new` | `journal_new` |
| `/accounting/ledger/` | `ledger_pick` | `ledger_view` |
| `/accounting/mappings/` | `mappings` | `mappings` |
| `/accounting/periods/` | `periods` | `periods` |

### Assets

| URL path pattern | Route name | View |
|---|---|---|
| `/assets/` | `home` | `dashboard` |
| `/assets/assignments/` | `assignments` | `placeholder` |
| `/assets/leases/` | `leases` | `placeholder` |
| `/assets/maintenance/` | `maintenance` | `placeholder` |
| `/assets/register/` | `register` | `register` |
| `/assets/register/new/` | `asset_new` | `placeholder` |

### Attendance & Leave

| URL path pattern | Route name | View |
|---|---|---|
| `/attendance/` | `daily` | `daily` |
| `/attendance/balances/` | `balances` | `balances` |
| `/attendance/balances/<int:pk>/edit/` | `balance_edit` | `balance_edit` |
| `/attendance/corrections/` | `corrections` | `corrections` |
| `/attendance/employee/<int:pk>/` | `month_grid` | `month_grid` |
| `/attendance/holidays/` | `holidays` | `holidays` |
| `/attendance/holidays/<int:pk>/edit/` | `holiday_edit` | `holiday_form` |
| `/attendance/holidays/new/` | `holiday_new` | `holiday_form` |
| `/attendance/import/` | `import` | `import_view` |
| `/attendance/leave-types/` | `leave_types` | `leave_types` |
| `/attendance/leave-types/<int:pk>/edit/` | `leave_type_edit` | `leave_type_form` |
| `/attendance/leave-types/new/` | `leave_type_new` | `leave_type_form` |
| `/attendance/leave/` | `applications` | `applications` |
| `/attendance/leave/new/` | `application_new` | `application_new` |
| `/attendance/reports/` | `reports` | `reports` |
| `/attendance/summary/` | `summary` | `summary` |
| `/attendance/weekly-off/` | `weekly_off` | `weekly_off` |

### Audit

| URL path pattern | Route name | View |
|---|---|---|
| `/audit/` | `log` | `log` |
| `/audit/object/<str:app_label>/<str:model>/<str:object_id>/` | `history` | `history` |

### Backups

| URL path pattern | Route name | View |
|---|---|---|
| `/backups/` | `home` | `home` |
| `/backups/<int:pk>/` | `detail` | `detail` |
| `/backups/import/<uuid:package_id>/` | `merge_preview` | `merge_preview` |

### Bank

| URL path pattern | Route name | View |
|---|---|---|
| `/bank/` | `accounts` | `accounts` |
| `/bank/accounts/<int:pk>/` | `account_detail` | `account_detail` |
| `/bank/accounts/<int:pk>/edit/` | `account_edit` | `account_form` |
| `/bank/accounts/<int:pk>/entry/` | `manual_entry` | `manual_entry` |
| `/bank/accounts/<int:pk>/import/` | `import_upload` | `import_upload` |
| `/bank/accounts/<int:pk>/reconciliation/` | `reconciliation` | `reconciliation` |
| `/bank/accounts/<int:pk>/transactions/` | `transactions` | `transactions` |
| `/bank/accounts/new/` | `account_new` | `account_form` |
| `/bank/book/<str:kind>/` | `book` | `book` |
| `/bank/imports/` | `imports` | `imports` |
| `/bank/imports/<int:pk>/` | `import_batch` | `import_batch` |
| `/bank/imports/<int:pk>/accounting-review/` | `import_migration` | `import_migration` |
| `/bank/imports/<int:pk>/review/` | `import_review` | `import_review` |
| `/bank/legacy-preview/` | `legacy_preview` | `legacy_preview` |
| `/bank/transactions/<int:pk>/` | `txn_detail` | `txn_detail` |
| `/bank/transfers/` | `transfers` | `transfers` |

### Compliance

| URL path pattern | Route name | View |
|---|---|---|
| `/compliance/` | `home` | `home` |
| `/compliance/check/` | `check` | `check` |
| `/compliance/due-dates/` | `due_dates` | `due_dates` |
| `/compliance/due-dates/<int:pk>/` | `due_date_edit` | `due_dates` |
| `/compliance/gst/input/` | `gst_input` | `gst_input` |
| `/compliance/gst/output/` | `gst_output` | `gst_output` |
| `/compliance/payments/` | `payments` | `payments` |
| `/compliance/payroll/` | `payroll` | `payroll_stat` |
| `/compliance/rules/` | `rules` | `rules` |
| `/compliance/rules/<int:pk>/` | `rule_edit` | `rule_edit` |
| `/compliance/rules/new/` | `rule_new` | `rule_edit` |
| `/compliance/tds/` | `tds` | `tds` |

### Document & Agreement Management

| URL path pattern | Route name | View |
|---|---|---|
| `/documents/` | `dashboard` | `dashboard` |
| `/documents/<slug:section>/` | `placeholder` | `placeholder` |
| `/documents/catalogue/` | `catalogue` | `catalogue` |
| `/documents/generate/` | `generate` | `generate_document` |
| `/documents/library/` | `library` | `library` |
| `/documents/library/categories/` | `categories` | `category_library` |
| `/documents/library/clauses/` | `clauses` | `clause_library` |
| `/documents/library/clauses/<int:clause_id>/edit/` | `clause_edit` | `clause_edit` |
| `/documents/library/clauses/new/` | `clause_new` | `clause_edit` |
| `/documents/library/templates/` | `templates` | `template_library` |
| `/documents/library/templates/<int:template_id>/` | `template_detail` | `template_detail` |
| `/documents/library/templates/<int:template_id>/edit/` | `template_edit` | `template_edit` |
| `/documents/library/templates/new/` | `template_new` | `template_edit` |
| `/documents/library/types/` | `types` | `type_library` |
| `/documents/library/versions/` | `template_versions` | `template_versions` |
| `/documents/library/versions/<int:version_id>/edit/` | `template_version_edit` | `template_version_edit` |
| `/documents/settings/` | `settings` | `settings` |
| `/documents/template-preview/<int:version_id>/` | `template_preview` | `template_preview` |

### Document index

| URL path pattern | Route name | View |
|---|---|---|
| `/doc-index/` | `list` | `doc_list` |
| `/doc-index/<int:pk>/edit/` | `edit` | `edit` |
| `/doc-index/check/` | `check` | `check` |
| `/doc-index/upload/` | `upload` | `upload` |

### Employee & Payroll

| URL path pattern | Route name | View |
|---|---|---|
| `/departments/` | `departments` | `departments` |
| `/employees/` | `employees` | `employee_list` |
| `/employees/<int:pk>/` | `employee_detail` | `employee_detail` |
| `/employees/<int:pk>/documents/<int:did>/delete/` | `document_delete` | `document_delete` |
| `/employees/<int:pk>/documents/<int:did>/history/` | `document_history` | `document_history` |
| `/employees/<int:pk>/edit/` | `employee_edit` | `employee_form` |
| `/employees/<int:pk>/salary/<int:from_id>/revise/` | `salary_revise` | `salary_new` |
| `/employees/<int:pk>/salary/new/` | `salary_new` | `salary_new` |
| `/employees/new/` | `employee_new` | `employee_form` |
| `/hr/` | `home` | `hr_dashboard` |
| `/payroll/` | `payroll` | `payroll_dashboard` |
| `/payroll/advances/` | `advances` | `advances` |
| `/payroll/advances/<int:pk>/` | `advance_detail` | `advance_detail` |
| `/payroll/advances/employee/<int:pk>/` | `advance_ledger` | `employee_ledger` |
| `/payroll/advances/new/` | `advance_new` | `advance_new` |
| `/payroll/payslips/` | `payslips` | `payslips` |
| `/payroll/record/<int:rid>/` | `payroll_detail` | `payroll_detail` |
| `/payroll/record/<int:rid>/edit/` | `payroll_edit` | `payroll_edit` |
| `/payroll/reports/` | `reports` | `reports` |
| `/payroll/settings/` | `settings` | `payroll_settings` |

### Expenses

| URL path pattern | Route name | View |
|---|---|---|
| `/expenses/` | `dashboard` | `dashboard` |
| `/expenses/all/` | `all` | `all_expenses` |
| `/expenses/categories/` | `categories` | `categories` |
| `/expenses/categories/<int:cat_pk>/sub/<int:pk>/edit/` | `subcategory_edit` | `subcategory_form` |
| `/expenses/categories/<int:cat_pk>/sub/new/` | `subcategory_new` | `subcategory_form` |
| `/expenses/categories/<int:pk>/edit/` | `category_edit` | `category_form` |
| `/expenses/categories/new/` | `category_new` | `category_form` |
| `/expenses/claims/<int:pk>/` | `claim_detail` | `claim_detail` |
| `/expenses/claims/<int:pk>/edit/` | `claim_edit` | `claim_form` |
| `/expenses/claims/new/` | `claim_new` | `claim_form` |
| `/expenses/company/<int:pk>/` | `expense_detail` | `expense_detail` |
| `/expenses/company/<int:pk>/edit/` | `expense_edit` | `expense_form` |
| `/expenses/company/new/` | `expense_new` | `expense_form` |
| `/expenses/files/<str:kind>/<int:pk>/delete/` | `att_delete` | `attachment_delete` |
| `/expenses/files/<str:kind>/<int:pk>/replace/` | `att_replace` | `attachment_replace` |
| `/expenses/reports/` | `reports` | `report_view` |
| `/expenses/reports/<str:key>/` | `report` | `report_view` |
| `/expenses/vendors/` | `vendors` | `vendors` |
| `/expenses/vendors/<int:pk>/` | `vendor_detail` | `vendor_detail` |
| `/expenses/vendors/<int:pk>/edit/` | `vendor_edit` | `vendor_form` |
| `/expenses/vendors/new/` | `vendor_new` | `vendor_form` |

### Financial Reports

| URL path pattern | Route name | View |
|---|---|---|
| `/financial-reports/` | `dashboard` | `dashboard` |
| `/financial-reports/catalogue/` | `catalogue` | `catalogue` |

### Home

| URL path pattern | Route name | View |
|---|---|---|
| `/` | `home` | `home` |

### Invoice Management

| URL path pattern | Route name | View |
|---|---|---|
| `/business/` | `profile_edit` | `profile_edit` |
| `/customers/` | `clients` | `clients` |
| `/customers/<int:client_id>/projects/` | `projects` | `projects` |
| `/customers/<int:client_id>/projects/<int:pk>/edit/` | `project_edit` | `project_edit` |
| `/customers/<int:client_id>/projects/add/` | `project_add` | `project_edit` |
| `/customers/<int:pk>/` | `client_detail` | `client_detail` |
| `/customers/<int:pk>/edit/` | `client_edit` | `client_edit` |
| `/customers/add/` | `client_add` | `client_edit` |
| `/description-templates/` | `description_templates` | `description_templates` |
| `/description-templates/<int:pk>/edit/` | `description_template_edit` | `description_template_edit` |
| `/description-templates/add/` | `description_template_add` | `description_template_edit` |
| `/format-templates/` | `format_templates` | `format_templates` |
| `/format-templates/<int:pk>/edit/` | `format_template_edit` | `format_template_edit` |
| `/format-templates/add/` | `format_template_add` | `format_template_edit` |
| `/invoice/<int:pk>/` | `invoice_detail` | `invoice_detail` |
| `/invoice/<int:pk>/edit/` | `invoice_edit` | `invoice_edit` |
| `/invoice/<int:pk>/payments/<int:payment_pk>/delete/` | `payment_delete` | `payment_delete` |
| `/invoice/<int:pk>/payments/<int:payment_pk>/edit/` | `payment_edit` | `payment_edit` |
| `/invoice/<int:pk>/payments/add/` | `payment_add` | `payment_edit` |
| `/invoice/new/` | `new_invoice` | `invoice_edit` |
| `/invoices/` | `invoice_list` | `invoice_list` |
| `/invoicing/` | `dashboard` | `dashboard` |
| `/manpower/` | `manpower_deployments` | `manpower_deployments` |
| `/manpower/<int:pk>/edit/` | `manpower_deployment_edit` | `manpower_deployment_edit` |
| `/manpower/add/` | `manpower_deployment_add` | `manpower_deployment_edit` |
| `/projects/` | `projects_hub` | `projects_hub` |
| `/receipts/` | `receipts` | `receipts` |
| `/recruitment/` | `recruitment_dashboard` | `recruitment_dashboard` |
| `/recruitment/agreements/<int:pk>/edit/` | `recruitment_agreement_edit` | `recruitment_agreement_edit` |
| `/recruitment/agreements/add/` | `recruitment_agreement_add` | `recruitment_agreement_edit` |
| `/recruitment/candidates/<int:pk>/edit/` | `recruitment_candidate_edit` | `recruitment_candidate_edit` |
| `/recruitment/candidates/add/` | `recruitment_candidate_add` | `recruitment_candidate_edit` |
| `/recruitment/placements/<int:pk>/edit/` | `recruitment_placement_edit` | `recruitment_placement_edit` |
| `/recruitment/placements/add/` | `recruitment_placement_add` | `recruitment_placement_edit` |
| `/recruitment/requisitions/<int:pk>/edit/` | `recruitment_requisition_edit` | `recruitment_requisition_edit` |
| `/recruitment/requisitions/add/` | `recruitment_requisition_add` | `recruitment_requisition_edit` |

### Performance Review

| URL path pattern | Route name | View |
|---|---|---|
| `/performance/` | `dashboard` | `dashboard` |
| `/performance/activities/<int:pk>/edit/` | `activity_edit` | `activity_edit` |
| `/performance/activity/` | `activity` | `activity_entry` |
| `/performance/applications/<int:pk>/edit/` | `application_edit` | `application_edit` |
| `/performance/daily-brief/` | `daily_brief_records` | `daily_brief_records` |
| `/performance/daily-brief/<int:pk>/` | `daily_brief_detail` | `daily_brief_detail` |
| `/performance/daily-brief/<int:pk>/edit/` | `daily_brief_edit` | `daily_brief_edit` |
| `/performance/daily-brief/analytics/` | `daily_brief_analytics` | `daily_brief_analytics_page` |
| `/performance/jds/<int:pk>/edit/` | `jd_edit` | `jd_edit` |
| `/performance/records/` | `records` | `records` |
| `/performance/reports/` | `reports` | `reports` |
| `/performance/settings/` | `settings` | `settings` |
| `/performance/settings/stages/<int:pk>/edit/` | `stage_edit` | `stage_edit` |

### Reports & Analytics

| URL path pattern | Route name | View |
|---|---|---|
| `/reports/` | `dashboard` | `dashboard` |
| `/reports/section/<slug:slug>/` | `section` | `section` |
| `/reports/sources/` | `sources` | `sources` |

