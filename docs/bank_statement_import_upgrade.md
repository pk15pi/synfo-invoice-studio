# Bank Statement Import Upgrade

**Implemented:** 7 November 2026  
**Data safety:** Additive schema changes only. No live bank transaction, expense, journal, invoice, payroll or opening-balance rows were created or edited by the implementation.

## Workflow

1. Open **Bank → Statement Imports** and choose the bank/cash account for the statement.
2. Upload PDF, CSV/TXT or XLSX (maximum 20 MB). An exact file already uploaded for the account opens the existing batch instead of creating another.
3. Text-based PDFs are extracted locally with PyMuPDF. CSV/TXT and XLSX retain the existing column mapping and are converted into the same draft-row review model.
4. Review the detected statement dates, balances, totals, extraction warnings and individual rows. Dates are editable; out-of-period rows and statement warnings need explicit acknowledgement.
5. Review classifications and reasons, edit uncertain rows, exclude rows, approve straightforward rows in a group, or approve possible duplicates/modifications individually.
6. Import only approved rows. The application reloads each changed/created bank transaction and compares the saved fields to the approved draft. Failures remain visible in the batch and block completion of that row.
7. Download the generated CSV, reopen the original statement and review batches from Import History.
8. After verified bank import, choose accounting classification or defer. The existing reconciliation UI can link a transaction to a journal line.
9. For a selected statement in Import History, choose **Regenerate Draft** to re-read that exact stored source and create a new numbered version. Each version has a history link and a summary of unchanged, changed, new, and removed rows. Matching runs against the current bank transaction register; regeneration never inserts transactions.
10. Choose **Cancel Draft** on an eligible unimported version, confirm the batch and statement period, and provide a reason. The app records the prior status, local actor or user, timestamp, and reason. It preserves the upload, generated CSV, row data, and decisions. Imported, completed, in-progress, already-cancelled, or partially imported batches cannot be cancelled as ordinary drafts.

The original upload is stored under the configured local `MEDIA_ROOT`; downloads remain behind the existing local-only bank routes. Account identifiers in the generated CSV use a masked value. Unapproved rows never reach `BankTransaction`. The importer never creates expenses, payments, receipts, payroll entries or journals automatically.

## Comparison and duplicate safeguards

- Full-file SHA-256 plus a database unique constraint prevents the same file from creating multiple batches for one bank account.
- Existing transactions are compared using bank account, exact bank reference where present, date, value date, debit/credit direction and amount, running balance, and normalized narration. The original narration is retained unchanged.
- Exact records link to the existing bank transaction and are not inserted again. A matching reference with changed fields becomes a proposed modification. Same-date/amount rows with differing references/narrations become possible duplicates. Previously ignored rows require review.
- Same-file identical transaction lines receive a stable occurrence fingerprint; the second row requires explicit review before being treated as a separate transaction.
- `BankTransaction.row_hash` remains unique. The import path uses an atomic transaction, account locking where supported, a final comparison before insertion and the database uniqueness constraint. New writes and approved modifications are read back. Failed read-back restores the old record or rolls back the new insert within the same transaction.
- Existing rows with active reconciliation matches cannot be modified. Proposed before-values and the source batch remain on the draft row for review.
- Regenerated batches link to their original upload batch and increment the version number. A regenerated review never overwrites prior draft rows or decisions; the original statement file is referenced by each version and its digest is verified before extraction.
- Cancellation changes only the batch workflow status and cancellation audit fields. It does not exclude or delete rows, alter a transaction, or reverse a linked journal entry. Cancellation and regeneration use database row locks and terminal-status checks to prevent conflicting operations.

## PDF extraction and limitations

- Text-based PDFs with selectable text are parsed locally. The extractor detects common bank names, masked account identifiers, explicit statement periods, opening/closing balances, table headings, transaction/value dates, narration, references, debit/credit columns and balances. It joins wrapped narration lines and retains source page and raw extracted text.
- Opening/closing balance, page-total and summary lines are retained in the draft as **Non-transaction row** so they cannot be imported as bank transactions.
- If no explicit period is readable, the transaction-date span is shown as an inferred range and a warning requires user confirmation. Dates are never invented.
- Scanned PDFs use PyMuPDF's local OCR bridge only when Tesseract is installed and available through `TESSERACT_CMD` (or `PATH`). Tesseract was not installed on this machine. An image-only PDF therefore fails with a clear message and the user must configure local OCR or use CSV/Excel fallback. No cloud OCR or paid API is used.
- Bank PDFs vary widely. The table parser uses header locations and conservative heuristics. Low-confidence/missing date, narration or debit/credit values stay in the uncertain class and must be edited or excluded. Users must verify all rows against the original statement.

