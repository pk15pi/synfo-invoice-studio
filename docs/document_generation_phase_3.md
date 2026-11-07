# Document Generation Phase 3: Standard Document Library

**Status:** Implemented; Phase 3 verification complete with one unrelated full-suite failure noted below.  
**Date:** 7 November 2026  
**Live database:** Phase 3 migrations applied after backup and isolated-copy verification.

## Existing implementation discovered

The project already had a substantial document-generation foundation. This phase extends that system rather than creating a second engine or repository.

| Feature | Existing implementation | Phase 3 work |
|---|---|---|
| Categories and document types | Configurable `DocumentCategory` and `DocumentType` records, existing seeds, code/numbering rules and admin forms | Added dedicated local library screens to create and edit categories and types; existing records remain configurable in storage |
| Template library | `DocumentTemplate` plus `DocumentTemplateVersion`, 38 seeded templates/version rows already present | Added template codes, document category, description, party type, scenario, format, default output and lifecycle status; added template/version management screens |
| Versioning | Existing unique per-template version number, approval states, protected generated-document reference and approved-content immutability | Added version metadata and a copy-forward service; new edits happen in a draft version, and approved history remains intact |
| Clause library | `DocumentClause` and `DocumentTemplateClause`; 24 seeded clauses were in review | Added mandatory/default inclusion properties, draft editing, approval workflow, new clause versions, and clause library UI |
| Template preview | Existing sample-value preview and safe template renderer | Kept preview restricted to staff and linked it from version history |
| Placeholder mapping | Existing `DocumentFieldDefinition`, source adapters for company/client/employee/vendor/asset, visibility conditions and allowlisted resolver | Preserved the safe resolver and added company name/brand/LLPIN/PAN aliases from the existing profile |
| Word/PDF generation | Existing Phase 2 `render_word`, `render_pdf`, preview token and output-format handling | Reused the current engine; optional approved clauses can be selected during generation |
| Generated-document storage | Existing `document_management.Document` index and private storage; no separate storage system | Reused the existing repository and added exact selected clause/version text to the generated-document snapshot |
| Client NDA/MCA uploads | Existing `invoice_app.ClientDocument` rows preserve older files when a new current upload is added | Left unchanged |
| Activity audit | Existing generated-document activity log and append-only audit app | Added UI audit events for library create/edit/version/workflow actions |

The live database had 8 document categories, 138 document types, 38 templates with 38 initial versions, 24 clauses, and no generated documents before Phase 3 migrations. The migrations add a Financial & Administrative category and 12 missing standard types, giving 9 categories and 150 types. Seeded template and clause text remains in review or inactive status; it was not auto-approved.

## Models and migrations

No new storage or employee/client master models were introduced. Migration files:

- `document_generation/migrations/0012_phase3_template_metadata.py` adds template metadata, lifecycle status, version effective/change metadata, and clause mandatory/default-inclusion flags. Existing template records receive a stable code, a category from their already assigned document type, and a status derived from their existing active/version state.
- `document_generation/migrations/0013_template_format_choice.py` constrains the supported template source format to the engine's structured-text format.
- `document_generation/migrations/0014_seed_financial_administrative_library.py` adds the missing Financial & Administrative category and 12 requested document types only where their configured type codes do not already exist. It links applicable existing category records without changing any financial transactions.

The migrations were first applied to an isolated copy of the backed-up database and then applied to the original database. They do not change invoices, expenses, accounting, payroll, employees, clients, or stored files. They do not remove or replace migrations.

## Library workflow

1. Open **Document & Agreement Management → Standard Document Library**.
2. Configure categories and document types. Field definitions remain available from the document-type screen and Django Admin.
3. Create a template and edit its first draft version. Add structured text, placeholder keys, output formats, signature configuration, and ordered clause mappings.
4. Submit the draft for review. Edits made while under review return it to Draft.
5. A staff user reviews the sample preview, approves the clauses and template, then activates the approved version.
6. Normal document generation lists only active templates with an approved active version. Mandatory clauses are included automatically; optional clauses can be selected and can be preselected by default.
7. To revise an approved template or clause, create a new version. The old version stays available and cannot be edited.

