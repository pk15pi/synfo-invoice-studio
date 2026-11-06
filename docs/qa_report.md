# Project QA report

**Run date:** 2026-11-06  
**Result:** Broad QA completed in an isolated environment. One pre-existing project test remains failing.

## Safety and isolation

- Before QA, a complete backup was created at `C:\Users\PRASEN~1\AppData\Local\Temp\QA_BACKUPS\2026-11-06_033223_+0530_qa_backup`.
- The backup contains the SQLite database, private uploads, project backup archives, configured backup-store contents, and a checksum manifest. The database backup passed SQLite integrity and foreign-key checks; all 121 table row counts matched the source at capture time.
- QA ran against a separate database and copies of file storage under the backup directory's `qa_environment` folder. Its settings exposed an on-page `QA ENVIRONMENT - TEST DATA ONLY` banner. The original database and file roots were not configured as QA write targets.
- Final verification: live database and initial backup have identical table sets, row counts, and logical row content across 121 tables. Both pass `PRAGMA integrity_check` with zero foreign-key violations. The isolated QA copy also has the same row counts and passes those checks.
- Automated tests used Django's disposable test database, which the runner destroyed after each run. Attachment tests used temporary file storage. Browser requests were GET-only. No persistent QA business records were added.
- The QA server on `127.0.0.1:8001` was stopped, the port is no longer accepting connections, and the disposable test database file is absent. The original project server on port 8000 was not used for test writes.

## Checks and coverage

- `manage.py check`: passed with no issues.
- `showmigrations --plan`: all migrations were already applied. No migrations were run.
- Django test suite: **589 tests; 588 passed, 1 failed**. The remaining failure is `invoice_app.tests.IconActionTests.test_customer_row_actions_are_accessible_icons`; it expects a customer-row Delete icon that the current template does not render. The page has Deactivate and Edit actions. A delete route exists, but adding a destructive customer delete action requires a product decision about related records, so it was not introduced as a QA-only change.
- Expense attachment tests: all four pass after making their `TransactionTestCase` setup seed the accounting mappings and expense categories it needs. Transaction test database flushes had removed migration-seeded records, making this test group order-dependent.
- Browser smoke pass: 96 unique navigation destinations returned HTTP 200 on the isolated server, with zero console errors, page errors, or failed requests. Expense dashboard and expense reports were included after fixes. Customer search and expense filtering returned expected matches against copied records; navigation hover behavior was checked.
- Responsive inspection: four key pages were rendered at 1920×1080, 1440×900, 1366×768, 1024×768, 768×1024, and 390×844. No page-level horizontal overflow was observed in those 24 renders.
- This was broad route, rendering, and automated suite coverage. Destructive and state-changing workflows were not submitted through the browser.

## Defects found and addressed

- The expense dashboard template had a broken template block tag, causing `/expenses/` to return HTTP 500. The tag was repaired.
- The shared expense period filter had malformed comparisons and a split template tag, causing `/expenses/reports/` to return HTTP 500. Those template expressions were repaired.
- The expense attachment test fixture depended on migration-seeded categories and mappings surviving `TransactionTestCase` flushes. Its setup now seeds those prerequisites. The four attachment tests pass.

## Remaining finding

The only remaining test failure is the mismatch between the customer row-actions test and the current UI. The test's requested Delete icon is absent by design in the current template, and the QA brief did not define customer deletion behavior when related projects or records exist. Resolve that policy before changing the UI or its test expectation.

## Changed project files

- `expense_management/templates/expense_management/dashboard.html`
- `expense_management/templates/expense_management/_period_filter.html`
- `expense_management/tests.py`
- `docs/qa_report.md` (this report)

Other modified files shown by Git status predate this QA run and were left untouched. The QA backup and isolated working copy remain available in the temporary backup directory above for review.
