# Manpower and Resource Billing

## What was added

The invoice application now supports client billing for general manpower and other resource assignments alongside its existing project invoice flow. Existing project invoices and manual service lines continue to use the existing invoice, GST, numbering, PDF, accounting, and payment paths.

Manpower is billed from client commercial terms. Employee salary and payroll amounts are not read or used by this feature. Assignments can be linked to an existing project or recorded as general client work, and a resource can be an employee or an external person/team description.

## Setup and use

1. Open **Invoice Management → Manpower deployments** (`/manpower/`) and add a deployment for a client. Set its resource/designation, assignment period, billing method, commercial rate, proration method, and optional contract or PO references. A project and employee are optional; when no employee is selected, enter an external resource description.
2. Use **Prepare invoice** on a deployment, or create a normal invoice and choose **General manpower**. Select the invoice month or enter a custom period, then select one or more deployment rows. The server recalculates the invoice amounts from the saved commercial terms when the invoice is saved.
3. Choose the resource display setting: show resource names, show designation/category, or consolidate the invoice into a service line. The PDF does not print internal deployment IDs. It never includes salary fields.
4. Review the draft and preview its PDF before issuing it. The existing invoice lifecycle, GST treatment, numbering, accounting, and payment workflow remains authoritative.

Recurring assignments only prefill a draft for review. The app does not issue invoices automatically. If a deployment already has a bill covering any part of the selected period, the app rejects a duplicate. An explicitly marked supplementary invoice is allowed for approved extra charges and remains visible as supplementary in the billing report.

## Billing calculations

Monthly deployments support actual calendar-day proration, a fixed 30-day divisor, configured working-day proration, full-month billing, or a manual amount override. Manual overrides require a reason. Configured working days use the Attendance app's weekly-off settings and exclude non-optional holidays. Daily and hourly deployments use an explicitly entered, approved billable quantity. Fixed contracts bill the saved contract amount unless a reviewed amount override is entered with a reason.

Rate changes are stored as effective-dated `ManpowerRateRevision` records. Monthly billing splits the amount across effective rate segments. A full-month method cannot be automatically split across a mid-month rate change. Daily/hourly changes inside a billing period require the user to split the period and enter the approved quantities for each rate. These checks prevent the app from guessing timesheet quantities or allocating charges.

## Data model and safeguards

- `ManpowerDeployment` stores assignment identity, dates, client-facing billing method/rate, optional project/employee, commercial references, and recurrence/status settings.
- `ManpowerRateRevision` preserves effective-dated rate history; changing a deployment rate does not rewrite old revisions.
- `Invoice` stores service type, resource display format, billing period, and supplementary status. These terms join the existing invoice snapshot/locking rules.
- `InvoiceLine` stores a nullable deployment link and resource/designation, method, quantity, rate, and period snapshots.
- `ManpowerBillingRecord` links billed deployment periods to invoices and blocks overlapping duplicate periods for non-supplementary bills.

The schema migration is `invoice_app/migrations/0015_businessprofile_manpower_resource_display_default_and_more.py`. A SQLite backup was made and integrity-checked before migration work. The migration was also applied to an isolated copy of that backup and checked before local application.

## Verification

The focused manpower tests cover monthly INR 285,000, daily INR 50,000, hourly INR 80,000, a fixed contract, proration methods, rate history, working-day/manual rules, duplicate and supplementary periods, optional projects, consolidated/name PDF displays, deployment/recurring-draft screens, and a general manpower invoice through existing GST calculation and ledger posting.

Run the focused checks with:

