# Document Generation Module — Phase 7 QA Report

**QA date:** 16 November 2026  
**Recommendation:** **NOT READY FOR PRODUCTION**

## Executive summary

The Phase 1–6 implementation passed the focused functional suites and nearly all of the complete project suite. Two existing indexed employee documents have no corresponding files in private storage or in the verified backup. Django's deployment check also reports four transport/cookie security warnings. Browser-based desktop and mobile visual checks could not run because no browser backend was available in this session. These items need resolution or explicit deployment/records-owner acceptance before production readiness can be declared.

QA was performed against an extracted copy of the active SQLite database and its private/public file archives. Pending document migrations were applied only to that copy. Tests used Django's isolated test database, and no QA records, test recipients, or outbound communications were written or sent to the source database.

## A. Phase audit

| Phase | Inventory and observed status |
|---|---|
| 1 — Foundation | Document categories/types, recipient/party references, document index, private file storage, metadata, navigation, role checks and page help are present. |
| 2 — Universal generation | Dynamic field definitions and source resolution feed Word/PDF generation and controlled downloads. Generation, validation, formatting and file handling are covered by tests. No separate frontend build manifest was present. |
| 3 — Standard library | The source database contains 9 categories, 150 document types, 39 templates, 39 template versions, 24 clauses, 105 template-clause links and 422 field definitions. Template/version/clause review and history workflows are covered by tests. |
| 4 — Approval and lifecycle | Draft/revision, configured approvals, rejection/revision requests, resubmission, issue, sending, amendment/superseding, cancellation, expiry and activity history are present and exercised in lifecycle tests. |
| 5 — Sharing and signing | Staff can record delivery, acknowledgement and reminders; upload private signed copies; track multiple signatories; verify/reject uploads; and revoke sharing records. Signed uploads are retained and hash-tracked. Public recipient links and in-app legal e-signatures are not implemented; delivery is recorded manually. |
| 6 — Automation and compliance | Rules, escalation recipients, idempotent execution history, in-app notifications, preferences, renewal tracking, compliance requirements/reviews and corrective actions are present. Scheduled execution requires the documented Windows Task Scheduler setup. Email/WhatsApp delivery is unavailable and disabled. Compliance evaluation is limited to known linked parties and is not a legal certification. |

Document Generation pages include the dashboard, catalogue, settings, standard library, type/category/clause/template/version workflows, generation/preview/download, lifecycle/approval pages, sharing/signature pages, renewal, automation/settings/history, notification/history/preferences, compliance/requirements/history/corrective actions and reports/exports. Central page-help coverage tests verify that named application routes are either mapped to a page guide or documented as a non-page action.

## B. Test results

- `manage.py check`: passed with no issues.
- `manage.py makemigrations --check --dry-run`: passed; no model/migration drift.
- Focused document, lifecycle, generation and help run: 63 tests passed.
- Post-fix regression subset: 27 tests passed.
- Report/dashboard rendering subset: 3 tests passed, including the automation table column alignment regression.
- Full suite before the final added dashboard test: 986 tests passed.
- Final full suite: 987 tests executed; 986 passed and one existing invoice-row Playwright test errored on a click option being outside the viewport. That exact test passed when rerun on its own. All 987 unique tests therefore passed at least once, but the final all-suite run was not clean and the isolated timeout remains a flake to monitor.
- Skips: none were reported in the test runner output.
- The in-app browser backend list was empty. Desktop/mobile viewport screenshots and manual page interactions were therefore blocked; this report does not claim those checks passed.
- Django `check --deploy`: four warnings remain (no HSTS duration, no HTTPS redirect, session cookie not secure, CSRF cookie not secure). These must be addressed in the actual TLS/deployment configuration before network exposure. The default app is configured for loopback hosts and access-control enforcement outside tests.
- No PostgreSQL compatibility run was performed; the project configuration and active database are SQLite.
- No dedicated performance benchmark or query-count audit was run.

The regression run exposed and this QA pass fixed: anonymous approval-report filtering raising a `TypeError`, the anonymous navigation linking to per-user notification preferences, older document-type form posts failing because a newly added renewal field was omitted, and misaligned automation activity table columns.

## C. Security report