## Accounting classification boundary

The accounting review page records a user-selected category and exposes the existing bank reconciliation link. Deferring leaves the imported bank records unchanged. This feature does **not** create or mutate records in Expense Book, vendor payments, payroll, reimbursements, receipts, tax, assets or journals. Those modules do not expose a common transaction-safe migration service, and guessing a destination or creating entries could duplicate live accounting. Complete any posting in the existing module after checking for existing records, then reconcile the bank transaction. Automated creation and destination read-back verification remain not implemented.

## Schema and files

- Existing `ImportBatch` is extended with file references, SHA-256, dates/balances, metadata, status, creator and stage counts.
- New `ImportDraftRow` holds extracted source fields, classification, justification, review decision, comparison link, before-values and verification outcome. This is separate from `BankTransaction`.
- Migrations: `0003_statement_draft_review`, `0004_statement_migration_review`, `0005_importbatch_failed_extractions_importbatch_matched_and_more`, `0006_importdraftrow_fingerprint_occurrence_and_more`, `0007_import_transaction_count`, `0008_draft_versioning_and_cancellation`, and `0009_cancellation_actor_label`.
- PDFs and generated CSVs are stored on the configured filesystem storage. Historical imports retain the original `ImportBatch` fields and existing `BankTransaction.row_hash` constraints.
- Pre-implementation backup: `backups/bank_statement_import_preimplementation_20261107_073038/`; SQLite `integrity_check=ok`, 128 tables, zero foreign-key violations. `MEDIA_ROOT` was absent when inspected. The backup manifest records the database path, hash and storage check.

## Requirement status

| Requirement | Status | Notes |
|---|---|---|
| Preserve CSV/XLSX imports | **COMPLETED** | Existing parser and mapping remain; rows now enter the draft workflow in the UI. Direct legacy importer calls remain compatible. |
| PDF upload and original retention | **COMPLETED** | PDF signature checked, 20 MB and 250-page limits, password/corruption errors, local file retained. |
| Text PDF extraction | **PARTIALLY COMPLETED** | Header-position extraction, dates, debit/credit, references, balances, wrapped narration, summary rows and page tracking. Heuristics cannot support every bank layout without manual review. |
| Scanned PDF OCR | **PARTIALLY COMPLETED** | Local Tesseract OCR is used when installed/configured. It is absent from the current machine; no OCR result can be verified here. |
| Downloadable structured CSV | **COMPLETED** | Includes masked account, statement period, row fields, confidence, source page, class and reason. |
| Detected/editable period and warning acknowledgement | **COMPLETED** | Explicit header period preferred; otherwise transaction span is marked inferred. Outside-period rows and arithmetic warnings require confirmation. |
| Separate draft table and batch history | **COMPLETED** | `ImportDraftRow` keeps source/review data apart from actual transactions; batches retain status and counters. |
| Exact/new/possible duplicate/modification/linked/uncertain/non-transaction classification | **COMPLETED** | Conservative matching and explicit row explanations; active journal matches are surfaced. |
| Row edit, exclusion, approval, filtering and pagination | **COMPLETED** | Draft rows can be edited and reclassified; selection only bulk-approves straightforward rows. |
| Duplicate prevention for exact uploads and transaction fingerprints | **PARTIALLY COMPLETED** | File digest DB constraint, existing row hash, final re-check, references and multi-factor comparison. Ambiguous no-reference transactions require manual review. |
| Approved bank import and read-back | **COMPLETED** | Only approved rows are inserted/modified; writes are reloaded and compared; failures remain visible. |
| Automated Expense Book/payment/receipt/payroll/tax/asset migration | **NOT IMPLEMENTED** | The separate review records classification/defer choices and links to existing reconciliation. It does not post to accounting modules. |
| Statement balance arithmetic and running-balance validation | **PARTIALLY COMPLETED** | PDF opening/closing values and available row balances are checked; explicit summary data is not reliably available in every CSV or bank layout. |
| Updated page help and user manual | **COMPLETED** | The route guide and central help coverage report describe supported formats, stages, classification, warnings, approval, verification and accounting boundary. |