Category, type, template and clause records have no delete action in the library UI. Foreign-key protection also prevents deleting records referenced by generated documents. Template and clause changes create append-only audit events. Approval, activation, deactivation and retirement require a Django staff account; other library routes remain local-only, consistent with this app's current no-login architecture.

## Placeholder and generation safety

Placeholders are resolved only through configured fields and explicit source adapters. The renderer does not use `eval` or `exec`; unknown keys and malformed syntax are rejected. Company values are read from the existing singleton company document profile. `{{ company_name }}` maps to the legal entity name, and `{{ company_brand }}` maps to the operating brand; these do not create a second legal entity. Client, employee, vendor and asset data use their existing master records. Salary-certificate values remain explicit document fields for the requested period rather than assuming a current salary value is correct for every certificate.

The signed preview token carries selected clause IDs. The server validates them against the chosen approved template version. Generated records retain the protected template-version foreign key, rendered content, field/company/party snapshots, and the selected clause code, name, version and exact text. Generated Word/PDF files remain indexed by `document_management` and stored in the existing private storage.

## Company profile and legal-content boundary

The existing profile supplies `Quantbud LLP` as legal entity and `Synfowebs Technology` as operating brand. Blank LLPIN, GSTIN, PAN, addresses, signatories and other legal facts remain blank until entered from verified records. No additional legal facts, clauses, signatures or legal opinions were invented. Existing starter clauses/templates stay under review and cannot be used in normal generation until approved by a staff user.

## Backup, migration and verification

- Initial backup folder: `backups/document_phase3_preimplementation_20261107-100234/`; pre-catalogue migration backup: `backups/document_phase3_before_catalogue_seed_20261107-105012/`.
- Each contains an SQLite online-backup copy, a private-media copy, and `manifest.json` with table row counts and SHA-256 hashes.
- SQLite `integrity_check` returned `ok`; backup/live comparison matched all 128 tables and row counts. All 28 private files (7,714,950 bytes) matched their backup hashes.
- Applied `0012_phase3_template_metadata`, `0013_template_format_choice`, and `0014_seed_financial_administrative_library` to the live database only after isolated migration and test verification. The QA copy was not substituted for the live database.
- `manage.py check`: passed.
- `makemigrations --check --dry-run document_generation`: no model/migration drift.
- `manage.py test document_generation`: 53 tests passed.
- Full project suite: 619 tests ran; 618 passed and one existing test failed: `invoice_app.tests.IconActionTests.test_customer_row_actions_are_accessible_icons` expects a customer-row Delete icon, but the current customer list offers Deactivate and Edit. This test/template mismatch is unrelated to Phase 3 changes.
- Browser verification: eleven document-library/generation/profile routes checked at 1366px and 390px; all returned HTTP 200 and had no page-level horizontal overflow. Template detail and version edit screens were visually reviewed at desktop/mobile widths; the all-versions listing was also checked at both widths.
- No separate frontend build manifest is configured in the repository; Django template checks and browser rendering were used for the frontend pass.
- No forms were submitted in the live database. Phase 3 feature tests and UI creation checks used Django's test database; schema testing used the isolated QA copy.

## Known limitations and Phase 4

- This application has no general user-authentication system. Approval and lifecycle transitions are therefore limited to staff accounts through Django Admin authentication; organization-wide roles and approvals would require an authentication/authorization phase.
- Templates are authored in the supported structured-text source format. Uploaded DOCX template import and visual clause editor are not included.
- Generated documents remain drafts until the existing document lifecycle is used. A digital acknowledgement is not a legally certified electronic signature; external e-signature integration is not added here.
- Legal reviewers must approve the seeded template and clause content before use. No legal advice or content certification is implied.
- Phase 4 has not started. No actions on e-signature providers, automated legal review, client NDA/MCA upload replacement, or other document submodules were taken.
