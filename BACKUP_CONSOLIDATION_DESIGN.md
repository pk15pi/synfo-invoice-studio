# Backup and offline data consolidation design

## Application audit

The application is a standalone Django 5.2 project configured to use SQLite at
`QB_DB_PATH` or `db.sqlite3`. There is no PostgreSQL backend configuration,
authentication system, or user model. Browser views that expose sensitive data
use `local_only`; this is a localhost boundary, not authentication or a control
for direct database/file access.

The installed business apps are `invoice_app`, `employee_management`,
`attendance`, `expense_management`, `reporting`, `accounting`,
`bank_management`, `compliance`, `document_management`, `audit`, and `backup`.
Their models use integer primary keys except for `accounting.Sequence`, whose
primary key is a string. There are no UUID business-record primary keys.
Existing full backups are handled separately by `backup.services`: a SQLite
online snapshot plus the configured private and media directories, checksums,
integrity verification, retention, and a destructive restore with a typed
confirmation and pre-restore backup.

The full model/FK graph is discoverable at runtime and exported as
`entity_counts` in each package. The important dependency chains include:

- Invoice configuration/templates -> clients -> projects -> invoices ->
  invoice lines, receipts, and credit notes.
- Chart of accounts and financial years -> periods and journal entries ->
  journal lines -> bank accounts, imports, transactions, reconciliation, and
  transfers.
- Employees/departments -> documents, bank accounts, salary structures and
  advances -> payroll periods and payroll records -> earnings, deductions,
  payments, recoveries, repayments, attendance and leave.
- Expense categories/vendors and related clients/employees/projects -> expense
  claims and payments/attachments -> GST claims and events.
- `document_management.Document` has a `ContentType` foreign key and generic
  `object_id`; it is also a read-only index of private employee/expense files
  and company documents.
- `audit.AuditEvent` is append-only. It records no person because the
  application has no login.

The implementation discovers models and FK/M2M dependencies from Django model
metadata, validates the complete graph, and inserts records only when required
FKs resolve. Nullable cycles are resolved after insertion; unresolved required
cycles abort the transaction. Generic document links are mapped separately.
Audit events and protected financial rows are imported as historical records,
not replayed as business operations.

## Package format and snapshot

Offline exports are version 1 ZIP packages with:

- `manifest.json`: format version, source instance UUID/name, app version,
  applied migration inventory, export UUID/time/type, completion status,
  entity/file counts and attachment byte count.
- `data.json`: records, field values, relation identities, M2M references,
  attachment checksums and per-record version hashes.
- `files/<sha256>`: referenced attachments, stored by content hash.
- `checksums.json`: SHA-256 for each data/report/attachment entry.
- `export_report.json`: completion and count summary.

Record identity is `(origin instance UUID, model label, original primary key)`.
The destination stores normalized `RecordOrigin` mappings and a source
snapshot; local integer primary keys are remapped on insert. Re-exporting
imported rows preserves their original identity. Names alone are never used as
identity. Potential duplicate hints (exact email, phone, employee code, invoice
number, or similar identifiers) are review-only.

Database records are read within a transaction; filesystem attachments are
read with size/hash checks and a before/after stat check where supported.
Exports are written to a temporary ZIP, validated by the same strict reader,
and atomically renamed only after completion. Missing/unreadable files fail the
export. Packages are integrity-protected, not encrypted; use OS/disk encryption
and controlled transfer/storage for employee, banking, payroll, and invoice
data.

## Import validation and merge

Import accepts ZIP only, with bounded upload/entry/file sizes, bounded
compression ratios, CRC and SHA-256 validation, strict ZIP entry names, no
symlinks/encryption, version/schema checks, field/primary-key validation,
foreign-key and M2M target validation, complete attachment reference checks,
and self-import/duplicate-export checks. Nothing is imported during upload.
The user gets a preview with counts, possible duplicates, schema/validation
errors, attachment counts, and changed-source conflict details before an
explicit confirmation.

Merge is additive and transactional. Existing master data is not updated or
deleted. A normalized origin map makes repeat imports idempotent and preserves
cross-instance references. Unique-key collisions that cannot be safely mapped
block the whole merge for manual resolution. Before merge, the application
creates and verifies a protected full pre-merge backup; a failed backup blocks
the operation. Financial records, posted journal
entries, payments, credit notes, and audit history are inserted only as
historical rows; model save hooks/business posting workflows are not replayed.
Database constraints remain enabled. A failed transaction rolls back records
and removes files written for that attempted merge.

When a previously imported source identity has changed, preview compares the
incoming version with the stored original snapshot and current destination,
including changed fields, hashes, and available modification timestamps. The
user must explicitly choose to keep the destination; the incoming package is
retained and can be downloaded for review. The current implementation does not
apply source-side edits or implement field-level conflict resolution. This is
deliberate: source replacement could silently alter protected financial or
historical data. No user-level modifying attribution is available in this
application.

## Instance identity and controls

`InstanceIdentity` has a persistent UUID, display name, creation time, and
last-export/import metadata. It is generated once on first use and is not
derived from hostname or network address. Set `QB_INSTANCE_NAME` to label an
installation. The UUID is stored in the application database, so a full backup
restore restores the same identity; a copied installation/database also copies
the identity and must not be operated as a separate instance until its identity
is deliberately re-established.

The existing full backup/restore flow remains separate and unchanged. Full
restore replaces a database and is not the cross-instance merge operation.
The merge UI inherits the existing localhost-only restriction. Because there is
no authentication, it cannot provide per-user authorization; do not expose the
application or its backup directories to a network.

## Current limitations and operational notes

- The project currently supports SQLite only; PostgreSQL operation and
  PostgreSQL backup/restore are not implemented or certified by this feature.
- Package schema compatibility is strict: applied migrations must match.
- Required non-nullable FK cycles and unresolved unique constraints fail closed.
- A conflict can currently be retained on the destination or left in the
  downloadable source package; it cannot yet be resolved by selecting and
  applying the source version.
- The package is checksummed, not cryptographically signed. SHA-256 detects
  corruption, not a malicious package author.
- Full backup ZIPs remain the recovery mechanism for restoring one installation.
  Keep a verified full backup before a merge.