- The active settings use SQLite, `DEBUG=False` by default, loopback-only `ALLOWED_HOSTS`, and access-control enforcement enabled by default outside tests.
- Document files use the private document root and controlled view/download paths. The backup verification matched every archived file against its manifest hash.
- Upload and lifecycle validation, role-based access tests, CSRF middleware configuration, private-download behavior, signed-copy retention, and append-only histories were exercised through the automated suite.
- Recipient-facing public links, link tokens/expiry, and electronic signature capture are not implemented. Sharing, acknowledgement and signature handoff are manually tracked by staff; no external email/WhatsApp was sent during QA.
- Residual deployment risk: the four Django security warnings above. Configure TLS, secure session/CSRF cookies and HSTS only after confirming the production termination/proxy design.
- No verified critical unauthorized-access defect remained after the QA fixes. Browser-based checks were unavailable.

## D. Data integrity report

**Active source database:** `E:\repos\QuantBud_Invoice_Studio\db.sqlite3`  
**Source integrity:** SQLite `integrity_check=ok`; zero foreign-key-check findings.  
**Source vs. pre-QA snapshot counts:** matching for all counted tables.

Source counts include 31 private document-index records; zero generated documents, approvals, shares, signed copies, automation executions or compliance reviews. The source also contains 6 automation rules and 6 rule-history events. No document-generation output file was orphaned from a generated-document record.

Two employee-document index entries (IDs 28 and 29) are already marked missing, have recorded size 0, and have no file in either the active private storage or the verified backup. No content/hash mismatches were found among present indexed files. These records were not repaired or deleted. The records owner should locate an authoritative copy or confirm these files are irrecoverable and retain the missing-file audit state.

Database-level foreign-key checks were clean on both the source and restored QA copy. Duplicate document-number and version-link checks had no generated document rows to evaluate; the relevant database uniqueness constraints and lifecycle tests were exercised instead.

## E. Help and user manual audit

- The page-help registry and route coverage test passed; every named application route is represented as a page guide or documented non-page action.
- Phase 5/6 guides and the application-wide manual include sharing, signing, automation, notifications, compliance, reports, renewal and limitations.
- Automated checks verified shared help modal markup, route-specific guide selection and safe guide serialization.
- Browser-only readability, scroll behavior and mobile placement could not be visually checked because the browser backend was unavailable.

## F. Backup, database and production configuration

- Fresh verified pre-QA archive: `C:\Users\prasenjit\AppData\Local\Temp\qb-phase7-qa-q3cwy4i2\backups\qb-manual-20261116-224957-714.zip`.
- The archive contains 50 files, passed its manifest/hash verification, and restored to an isolated copy with SQLite integrity `ok` and zero foreign-key findings.
- QA copy: `C:\Users\prasenjit\AppData\Local\Temp\qb-phase7-qa-q3cwy4i2\restored\db.sqlite3` plus its extracted media roots.
- Document migrations `0020_documentsignedcopy`, `0021_documentcompliancerequirementevent`, and `0022_documentautomationrule_escalation_recipients` succeeded on the QA copy. The active source database remains at document migration `0019_documentautomationruleevent_and_more`; it was not migrated during QA. Apply the pending migrations through the normal reviewed deployment process.
- Test databases were created and destroyed by Django's test runner. QA test data remained isolated from the source database.
- The app supports SQLite in its active configuration. PostgreSQL was not configured or tested.
- Configure HTTPS/security headers and the scheduled automation task before enabling network access or relying on automatic reminders.

## G. Remaining blockers and recommendation

1. Resolve or formally accept the two already-flagged missing employee files through a controlled records process; do not silently clear their missing status.
2. Configure and verify the production TLS/proxy, HSTS, secure session cookie and secure CSRF cookie settings. The current deployment check reports four warnings.
3. Complete desktop/mobile browser QA when a browser backend is available. Retest the intermittent invoice-row Playwright case in the deployment browser environment.
4. Apply pending document migrations in the production deployment only after its normal backup and release review.
5. Configure the documented scheduler. External email, public recipient access and legal e-signing remain unsupported capabilities.

**Final recommendation: NOT READY FOR PRODUCTION.** The automated document workflows and source database integrity checks are strong, but missing source files and unresolved deployment hardening/visual QA prevent an unqualified readiness sign-off.
