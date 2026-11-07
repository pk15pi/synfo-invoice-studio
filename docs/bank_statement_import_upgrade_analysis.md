# Bank Statement Import Upgrade - Architecture Analysis

**Reviewed:** 7 November 2026  
**Database safety:** Pre-implementation snapshot verified before design work; no live financial rows changed.

## Existing implementation

- `bank_management.ImportBatch` is the existing statement import batch. It stores a bank account, filename, status, raw source rows and user-selected column mapping in JSON, counts, error notes and timestamps. It currently does not retain the uploaded file.
- `bank_management.BankTransaction` is the actual statement transaction table. It stores transaction/value dates, original narration, reference, debit, credit, running balance, source, batch, a unique row hash and reconciliation status.
- `bank_management.ReconciliationMatch` connects a bank transaction to an `accounting.JournalLine`; `bank_management.services` enforces remaining-amount and sign rules. Other accounting/payment modules do not have a direct FK to `BankTransaction`.
- `bank_management.importer` accepts CSV, TXT and XLSX. It previews a column mapping, flags duplicates by the current row hash, and `confirm()` inserts rows directly into `BankTransaction` inside a database transaction. It does not provide a persistent per-row draft/review record, overlap-aware comparison, PDF support, read-back verification, or accounting migration workflow.
- `BankTransaction.row_hash` is unique and currently fingerprints bank account, transaction date, signed amount, lower-cased reference, normalized narration and occurrence index. This preserves repeated same-day transactions with identical details, but can miss a duplicate if a reference/narration was altered, and its occurrence rule is statement-order-sensitive.
- The import UI is `imports.html`, `import_upload.html`, `import_batch.html`, and `import_done.html`; routes are protected by the existing `local_only` wrapper. Existing CSV/XLSX column mapping is a compatibility constraint.
- Bank imports do not create expenses or journal entries. New statement rows remain unreconciled until matched by the existing reconciliation UI. Accounting posting services exist in `accounting.postings` and module services, but there is no common safe transaction-to-expense migration API.

## Runtime and storage

- Active database: `db.sqlite3` (SQLite). A database backup was made at `backups/bank_statement_import_preimplementation_20261107_073038/db.sqlite3`; SQLite integrity passed, there are 128 tables and no foreign-key violations. SHA-256: `d7596fb8b5ae76927f3cc09698551d47cc345f821f068792bb6d9b7798194f2f`.
- Configured `MEDIA_ROOT` is `E:\repos\QuantBud_Invoice_Studio\media`; it did not exist at review time. The bank import must create protected application-owned storage and must not expose untrusted uploads as executable content.
- PyMuPDF (`fitz`), Pillow and openpyxl are installed. `pdfplumber`, `pypdf`, `pytesseract`, `pdf2image`, and a `tesseract` executable were not detected. Text-based PDF extraction is available locally. OCR requires an explicitly installed/configured local OCR executable; no cloud API is available or proposed. A scanned PDF without OCR support must fail clearly and remain available for manual CSV/XLSX fallback rather than produce guessed transactions.

## Gaps and proposed extension

1. Keep `ImportBatch` as the batch/history record and add source-file, statement-date/balance, creator, digest and workflow metadata instead of creating a parallel batch table.
2. Add a persistent `ImportDraftRow` table for extracted/mapped rows, comparison classifications, review decisions, proposed changes, actual transaction FK and read-back verification. This isolates extraction and review from actual bank transactions.
3. Preserve CSV/XLSX parsing and mapping; route their results into the same draft review data model. PDF upload should extract text with PyMuPDF, retain the original PDF, record page/confidence/source data and make a downloadable CSV. Extraction should skip summary rows, join wrapped narration and flag rows the parser cannot interpret. OCR is opt-in via a local executable configuration; unsupported scans stop for manual fallback.
4. Classify exact matches, new rows, possible duplicates, proposed modifications, existing accounting links, uncertain extraction and non-transaction rows. Comparison should prioritize stable references/UTRs, then account/date/direction/amount/value date/balance and normalized narration. A match is a proposal only; never silently change actual records.
5. Import only explicitly approved rows in an atomic transaction, then read each saved transaction back and compare every approved field. Keep exclusions/failures in the draft record. Preserve the existing unique hash and add stronger duplicate checks without assuming that date+amount alone is unique.
6. Validate statement-period/date edits, transaction bounds, balance sequence and opening + credits - debits = closing. Show discrepancies and require acknowledgement; never create a balancing entry.
7. Provide a separate migration-review stage. Existing transaction-to-journal linking should use `ReconciliationMatch` and its validation service. Do not automatically create Expense Book, payroll, receipt or journal rows. A destination must have a specific supported implementation and explicit row approval; uncertain or unsupported classes remain deferred for manual work.
8. Extend import history and page help; report extraction/OCR dependencies and limitations accurately.

## Data and migration risks

- Additive model fields/tables require a migration and should default safely for historical batches. Existing batch statuses (`pending`, `imported`), JSON raw rows, hashes, transactions and reconciliation matches must remain readable.
- Stored source statements may contain full account numbers and transaction descriptions; access must remain under the existing local-only policy, with account identifiers masked in UI/export and uploads size-limited/content-validated.
- A parser cannot reliably infer every bank's columns from arbitrary layouts. Ambiguous rows must remain editable/uncertain; no automatic insertion is allowed from low-confidence extraction.
- Changing an existing transaction can affect matching or reconciliation history. Proposed modifications need row-specific approval, pre-change values and post-write verification; modifying a matched record should be blocked until its matches are explicitly reviewed.
- Accounting migration can duplicate existing expenses, payments or journal entries. The safe initial path is classification/link review, with no automatic financial posting for unsupported destinations.

## Test strategy

Use isolated Django test databases and generated PDFs/CSV/XLSX fixtures. Cover text extraction, malformed/encrypted PDFs, date and balance checks, wrapped descriptions, duplicate references/overlapping periods, exact/new/ambiguous rows, approval/exclusion, atomic import, post-write verification, CSV/XLSX regression, deferred migration and reconciliation linking. Do not run destructive tests against `db.sqlite3` or live media.
