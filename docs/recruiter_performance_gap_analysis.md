# Recruiter Performance Review - Gap Analysis

**Review date:** 7 November 2026  
**Status:** Inspection and gap analysis complete; implementation and regression review are recorded in [recruiter_performance_implementation.md](recruiter_performance_implementation.md).

## Existing structure

- Employee identity, department, designation, employment status and history live in `employee_management.Employee` and `Department`. The active database has 17 employee records across active, probation and former-employee statuses.
- There is no recruiter flag or recruiter performance/assignment model. Recruiters therefore need to be identified on the existing Employee record; a second recruiter master would duplicate employee identity.
- `invoice_app` already has minimal candidate, client requisition, recruitment agreement and placement billing records. They do not capture recruiter assignments, candidate stage history, rejection stages or recruiter activity. The active database currently has no candidate, requisition or placement rows.
- Existing customer records are available to link to a job description. Job tracking needs to remain distinct from billing requisitions because a JD can have multiple recruiter assignments and independently managed recruitment performance outcomes.
- The application has no end-user sign-in or recruiter-level identity. `LocalOnlyMiddleware` describes it as a local, management-only application. The new module should follow that architecture and must not claim per-recruiter access controls.
- Shared design elements are `invoice_app/base.html`, page-specific guides in `invoice_app/page_help.py`, and the `?` help dialog. `reporting.charts` provides dependency-free SVG charts; `reporting.exports` and the employee exports demonstrate existing PDF/Excel conventions. `openpyxl` and ReportLab are installed.

## Planned implementation

1. Add a recruiter eligibility flag to the existing Employee record; use the employee ID, name, department, designation and employment status in selector labels. Default new assignments to active employees and keep historical assignments visible for former employees.
2. Add an independent `performance_review` app for job descriptions, recruiter assignments, summary activity, candidate applications, configurable stages/reasons/scoring weights, and append-only stage-change history.
3. Keep summary-only totals separate from candidate-verified outcomes in screens, formulas and exports. Enforce duplicate application and duplicate selection safeguards, and count unique JDs separately from recruiter-JD assignments.
4. Add dashboard, activity entry, recruiter-wise records, reports, settings, ranking, charts and filtered PDF/Excel exports using the shared visual and Help systems.
5. Add page-specific Help guides and update the central coverage documentation; verify with isolated Django tests and migration/system checks without using live records as test fixtures.

## Data protection

Before schema changes, a SQLite online backup was written to `backups/recruiter_performance_preimplementation_20261107_151328/db.sqlite3`. It passed `PRAGMA integrity_check` (`ok`), had zero foreign-key violations, and contains 129 tables. Its SHA-256 and table counts are in the adjacent `manifest.json`. The 27 files (7,714,883 bytes) under `private_media/employees` were copied into the same backup directory. No recruitment-related upload storage exists; unrelated bank statement media was not copied. No live business records have been modified.

## Scope and constraints

This is operational performance tracking, not a replacement ATS. Existing recruitment billing records and all employee/payroll/financial data remain unchanged. Since there is no authenticated user/role system, module permissions follow the current local management-only application model.

## Implementation follow-up

The schema was migrated after focused tests passed. The existing 17 employee records remain; recruiter eligibility is off by default and can be selected from Performance Settings. Default recruitment stages and rejection reasons were seeded. No financial, payroll or employee identity values were modified. The verified pre-implementation backup remains at the path above. See the implementation report for final tests, export details, requirement classifications and the one unrelated full-suite failure.