```powershell
.venv\Scripts\python.exe manage.py test invoice_app.tests_manpower
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

## Current boundaries

The project has no approved timesheet integration, so daily and hourly quantities are supplied and reviewed on the invoice. No overtime, weekend premium, or minimum commitment is inferred automatically; those commercial values are stored for reference but need explicit reviewed billing quantities or amounts. Customer-facing invoice tax uses the same existing general service SAC/GST defaults and customer GST treatment as other custom invoice lines.

Custom date ranges are supported for billing. The deployment report is grouped by invoice billing month, so invoices without a billing month may not appear in a monthly view. A rate revision that changes the billing method inside a month requires an explicitly split invoice row rather than automatic mixed-method calculation.

## Recruitment and candidate placement fees

The repository did not contain a candidate, requisition, or recruitment billing model, so this feature adds a small recruitment workflow inside the existing `invoice_app`. It does not add another invoice engine or candidate-facing document store.

### Models and client agreements

- `RecruitmentCandidate` is one reusable internal candidate record. Candidate contact details and identity documents are not collected here. An optional unique reference can distinguish two people with the same name; the form warns against creating a duplicate when the same name is already present.
- `RecruitmentRequisition` is an optional client-specific job requisition.
- `RecruitmentAgreement` stores the client-specific percentage or fixed placement fee, CTC basis, effective dates, min/max fee, payment terms, optional SAC/GST rate, trigger, and replacement/refund guarantee terms. Draft agreements without a billing trigger cannot be selected for a placement. After an agreement has an approved placement snapshot, the UI requires a new agreement version rather than changing the terms in place.
- `RecruitmentPlacement` links a candidate to the successful client placement, with optional requisition, finalized compensation details, client approval/offer references, milestone dates, guarantee dates, and an invoice link. One candidate may have separate placement records for different clients.

CTC components are stored separately but are never summed automatically. The selected agreement basis controls the calculation: finalized annual CTC, finalized monthly CTC multiplied by 12, or a separately entered client-approved basis amount. Expected annual CTC is a separate field and is never used to calculate a percentage fee. Component amounts, CTC values, approval references, and candidate names are hashed in audit snapshots rather than written in clear text to the append-only audit log.

### Fee calculation and eligibility

Percentage fee = approved fee basis × client-agreed percentage ÷ 100, rounded to two decimal places using `ROUND_HALF_UP`. Agreement minimum and maximum fees are applied when configured. A fixed agreement uses its saved fixed fee and does not calculate a percentage of CTC. A fee override requires a reason; the reason and before/after amounts are recorded in the append-only audit log. Once approved, the placement snapshot is locked.

No fee is eligible until its configured contractual trigger is recorded:

- Offer acceptance uses the offer acceptance date.
- Joining uses the joining date.
- Retention uses joining date plus the configured retention days.
- A custom trigger uses its explicitly recorded milestone date and reference.

An unset trigger, future milestone, draft agreement, unfinalized percentage-basis CTC, or missing CTC basis amount blocks fee approval. Approval only creates an approved billing record; an operator must still open the existing invoice form, review the client and resource display, preview the invoice, and save a draft.

### Invoice, privacy, GST and accounting

Recruitment invoices use the existing invoice number, tax treatment, PDF templates, payments, credit notes, and ledger posting. Their service type is `recruitment_placement`; their revenue uses the new `invoice.recruitment_revenue` mapping to account 4150, Recruitment and Placement Revenue. Credit notes use the same mapping. This keeps placement revenue distinguishable from existing service revenue while retaining the standard mapping configuration.

An approved placement can only be linked once. Its invoice line keeps the underlying placement foreign key even when the PDF hides the candidate name. The invoice-level display setting supports candidate name plus designation, designation only, or one consolidated professional recruitment line. CTC values, salary components, approval references, and personal contact/identity details are not printed. The agreement reference is snapshotted onto the invoice. Optional agreement SAC/GST settings are snapshotted with the fee; blank values use the existing general service tax defaults and client GST treatment.

The recruitment dashboard filters by client, candidate, designation, fee state, invoice/payment state, recruitment approval date, and joining date. It shows finalized CTC internally, fee approval, invoices and outstanding amounts, agreement rates, and guarantees approaching expiry. Guarantee conditions are recorded and expiry is highlighted; refunds, replacement obligations, credit notes, and invoice cancellations remain manual and follow the existing approval/accounting flow.

### Recruitment verification

`invoice_app.tests_recruitment` covers the 8%, 10%, and 12% examples, fixed INR 40,000 placement fee, expected-versus-finalized CTC, trigger gating, fee snapshot preservation after a rate change, audited overrides, one candidate across clients, duplicate invoice prevention, candidate privacy on the PDF, GST, separate ledger posting, payments, and the recruitment screens.

The new schema migration is `invoice_app/migrations/0016_recruitmentcandidate_recruitmentrequisition_and_more.py`; the invoice agreement-reference snapshot is added by `0017_invoice_recruitment_agreement_reference.py`. Accounting migration `accounting/migrations/0006_recruitment_revenue_account.py` adds the 4150 revenue account and mapping. The pre-recruitment SQLite backup was integrity-checked at `backups/pre_recruitment_20261106.sqlite3` before these schema changes. Migration execution on an isolated copy is required before applying them to the local database.

Current boundaries: candidate contact data and attachments remain in the secure HR/document storage processes; no timesheet integration is assumed. A recruitment invoice can include multiple approved placement rows, but a placement can only be billed once. Refunds/replacements are tracked as agreement terms and guarantee dates; the app does not issue refunds or credit notes automatically. GST continues to use the existing invoice tax service and account mappings; no new tax rate is presumed when an agreement leaves the optional rate blank.
