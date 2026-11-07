# Bank Draft Management Gap Analysis

**Inspection date:** 7 November 2026  
**Implementation status at inspection:** Before draft cancellation, regeneration/versioning, and import-page layout changes.

## Existing workflow

- `ImportBatch` represents one uploaded statement and stores its source file, generated CSV, statement metadata, workflow status, and aggregate counts.
- `ImportDraftRow` stores extracted and mapped rows separately from `BankTransaction`. PDF upload extracts directly into draft rows; CSV/TXT/XLSX uploads use the existing column-mapping step before draft creation.
- Draft row classification compares parsed rows to the live bank transaction table. Import is a separate explicit approval step with write-time duplicate checks and read-back verification.
- Verified transactions can be classified for follow-up or linked through the existing reconciliation workflow. The accounting classification page does not create accounting postings.
- Same-account, same-file uploads are deduplicated by SHA-256. There is no parent/version relation between batches, and the unique digest constraint does not support regenerating a second review version.
- The import review template already shows source and CSV links, counts, date confirmation, filters, row editing, approvals, and an inline Cancel action.

## Gaps against the attached request

| Area | Current state | Required change |
|---|---|---|
| Cancel draft | A POST action changes `status` to `cancelled`, excludes pending rows, then redirects to history. No confirmation, reason, actor, timestamp, or previous status is recorded. It blocks only `imported` and `completed`, so partial-import/verification-failure states need stronger safeguards. | Confirm the selected batch and period, record cancellation audit fields, preserve rows and source files, and block cancellation whenever import is in progress or any row has already been imported/verified. Block subsequent import and migration actions for cancelled batches. |
| Regenerate draft | No action reprocesses a previously uploaded file. | Add explicit batch selection and a regenerate action that reads that batch's retained source file, creates a new batch version, re-runs parsing and current matching, and never modifies prior versions or inserts transactions. |
| Version history | Batch rows have no version number or source-version relationship. | Add source lineage and monotonically increasing version numbers; expose links to earlier versions and a regenerated-row summary. |
| Review UI | Main comparison table relies on default table layout. Narration and justification can compete with many columns; full text is visible but difficult to scan. Most styling and filtering JavaScript are embedded in the template. | Add shared import-page styles, deliberate column sizing, contained horizontal scrolling, expandable full-text details, clearer summaries/actions, and responsive controls. |
| Help | Existing help documents upload, review, approval, and accounting boundaries, but not cancellation, regeneration/version history, or expanded row details. | Update route guidance and the central help manual after implementation. |

## Safety boundary

The active database is `db.sqlite3`. Before implementation, a SQLite online backup passed `integrity_check`, had zero foreign-key violations, and covered 129 tables. The configured statement storage directory was copied with SHA-256 checks into `backups/bank_draft_management_20261107_134500/`. That directory contains both synthetic test CSV fixtures and an existing bank-statement PDF; it is treated as sensitive source storage and will not be removed or overwritten.

Implementation and tests will preserve all existing statement files and bank transactions. Automated tests will use Django's isolated test database and temporary media storage.