## Backup, tests and setup

The active database before the draft-management changes was backed up at `backups/bank_draft_management_20261107_134500/`. Its SQLite `integrity_check` was `ok`, it had zero foreign-key violations and 129 tables. The statement-storage directory was copied into the same backup with SHA-256 hashes for all 16 files. After migration, integrity and foreign-key checks passed; no table counts changed except migration history, and the bank batch, draft-row, bank-transaction, journal-entry and journal-line counts remained the same.

Automated bank tests cover existing CSV/XLSX parsing, PDF metadata/transaction extraction, wrapped narration, multiple pages, date formats, invalid/scanned/password-protected PDFs, exact file re-import, period acknowledgement, duplicate classification, separate draft storage, explicit row approval, proposed modification, read-back verification, cancellation rules/audit/preservation, PDF/CSV/XLSX regeneration, version history, source-file integrity checks, and no transaction insertion during regeneration. The bank, page-help and Attendance suites passed together: 80 tests. UI checks covered the requested 12 desktop, tablet and mobile viewports across the imports list, upload, draft review, and accounting review pages; no horizontal page overflow was found and both cancellation dialogs fit their viewports.

The full project suite after these changes ran 643 tests: 642 passed and the same existing customer-action assertion failed because it expects Delete while the customer page offers Deactivate and Edit. It does not exercise the bank import changes. The focused bank/page-help/Attendance run passed all 80 tests.

The active `media/bank_statements/` directory contains retained statement source files and generated CSVs, including an existing original bank PDF, as well as duplicate test fixture CSVs from the prior implementation session. It was copied and hash-verified before this task and was not changed. A previous automatic safety review rejected recursive cleanup because the directory can contain sensitive source records; the mixed directory is being preserved.

Apply the additive schema changes with:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

For scanned statements, install Tesseract locally and set `TESSERACT_CMD` to its executable path. `TESSDATA_PREFIX` may be set to its language-data directory if auto-detection does not work. The app does not send statements to an external service.

## Draft-management requirement status

| Requirement | Status | Verification |
|---|---|---|
| Cancel an eligible draft with confirmation and required reason | **COMPLETED** | Confirmation names the selected batch/version and period. The reason is required and stored with previous status, actor, and timestamp. |
| Block cancellation after import, during import, or after cancellation | **COMPLETED** | Status and imported/verified transaction checks guard the action; import and accounting classification are blocked for cancelled drafts. |
| Preserve uploaded file, generated CSV, extracted rows and review decisions | **COMPLETED** | Cancellation only updates batch status/audit fields; tests verify source paths and row decisions remain. |
| Regenerate PDF, CSV and XLSX from an explicitly selected retained upload | **COMPLETED** | The batch review page is the selected upload context; regeneration verifies the original digest and creates a new child version. |
| Preserve prior versions and compare regenerated rows | **COMPLETED** | Version history and summaries track unchanged, changed, new and removed rows; old decisions stay on their original rows. |
| Prevent duplicate bank transactions during regeneration | **COMPLETED** | Regeneration never writes to `BankTransaction`; each new draft row is reclassified against the current register and import rechecks at write time. |
| Improve import controls, tables, details, actions and responsive layouts | **COMPLETED** | Import history, upload, mapping, review, accounting review and completion pages use shared responsive styles. Tables scroll independently; full narration, references and justifications are expandable. |
| Test requested viewport sizes and page-level overflow | **COMPLETED** | Browser matrix checked 12 specified sizes across five import screens with zero overflow; final desktop and mobile screenshots were reviewed. |
| Compare before and after screenshots | **PARTIALLY COMPLETED** | Final screenshots were rendered with statement values masked. No saved pre-change screenshots were available for comparison. |
| Full project test suite clean | **PARTIALLY COMPLETED** | One unrelated existing customer-action assertion still expects Delete rather than the current Deactivate/Edit actions. All focused tests pass. |
| Help / User Manual updated | **COMPLETED** | Bank help explains cancellation, regeneration, versions, duplicate checks, expanded details and table scrolling; coverage includes the new POST routes. |
