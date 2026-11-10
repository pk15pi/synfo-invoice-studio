"""Page-specific in-app help for the user-facing Django application.

Keep this registry separate from views and business rules. The frontend also
reads the visible form labels and action names so the dialog stays aligned with
the rendered page as forms evolve.
"""


PAGE_GUIDES = {}


def _add(keys, title, overview, purpose, steps, rules=(), related=(), notes=()):
    guide = {
        "title": title,
        "overview": overview,
        "purpose": purpose,
        "steps": list(steps),
        "rules": list(rules),
        "related": list(related),
        "notes": list(notes),
    }
    for key in keys.split():
        PAGE_GUIDES[key] = guide


# Home and invoice/customer/project management.
_add("home", "Application home", "Use the home page to open the main business modules.",
     "It provides the starting point for day-to-day work.",
     ["Choose the module that matches the work you need to do.", "Use the navigation menu to return to another module."],
     related=["Customer, invoice, employee, payroll, attendance, performance review, asset management, document and agreement management, and reporting pages are available from the main navigation and module cards."])
_add("dashboard", "Invoice management dashboard", "Review invoice activity and open the main billing workflows.",
     "This dashboard links customer, project, manpower and invoice work.",
     ["Review the dashboard summaries and recent records.", "Open the relevant list or use the page action to begin a workflow."],
     related=["Customer and project records supply the parties and work context used on invoices."])
_add("profile_edit", "Business profile", "Maintain the company identity and contact details used throughout the application.",
     "Profile information can appear on invoices, generated documents and reports.",
     ["Review the existing business details.", "Enter verified legal and contact information.", "Save the profile and review a generated document or invoice to confirm its presentation."],
     rules=["Use authoritative company records. The profile does not itself verify legal or tax identifiers."],
     related=["Invoice PDFs, document generation and report headers may use these details."])
_add("clients", "Customer list", "Find customer records and open customer-specific work.",
     "Customer master records reduce repeated entry and connect invoices and projects to the right client. Invoice email starts off for every customer until you enable it.",
     ["Search or filter the list.", "Open a customer to see its details and linked projects.", "Use Add customer to create a new record.", "Use the mail icon to enable or disable invoice email for a customer (confirm first)."],
     rules=["Use the customer status controls to deactivate/reactivate where available; deleting a customer may be restricted when records refer to it.", "Invoice email must be enabled before Email invoices can select or send to that customer."],
     related=["Customer records are used by invoices, projects, manpower deployments and recruitment agreements.", "Email invoices respects each customer’s invoice-email active flag."])
_add("client_add client_edit", "Add or edit customer", "Enter the customer identity, billing preferences, contact information and invoice email settings.",
     "This page stores the customer master used by invoices and client workflows, including whether invoice email is active, To/CC/BCC and email subject/body for invoice mailing.",
     ["Enter the customer name and applicable address/tax details.", "Choose the billing type and payment terms where shown.", "In Invoice email, turn Invoice email active on when this customer should receive mailed invoices, then set To/CC/BCC and choose a subject/body template or custom text (optional Save as template). Placeholders include {invoice_month} and {invoice_year} from the invoice date.", "Review required fields and save. The last subject/body choice becomes the default for new customers."],
     rules=["Use the correct legal/customer name and verified tax details.", "Billing type and due terms affect later invoice calculations or due dates."],
     related=["Invoices, projects, manpower deployments and recruitment placements can refer to this customer."])
_add("client_detail", "Customer details", "Review one customer and the work linked to that record.",
     "Use the detail page to check the customer record before creating related work.",
     ["Review the customer profile and related projects/documents.", "Use Edit to correct master details, or the available actions to open related records."],
     rules=["Changes to master details do not rewrite previously issued invoice snapshots."],
     related=["Customer list, invoices, projects and customer documents."])
_add("client_document", "Customer document", "View or retrieve a document associated with a customer.",
     "This page serves the selected customer file from the existing document workflow.",
     ["Check that the customer and document are the intended ones.", "Use the available view/download action to access the file."],
     rules=["Treat downloaded customer documents as confidential business records."],
     related=["Customer details and the shared document index."])
_add("projects_hub projects", "Project list", "Review projects and their customer relationships.",
     "Projects provide reusable work context for invoices and manpower deployments.",
     ["Filter or search the project list if controls are shown.", "Open a project or its customer to review linked work.", "Use Add project to create a project under the appropriate customer."],
     rules=["Project billing settings may affect invoice line descriptions or calculations; verify them before billing."],
     related=["Customer master, invoice creation and manpower deployment."])
_add("project_add project_edit", "Add or edit project", "Record a project under its customer and set the project-specific billing details.",
     "Projects help keep invoices and service work associated with the right customer engagement.",
     ["Confirm the customer context.", "Enter project name/code and applicable billing or date details.", "Save, then review the project in the customer’s project list."],
     rules=["A project belongs to its selected customer; check the customer before saving."],
     related=["Customer details, invoice creation and manpower deployments."])
_add("manpower_deployments", "Manpower deployments", "Review resource deployments and billing information by customer/project.",
     "This list tracks manpower supplied to client engagements for resource-based billing.",
     ["Review the customer, project, resource and deployment dates.", "Add or edit a deployment when an assignment changes.", "Use the listed deployment information when preparing a related invoice."],
     rules=["Only enter agreed rates and actual deployment periods; invoice amounts depend on the configured billing basis."],
     related=["Customer projects, recruitment placements and invoice creation."])
_add("manpower_deployment_add manpower_deployment_edit", "Add or edit manpower deployment", "Record a resource assignment and the agreed client billing basis.",
     "The deployment ties a supplied resource to a customer/project and period.",
     ["Select the customer and project.", "Enter resource/role, dates, quantity or billing basis and agreed rate as provided by the form.", "Review the calculated or displayed amount and save."],
     rules=["Use contract-approved rates and actual deployment dates. This record is not itself an invoice."],
     related=["Customer projects and invoice creation."])
_add("recruitment_dashboard", "Recruitment placements", "Manage client requisitions, candidates, agreements and placements.",
     "This workflow records recruitment activity and the client-approved commercial trigger for a placement fee.",
     ["Create or update the client requisition and candidate records.", "Record the client agreement before assessing a placement fee.", "Create a placement only with finalized, client-approved compensation and trigger details."],
     rules=["Expected or proposed compensation is not a finalized basis for a percentage fee.", "GST treatment follows the existing customer and invoice configuration."],
     related=["Customer master, recruitment agreements and invoice creation."])
_add("recruitment_candidate_add recruitment_candidate_edit", "Add or edit recruitment candidate", "Maintain the candidate details needed for recruitment tracking.",
     "Candidate records connect applications to requisitions and placements.",
     ["Enter only the recruitment information requested by the form.", "Link the candidate to the appropriate requisition where applicable.", "Save and review the candidate in the recruitment list."],
     rules=["Keep sensitive identity documents and personal details in the designated secure HR/document workflow."],
     related=["Recruitment dashboard, requisitions and placements."])
_add("recruitment_requisition_add recruitment_requisition_edit", "Add or edit client requisition", "Record a client’s hiring requirement and role details.",
     "Requisitions provide the client and role context for candidate placement.",
     ["Select the customer and enter the role/requirement details.", "Review status and dates, then save.", "Link candidates and agreements through the recruitment workflow."],
     related=["Recruitment candidates, client agreements and placements."])
_add("recruitment_agreement_add recruitment_agreement_edit", "Add or edit recruitment agreement", "Record the client-approved recruitment fee, basis, trigger and guarantee terms.",
     "The agreement controls how a placement fee is calculated and when it becomes billable.",
     ["Select the customer and enter the agreed fee basis and commercial terms.", "Check the fee floor/cap, CTC basis, payment terms, trigger and guarantee settings.", "Save and confirm the approved agreement is selected for the placement."],
     rules=["Use the signed/approved client terms. Do not treat a proposed compensation figure as finalized."],
     related=["Recruitment placements and invoice creation."])
_add("recruitment_placement_add recruitment_placement_edit", "Add or review candidate placement", "Record an actual candidate placement and calculate the fee from approved agreement terms.",
     "Placement records document the agreed fee trigger and supporting compensation values.",
     ["Select the customer, requisition, candidate and agreement.", "Enter finalized, client-approved compensation and trigger details.", "Review the fee calculation and save for the next billing step."],
     rules=["Expected or proposed compensation does not qualify as the percentage-fee basis.", "A placement record does not itself issue an invoice."],
     related=["Recruitment agreement and invoice creation."])
_add("performance_review:dashboard", "Recruiter performance dashboard", "Review candidate-verified recruiter performance and separate summary-only activity for a selected reporting period.",
     "This dashboard brings recruiter output, recruitment funnel, rejection patterns and ranking into one period-based view.",
     ["Choose a reporting period and apply the filter.", "Read candidate-verified figures separately from summary-only reported figures.", "Review the ranking and its score method alongside the underlying recruiter measures.", "Open Record Recruiter Activity to enter new JD, assignment, summary or candidate records."],
     rules=["Unique JDs count recruiter-JD assignments once; JD assignments are shown separately.", "Summary-only totals are never merged into candidate-verified outcomes.", "Recruiter ranking excludes pending outcomes from its quality conversion and requires at least one resolved candidate outcome."],
     related=["Recruiter activity, records, reports and performance settings."])
_add("performance_review:activity", "Record recruiter activity", "Create a job description, assign an employee recruiter, record summary activity or track a candidate outcome.",
     "Use the existing Employee master and candidate records to maintain recruiter activity without duplicating people.",
     ["Create or select a JD and save it.", "Assign one or more recruiters to the JD; the assignment is the recruiter-specific JD count.", "Use Summary-only activity only when individual candidates are not being recorded.", "For candidate-level tracking, select the existing candidate, JD assignment, submission date and current stage.", "When a candidate is rejected, choose the rejection stage; choose a configured reason if known.", "When status changes, update the candidate record with the effective date. The previous stage and outcome are retained in history."],
     rules=["A candidate may be recorded against another JD, but only once per recruiter assignment.", "A selected candidate cannot be selected a second time for the same JD.", "Summary-only figures stay separate from candidate-level verified results.", "Add candidate records from the existing Recruitment placements page if no candidate exists yet."],
     related=["Employee Management, Recruiter-wise Records and Performance Settings."])
_add("performance_review:application_edit", "Edit candidate outcome", "Correct an existing candidate submission and its current recruitment stage, outcome or milestone dates.",
     "Use this record to update the candidate once; stage and outcome transitions append to the history table.",
     ["Confirm the candidate, JD and recruiter shown in the heading.", "Update the stage, outcome and effective date.", "When rejected, choose a rejection stage; provide a reason when available.", "Enter milestone dates required by the current outcome, then save."],
     rules=["The same candidate cannot be added twice for one recruiter assignment.", "A candidate cannot be selected more than once for the same JD.", "The record's prior stage/outcome transitions remain in its history."],
     related=["Activity Entry and Recruiter-wise Records."])
_add("performance_review:activity_edit", "Edit summary-only activity", "Correct an aggregate recruiter activity record without adding candidate identities.",
     "Summary-only figures are retained separately from candidate-verified reporting.",
     ["Check the date, JD and recruiter before changing values.", "Save the corrected figures; the previous values are retained in the activity change history."],
     rules=["Do not also enter the same activity as candidate-level records unless you reduce or correct the aggregate entry to avoid duplication.", "Summary-only entries are excluded from candidate-level selection rates and ranking."],
     related=["Activity Entry, Records and Performance Reports."])
_add("performance_review:jd_edit", "Edit job description", "Correct the JD ID, client, title, opening date, status or remarks.",
     "Job descriptions supply the context used by one or more recruiter assignments.",
     ["Confirm the JD reference and linked client.", "Update the role details and save."],
     rules=["A JD is counted once per recruiter for unique-JD statistics; separate recruiter assignments remain visible."],
     related=["Activity Entry and Recruiter-wise Records."])
_add("performance_review:stage_edit", "Edit performance setting", "Rename or deactivate a configured recruitment stage, rejection stage or reason.",
     "Configured values appear on new candidate records while historical records retain their links.",
     ["Review the value type and ordering.", "Rename or deactivate the value and save."],
     rules=["Deactivating a value hides it from new activity forms but preserves historical references."],
     related=["Performance Settings and candidate outcome history."])
_add("performance_review:records", "Recruiter-wise records", "Review JDs, assignments, candidate outcomes and summary-only activity for a selected period.",
     "This page provides the auditable detail behind dashboard and report totals.",
     ["Apply the period filter.", "Review JD assignments and summary-only records in separate tables.", "Open a candidate to correct its current stage, outcome, dates or rejection details.", "Open candidate history to see previously recorded transitions."],
     rules=["Editing a candidate creates a history entry when stage or outcome details change.", "Historical recruiter records remain available if the employee later becomes inactive."],
     related=["Performance Dashboard, Activity Entry and Reports."])
_add("performance_review:reports", "Recruiter performance reports", "Compare recruiter and JD performance and export period-filtered monthly, quarterly, annual or custom reports.",
     "The report tables and exports use the same selected date range and keep summary-only figures separate.",
     ["Choose this month, previous month, a quarter, financial year, calendar year, selected month or custom dates.", "Review recruiter totals, monthly trend, JD assignments and rejection analysis.", "Use Export Excel for seven checkable worksheets or Export PDF for a printable summary.", "Review the top-performer score and the figures shown beside it."],
     rules=["Selection Rate = candidate-verified selections in the period divided by unique candidates submitted in the period, multiplied by 100; zero denominator returns zero.", "Summary-only activity is not combined with candidate-verified records.", "Exports respect the selected period and include employee identifiers and recruiter activity; store them appropriately."],
     related=["Performance Dashboard, Recruiter-wise Records and Performance Settings."])
_add("performance_review:settings", "Recruiter performance settings", "Choose which existing employees are recruiters and maintain recruitment stages, rejection reasons and ranking weights.",
     "Settings configure the names used in activity records and the transparent performance score.",
     ["Select active employees from the Employee master to make them available for new recruiter assignments.", "Former employees and their historical assignments remain visible.", "Add or deactivate configured stages, rejection stages or rejection reasons.", "Set score weights; all weights must total 100%. Configure the reporting year start month."],
     rules=["This application is local management-only and has no per-recruiter sign-in or record permissions.", "Existing employee records are reused; this page does not create a separate recruiter master."],
     related=["Employee Management and Performance Reports."])
_add("performance_review:daily_brief_records", "Daily Brief entry and records", "Enter a recruiter’s daily JD-wise resume activity and review saved reports.",
     "Daily Brief is a manual progress log that stays separate from operational JD, candidate and placement records.",
     ["Choose any active employee (type to filter the Recruiter list) and select the activity date; earlier dates are allowed.", "Enter the non-negative whole-number Selections Achieved count, then one JD Number and non-negative whole resume count per row. Use + to add rows and − to remove them; at least one row stays visible.", "JD and resume totals update from complete valid rows. Save creates a new recruiter/date report; a duplicate prompts you to review the existing report.", "Use View for the saved details and correction history, or Edit to correct the report. Corrections are retained in the record history.", "Use the period toolbar to filter by calendar year, week, trailing 15 days, calendar month, financial quarter/half/year or inclusive custom dates. Weekly dates use Monday–Sunday; 15 days end on the selected reference date.", "Search recruiter names/employee IDs or JD Numbers; filters combine. Change rows per page or sort a table column as needed."],
     rules=["Each recruiter can have one Daily Brief per activity date.", "A JD Number is stored as reported text and does not link to JD management.", "This log does not create candidate records or update candidate-level performance metrics."],
     related=["Daily Brief Analytics and the Employee master (all employed staff appear in the Recruiter combobox)."])
_add("performance_review:daily_brief_detail performance_review:daily_brief_edit", "View or edit Daily Brief", "Inspect the selection count and complete JD row details or correct an existing recruiter report.",
     "The detail page shows each reported JD and resume count; the edit page lets management correct its recruiter, date or rows.",
     ["Confirm the recruiter and activity date before editing.", "Correct the Selections Achieved count, JD Numbers or resume counts, add or remove rows, and save.", "Review the append-only before/after snapshots in Correction History."],
     rules=["A recruiter/date duplicate cannot be created by editing.", "The saved rows remain independent of operational JD, candidate, client and placement records."],
     related=["Daily Brief Entry & Records and Daily Brief Analytics."])
_add("performance_review:daily_brief_analytics", "Daily Brief Analytics", "Review period-filtered JD, resume and selection totals, recruiter comparisons, trends and averages.",
     "All analytics are calculated only from Daily Brief parent records and their JD detail rows.",
     ["Choose a calendar-year, weekly, 15-day, monthly, financial-quarter, financial-half, financial-year or custom inclusive date range.", "Quarter, half-year and financial-year choices use the configured financial-year start month; the page states the start month.", "Filter by recruiter, recruiter name/ID or JD Number. The same date and search filters apply to all KPI cards, charts and the comparison table. Selections Achieved sums the manually reported selection counts.", "Read average resumes per JD as total detail-row resumes divided by JD rows; average JDs per recruiter is JD rows divided by distinct active reporters.", "Use the daily trend charts for dates and the recruiter comparisons for JD/resume totals, selections achieved, JD share and average resumes per JD."],
     rules=["The same JD Number reported on different dates is counted each time.", "No candidate outcomes, operational JD data or placement data are inferred from resume counts."],
     related=["Daily Brief Entry & Records."])
_add("description_templates description_template_add description_template_edit", "Invoice description templates", "Manage reusable invoice descriptions for common work items.",
     "Templates speed up invoice entry while keeping line descriptions consistent.",
     ["Choose or create a description template.", "Edit the wording and any available classification fields.", "Save and select the template on an invoice when appropriate."],
     rules=["Review the inserted wording on each invoice; a template does not confirm that the service was delivered."],
     related=["Invoice creation and customer projects."])
_add("format_templates format_template_add format_template_edit format_template_preview", "Invoice format templates", "Configure or preview the presentation used for invoice documents.",
     "Format templates control invoice layout and branding, not the underlying accounting values.",
     ["Choose the format to create, edit or preview.", "Review visible layout options and sample content.", "Save changes and preview an invoice before using the format operationally."],
     rules=["A visual format does not change invoice totals, tax or ledger postings."],
     related=["Business profile, invoice creation and invoice PDFs."])
_add("invoice_list", "Invoice list", "Search issued and draft invoices and open an invoice record.",
     "The list is the starting point for reviewing invoice status, editing permitted drafts and downloading PDFs.",
     ["Use search and filters to locate an invoice.", "Open the invoice to review its lines, payments and status.", "Use row actions only after checking the invoice state."],
     rules=["Finalized or posted invoices may have restricted editing; corrections may require cancellation, credit note or reversal workflows."],
     related=["Customer records, receipts, accounting journal and reports."])
_add("new_invoice invoice_edit", "Create or edit invoice", "Prepare an invoice with a customer, billing details and line items.",
     "This form builds the invoice record and its totals for draft, issue or posting workflows.",
     ["Select the customer and any applicable project.", "Add each service/item with description, quantity, rate and applicable tax/discount.", "Review subtotal, tax, total and due terms.", "Save the draft or use the available issue/finalize action when ready."],
     rules=["Invoice number and ledger effects follow the current lifecycle actions.", "Verify customer, tax treatment, quantities, rates and dates before issuing; finalized records may require a formal correction workflow."],
     related=["Customer/project master, payment tracking, PDF output and accounting."])
_add("invoice_detail", "Invoice details", "Review one invoice, its current status, line items and payment history.",
     "Use this page to check the full invoice record before issuing, collecting payment or correcting it.",
     ["Confirm the customer, number, dates and total.", "Review line items and payment records.", "Choose an available action based on the current status."],
     rules=["Posting, finalizing, cancellation and credit-note actions can affect the accounting trail; review confirmation text before proceeding."],
     related=["Invoice list, receipts, accounting journal and customer details."])
_add("credit_note_add", "Add invoice credit note", "Record a credit against the selected invoice.",
     "A credit note documents an approved reduction/correction against an invoice.",
     ["Confirm the invoice and the reason for the credit.", "Enter the credit details requested by the form.", "Review the amount and tax handling before saving."],
     rules=["A credit note affects the invoice balance and may create accounting entries; use a source-supported amount."],
     related=["Invoice detail, customer account and accounting journal."])
_add("payment_add payment_edit", "Record or edit invoice payment", "Track a payment received against the selected invoice.",
     "Payment records reduce the receivable balance and support receipts and cash/bank reporting.",
     ["Confirm the invoice.", "Enter the payment date, amount, method and reference shown by the form.", "Save and review the updated invoice balance."],
     rules=["Use the actual received amount and bank/cash evidence. Editing/deleting a payment may change the invoice balance and accounting links."],
     related=["Invoice detail, receipts, bank and accounting."])
_add("payment_delete", "Delete invoice payment", "Review a request to remove a payment from an invoice.",
     "This confirmation protects the payment and invoice balance from accidental changes.",
     ["Check the invoice, payment date, amount and reference.", "Proceed only if the payment record is incorrect and the source supports removal.", "Review the invoice balance after the action."],
     rules=["Removing a payment changes the recorded amount paid and may affect linked accounting records; confirm before continuing."],
     related=["Invoice detail, receipts and accounting journal."])
_add("receipts", "Receipts", "Find payment records and access receipt-related information.",
     "This page supports review of collections recorded against invoices.",
     ["Filter or search for the relevant invoice/payment.", "Open the associated invoice to verify customer, amount and date.", "Use available receipt actions for the selected payment."],
     rules=["A receipt reflects a recorded payment; it is not proof of bank settlement unless matched to source evidence."],
     related=["Invoice payments, bank statement imports and accounting."])

# Employee and payroll pages.
_add("em:home", "HR dashboard", "Open employee, payroll and HR record workflows.",
     "The dashboard is the navigation point for personnel and payroll operations.",
     ["Choose the relevant HR or payroll page.", "Check employee master details before creating payroll or attendance records."],
     related=["Employees, attendance, payroll, documents and employee advances."])
_add("em:employees", "Employee list", "Find employee records and open their employment details.",
     "Employee master records are referenced by attendance, payroll, advances and HR documents.",
     ["Search or filter the employee list.", "Open an employee to review profile and employment information.", "Use Add employee to create a record."],
     rules=["Use verified identity and employment data. Access to this local-only area is not a replacement for role-based security."],
     related=["Attendance, payroll, salary structures, bank details and documents."])
_add("em:employee_new em:employee_edit", "Add or edit employee", "Maintain an employee’s identity, employment and contact information.",
     "The employee master is the source for HR and payroll workflows.",
     ["Enter verified personal and employment details.", "Select department and employment dates where applicable.", "Review required fields and save."],
     rules=["Use authoritative records; changes to employee master data may affect future payroll or reporting, not necessarily finalized past payrolls."],
     related=["Employee detail, attendance, payroll, salary and document records."])
_add("em:employee_detail", "Employee details", "Review one employee’s profile and linked HR records.",
     "Use the detail page to verify the master record before changing employment or payroll-related information.",
     ["Review employee identity and employment status.", "Open the relevant contact, bank, salary or document section.", "Use Edit for master changes and check related records afterward."],
     rules=["Bank and identity information is sensitive. Confirm account-holder details before payroll payments."],
     related=["Employee list, payroll, attendance, bank details and HR documents."])
_add("em:departments", "Departments", "Review and manage department master records.",
     "Departments classify employees for HR lists and reports.",
     ["Review the department list.", "Add or edit a department as needed.", "Deactivate or delete only after checking employee references."],
     rules=["A department referenced by employees may not be safe to delete."], related=["Employee records and reports."])
_add("em:department_new em:department_edit", "Add or edit department", "Create or update a department name and its available settings.",
     "Department records help organize employee data.",
     ["Enter the department details.", "Save and confirm it appears in the department list."],
     related=["Employee forms and HR reports."])
_add("em:personal_details em:documents em:bank_details em:salary_structures", "Employee records hub", "Review employee personal, document, bank or salary records for the selected section.",
     "This hub provides a cross-employee view of HR data. The Documents hub shows one row per employed employee with Aadhaar (A) and PAN (P) round pills, and a separate Misc button for every other current file (including Experience Certificate).",
     ["Use the section tabs/filters to locate the employee record.", "On Documents, open a pill to view the file in a new tab; each other document has its own Misc button.", "Open the employee-specific page to add or correct details.", "Verify sensitive values before saving."],
     rules=["Bank and identity details are sensitive and should be entered from authoritative sources only.", "Document pills never show full Aadhaar or PAN numbers."],
     related=["Employee master, payroll, attendance and document storage."])
_add("em:storage", "Employee document storage", "Review employee files stored in the existing private document repository.",
     "This page helps locate stored documents and check available storage information.",
     ["Use the filters or employee links to locate the intended record.", "Open only the document needed for the current task.", "Use the employee document workflow to add, replace or review file history."],
     rules=["Employee documents may contain sensitive identity or payroll information; download and share them carefully."],
     related=["Employee details, document history and the shared document index."])
_add("em:contact_new em:contact_edit", "Add or edit employee contact", "Record an employee contact detail associated with the selected employee.",
     "Contact records help maintain current HR contact information.",
     ["Confirm the employee shown in the page context.", "Enter the contact name, relationship and details requested.", "Save and verify it appears under the employee."],
     related=["Employee details and HR records."])
_add("em:document_new em:document_replace", "Upload or replace employee document", "Add a file to an employee’s document history or replace the current file with a new version.",
     "This workflow keeps employee documents associated with the correct employee and preserves document history.",
     ["Confirm the employee and document type.", "Select the correct file and enter its issue/expiry or reference details if requested.", "Review the selected file and save.", "Open the document history to confirm the new version."],
     rules=["Verify the employee and document type before upload. Replacing a file may change which version is current while retaining older versions."],
     related=["Employee details, document history and private document storage."])
_add("em:document_history", "Employee document history", "Review the versions and activity associated with one employee document.",
     "History helps confirm which file is current and what was replaced.",
     ["Confirm the employee and document name.", "Review upload dates and version information.", "Open the correct version for viewing or download."],
     rules=["Older versions may contain sensitive personal information and should remain access-controlled."],
     related=["Employee documents and document storage."])
_add("em:bank_new", "Add employee bank details", "Record a verified bank account for an employee.",
     "Bank details may be used for salary payment exports and payroll workflows.",
     ["Confirm the employee identity.", "Enter account-holder, bank and account data from an authoritative employee source.", "Check the account details before saving."],
     rules=["Do not infer account details from a name or prior payment. Bank information is sensitive; verify it securely."],
     related=["Employee profile, payroll payments and bank export."])
_add("em:salary_new em:salary_revise", "Add or revise employee salary structure", "Record the salary components and effective date for an employee.",
     "Salary structures supply recurring pay inputs for future payroll generation.",
     ["Confirm employee and effective date.", "Enter approved earnings and deduction components.", "Review totals and save; check the next draft payroll."],
     rules=["Use an authorized salary approval. A revision should preserve prior effective periods and does not itself change finalized payroll."],
     related=["Employee profile, payroll generation, payslips and statutory reports."])
_add("em:advance_new em:advance_detail em:advance_ledger em:advances", "Employee advances and loans", "Review advances, repayments and employee balances.",
     "This ledger tracks funds issued to employees and repayments over time.",
     ["Select the employee/advance and review principal, dates and current balance.", "Use the available action to record a supported advance or repayment.", "Recheck the employee ledger after saving."],
     rules=["Use source-supported amounts and dates. Changes can affect the employee balance and accounting records."],
     related=["Employee master, payroll deductions and accounting."])
_add("em:payroll", "Payroll dashboard", "Review payroll periods and open payroll preparation or payment workflows.",
     "The dashboard organizes payroll by pay period and status.",
     ["Choose the relevant financial/payroll period.", "Review draft and finalized records.", "Use generation, review, finalize or payment actions only after checking source inputs."],
     rules=["Finalized payroll should be corrected through supported revision/reversal actions; do not silently overwrite a paid period."],
     related=["Employee salary structures, attendance, payslips, salary payments and compliance."])
_add("em:payroll_generate", "Generate payroll", "Create payroll draft records for the selected payroll period using available employee and salary inputs.",
     "Draft generation prepares a period for review; it is not the same as finalizing or paying payroll.",
     ["Select the intended payroll period.", "Review included employees and source assumptions.", "Generate drafts, inspect each amount, and correct source data before finalization."],
     rules=["Check salary rates, attendance/proration and deductions. Generated draft values require review before finalization."],
     related=["Salary structures, attendance, payroll period and payslips."])
_add("em:payroll_detail em:payroll_edit", "Payroll record", "Review or edit a payroll entry for one employee and pay period.",
     "This page exposes the earnings, deductions and net pay components used for the record.",
     ["Confirm employee and period.", "Review each component and source note.", "Save only supported corrections; finalize after review."],
     rules=["Finalized payroll changes should use the revision workflow. Confirm impacts to payslips, payments and accounting."],
     related=["Payroll dashboard, salary structures, attendance, payslips and salary payments."])
_add("email_templates email_template_add email_template_edit invoice_email_templates invoice_email_template_add invoice_email_template_edit", "Email templates",
     "Manage reusable subject and body text for invoice and salary-slip emails (Shared).",
     "Categories control where a template appears: Common (customer dropdowns and salary defaults), Customer (invoice only), Salary (salary defaults only).",
     ["Filter by Type (Subject/Body) and Category.", "Add or edit Category, Type, Name and Text.", "Optional placeholders: {invoice_number}, {customer_name}, {customer_brand}, {invoice_date}, {invoice_month}, {invoice_year}, {grand_total}, {month}, {year}, {employee_name}, {employee_id}, {salary_month}, {salary_year}.", "Use as salary default on a Common or Salary template so Email salary slips uses it for every employee.", "Delete only when no customer references it and it is not a salary default."],
     rules=["Deleting a template that customers still reference or that is a salary default is blocked.", "Saving a template from Customer Add/Edit creates a Customer-category row.", "Salary slips always use the Shared salary default subject/body — not per employee."],
     related=["Email invoices, Email salary slips, and Add/Edit customer."])
_add("invoice_email", "Email invoices", "Filter finalized invoices by period and email each customer their invoice PDF.",
     "Each row is one invoice. Only customers with Invoice email active can be selected or sent. Send uses the customer's To/CC/BCC and subject/body template or custom text. Send is used the first time; after any attempt the button becomes Resend. Defaults to the previous calendar month.",
     ["Choose the period (defaults to previous month).",
      "Confirm the customer has Invoice email enabled and a To address.",
      "Select eligible rows (inactive-email customers stay disabled) and use Send selected or Bulk send selected, or Send on one row.",
      "View PDF still works when email is off for that customer."],
     rules=["Outgoing SMTP must be configured under Shared → Settings.", "Customers with invoice email off are skipped by send selected / bulk send and refused by the send endpoint.", "Already-sent selected rows are resent when you use Send selected."],
     related=["Invoice list, customer Invoice email settings and Shared → Settings outgoing email."])

_add("em:payslip_email", "Email salary slips", "Filter finalized payslips by period and email each employee their salary slip PDF.",
     "Each row is one employee and salary month. Send is used the first time; after any attempt the button becomes Resend. Selected rows can be mailed with the shared gap or in bulk.",
     ["Choose the period with the shared Yearly / Financial year / Custom date picker.", "Confirm the employee email address.", "Use Send/Resend for one slip, or check rows and use Send selected / Bulk send selected."],
     rules=["Only finalized current payslips are listed.", "Outgoing email password is set under Shared → Settings; it is never shown on screen.", "Failed sends stay Not sent.", "Send selected waits the shared gap; Bulk send selected does not. Already-sent selected rows are resent."],
     related=["Payslips, payroll settings and employee profile email fields."])
_add("em:payslips", "Payslips", "Filter finalized payslips with the shared Yearly / Financial year / Custom date picker, then download ZIPs and CA salary TDS Excel.",
     "The list is filtered by salary month (payroll period), not payment date. Export Q1-Q4 build one Excel row per employee from finalized current payslips; the file includes full PAN for CA salary TDS only.",
     ["Choose Yearly, Financial year, or Custom date and Apply.", "Download monthly or full-year payslip ZIPs for the derived financial year.", "Use Export Q1-Q4 for the CA salary TDS workbook (one row per employee)."],
     rules=["The page defaults to the previous calendar month when opened with no period filters.", "ZIP and Q1-Q4 exports use the selected financial year, or the FY that contains the filtered period end.", "Quarter exports include full PAN in Excel for the CA; keep that file private. PAN stays masked on screen."],
     related=["Payroll periods, Email salary slips and employee payroll records."])
_add("em:settings", "Payroll settings", "Review payroll rules, configuration, and outgoing Microsoft 365 email used for salary slips and invoices.",
     "Settings control configured payroll defaults and rules.",
     ["Review the active rules and effective dates.", "Create or edit a rule only from an approved payroll policy.", "Review a draft payroll after changing settings."],
     rules=["A settings change may affect future payroll generation. It does not by itself revise finalized payroll."],
     related=["Salary structures, payroll generation and statutory compliance."])
_add("em:rule_new em:rule_edit", "Add or edit payroll rule", "Configure a payroll rule and its effective period.",
     "Payroll rules supply configured calculation behavior to payroll processing.",
     ["Enter rule values from the approved policy.", "Check effective dates and scope.", "Save and test the rule on a draft payroll before finalizing a period."],
     rules=["Do not use unverified rates or retrospectively alter finalized payroll without the supported revision process."],
     related=["Payroll settings and payroll generation."])
_add("em:reports", "Payroll reporting", "Review payroll totals and period-level detail.",
     "Use payroll reporting to validate salary costs and payment status.",
     ["Set the period filters.", "Apply filters and review the displayed totals.", "Export only the intended period."],
     related=["Payroll dashboard, payslips, salary payments and statutory compliance."])
_add("em:advance_new", "Record employee advance", "Start an employee advance record with its amount and repayment information.",
     "An advance is tracked as a balance that may be recovered later.",
     ["Choose the employee.", "Enter the approved amount, date and repayment terms.", "Save and verify the resulting balance."],
     rules=["Record only the actual approved and disbursed amount; repayment deductions must follow the agreed terms."],
     related=["Employee ledger, payroll and accounting."])
_add("em:advance_detail", "Employee advance details", "Review one advance, its payments and remaining balance.",
     "This page is the record-level view for an employee advance.",
     ["Confirm the employee and advance.", "Review the balance and transaction history.", "Use available actions to record the next supported step."],
     related=["Advance ledger, payroll and accounting."])
_add("em:advance_ledger", "Employee advance ledger", "Review advances and repayments for one employee.",
     "The ledger explains how the employee’s outstanding advance balance was formed.",
     ["Check each transaction date, type and amount.", "Compare the balance with supporting records.", "Use the advance record for corrections."],
     related=["Employee detail, advances and payroll."])

# Attendance and leave.
_add("att:daily", "Daily attendance", "View or record attendance for a work date and employee group.",
     "Daily attendance feeds attendance review and may be used during payroll preparation.",
     ["Choose the attendance date and filters.", "Enter or review each employee’s status and hours.", "Save changes and verify the daily summary."],
     rules=["Use the actual attendance source. Corrections should include a reason where the page requests one."],
     related=["Attendance reports, leave balances and payroll generation."])
_add("att:month_grid", "Employee monthly attendance", "Review an employee’s attendance day by day for the selected month.",
     "The month grid helps check attendance patterns and missing entries.",
     ["Select the month/year if available.", "Review each daily status and totals.", "Use the corrections workflow for a source-supported change."],
     related=["Daily attendance, corrections and payroll."])
_add("att:summary", "Attendance summary", "Review attendance totals for the selected period or employee scope.",
     "The summary supports monthly attendance review before payroll.",
     ["Choose a period and any employee filters.", "Apply filters and compare totals.", "Investigate missing or unusual days in the daily register."],
     related=["Daily attendance, attendance reports and payroll."])
_add("att:import", "Import attendance", "Upload attendance data using the supported import format.",
     "Import reduces manual entry but still requires validation against source records.",
     ["Download the provided CSV template if needed.", "Prepare the file with the required columns and date format.", "Upload and review validation results before accepting the import."],
     rules=["Correct invalid rows in the source file and re-import as instructed. Avoid importing the same dates twice."],
     related=["Daily attendance, corrections and payroll."])
_add("att:holidays", "Holiday calendar", "Review holidays used by attendance and leave calculations.",
     "Holiday records identify non-working days in the configured calendar.",
     ["Review the holiday date and description.", "Add or edit only confirmed holidays.", "Check attendance/leave reports for the affected period."],
     related=["Attendance, leave balances and payroll."])
_add("att:holiday_new att:holiday_edit", "Add or edit holiday", "Create or update a holiday date and description.",
     "Holiday configuration can affect working-day and leave calculations.",
     ["Enter the confirmed date and holiday name.", "Save and verify it appears once in the calendar."],
     rules=["Check the applicable year and avoid duplicate holiday entries."], related=["Holiday calendar, attendance and leave."])
_add("att:weekly_off", "Weekly-off settings", "Review the configured weekly non-working days.",
     "Weekly-off settings help define expected working days for attendance workflows.",
     ["Review the selected day or schedule.", "Update only to match the approved work calendar.", "Check affected attendance periods after a change."],
     related=["Attendance, holidays and payroll."])
_add("att:leave_types", "Leave types", "Review the leave categories employees can request.",
     "Leave types determine the labels and policy settings available in applications.",
     ["Review each active leave type.", "Add or edit a type using the approved leave policy.", "Check balances and applications after changing configuration."],
     rules=["Changing a leave type may affect future requests; review existing balances before deactivation."],
     related=["Leave applications and leave balances."])
_add("att:leave_type_new att:leave_type_edit", "Add or edit leave type", "Configure a leave category and any policy fields shown.",
     "This form sets up the leave type used in requests and balances.",
     ["Enter the policy-approved name and settings.", "Check whether the type is paid, accruing or otherwise restricted where those fields exist.", "Save and review the leave-type list."],
     rules=["Use the current written leave policy; configuration does not notify employees automatically."],
     related=["Leave applications and balances."])
_add("att:applications", "Leave applications", "Review employee leave requests and their current decision status.",
     "This list supports review and tracking of leave requests.",
     ["Filter by status, employee or period.", "Open a request to review dates and leave type.", "Use the available approve/reject/cancel action according to your authority."],
     rules=["Review available balance, overlapping dates and supporting notes before deciding."],
     related=["Leave types, balances, attendance and payroll."])
_add("att:application_new", "Submit leave application", "Record an employee leave request for a date range and leave type.",
     "The application creates a request for review; it does not necessarily approve leave automatically.",
     ["Select the employee and leave type.", "Enter the requested dates and reason.", "Review the request and submit it for the decision workflow."],
     rules=["Check leave balance, date range and overlapping requests. Approval status is shown on the application list."],
     related=["Leave applications, balances and attendance."])
_add("att:balances", "Leave balances", "Review remaining leave by employee and leave type.",
     "Balances help validate leave requests and carry-forward amounts.",
     ["Select a period or employee filter.", "Review opening, earned, used and remaining values where shown.", "Use the edit/carry-forward action only with approved source figures."],
     rules=["Manual balance adjustments and carry-forward affect future leave availability; verify the source period and amount."],
     related=["Leave types, applications, carry-forward and payroll."])
_add("att:balance_edit att:carry_forward", "Edit or carry forward leave balance", "Update a balance or transfer eligible unused leave into the next period.",
     "These actions maintain continuity of leave balances across periods.",
     ["Confirm the employee, leave type and source/target period.", "Enter the approved adjustment or carry-forward amount.", "Review the resulting balance before saving."],
     rules=["Apply only the current leave policy and approved limits; check prior balance to avoid duplicate carry-forward."],
     related=["Leave balances, applications and leave policy."])
_add("att:reports att:corrections", "Attendance reports and corrections", "Review attendance registers or source-supported correction requests.",
     "Reports provide period-level attendance evidence; corrections explain changes to recorded attendance.",
     ["Set the period and employee filters.", "Review totals and exceptions.", "For corrections, compare the current value with evidence and record a clear reason."],
     rules=["Exports reflect the selected filters. Corrected attendance can affect payroll drafts; verify downstream periods."],
     related=["Daily attendance, imports, leave and payroll."])

# Expense management.
_add("exp:dashboard", "Expense dashboard", "Review company expenses and employee reimbursement summaries.",
     "The dashboard gives period-level visibility and links to expense work queues.",
     ["Set period/year/month filters and apply them.", "Review expense and claim totals.", "Open the relevant list or report to investigate a figure."],
     rules=["Dashboard totals follow the selected period and recorded statuses."],
     related=["All expenses, employee claims, vendors and expense reports."])
_add("exp:all", "All expenses", "Search and review company expenses and employee claims across categories and statuses.",
     "This is the central register for company-paid expense entries and employee claims.",
     ["Row 1: search, type (company/claim) and company payment status.", "Date period: yearly, financial year or a custom range — leave blank for all dates.", "More filters: claim workflow status, category, vendor, employee and department (comboboxes). Active filters appear as removable chips.", "Draft company expenses can be edited and finalized from their detail page; cancelled entries remain read-only history."],
     rules=["Draft expenses stay out of the accounting ledger and financial reports until finalized.", "Retain source evidence when finalizing or recording payment."],
     related=["Expense dashboard, vendors, accounting and financial reports."])
_add("exp:expense_new exp:expense_edit", "Add or edit company expense", "Record a company expense with its category, payee, amount and source details.",
     "Company expense entries feed expense reports and may link to accounting/bank records.",
     ["Enter the transaction date and category.", "Select or enter the vendor/payee and invoice/reference details.", "Enter the amount, tax and allocation details.", "Attach supporting evidence and save as a draft.", "Review the draft and finalize it before recording a payment."],
     rules=["Draft expenses stay out of the accounting ledger and financial reports until finalized.", "Use the actual transaction/document amount and verified tax treatment. Avoid duplicate entries for the same purchase."],
     related=["Expense categories, vendors, attachments, bank transactions and reports."])
_add("exp:expense_detail", "Expense details", "Review one company expense, its payment state and attachments.",
     "This page lets you verify the recorded expense before changing or paying it.",
     ["Compare vendor, date, amount, category and reference with the source.", "Edit the record while it is a draft.", "Finalize the draft to post it to the ledger, then record payment if needed."],
     rules=["Finalized expenses cannot be edited; correct the draft before finalizing.", "Payment and cancel actions affect the expense balance and downstream reports."],
     related=["All expenses, bank, accounting and expense reports."])
_add("exp:expense_payment", "Record expense payment", "Record payment against a company expense.",
     "Payment tracking shows whether and how the expense was settled.",
     ["Confirm the expense and outstanding amount.", "Enter the actual payment date, amount, method and reference.", "Save and verify the remaining balance."],
     rules=["Do not record a payment unless it is supported by bank/cash evidence. Avoid paying more than the verified outstanding amount."],
     related=["Expense details, bank transactions and accounting."])
_add("exp:claims", "Employee expense claims", "Review employee-submitted reimbursement claims.",
     "Claims track employee-paid business costs awaiting review or reimbursement.",
     ["Filter the list by employee, date or status.", "Open a claim to review amount, purpose and attachments.", "Use the review queue for approval and reimbursement steps."],
     rules=["A claim is not reimbursed until the payment action is recorded. Check receipts and business purpose."],
     related=["Claim review, employee records, reimbursement payments and accounting."])
_add("exp:claim_new exp:claim_edit", "Add Employee Claim", "Record an employee-paid business cost for review.",
     "The claim workflow separates employee submission from approval and reimbursement.",
     ["Enter the purchase date, category, amount and business purpose.", "Add vendor/reference details and attach a receipt.", "Save or submit the claim for review."],
     rules=["Use actual out-of-pocket costs and a supporting receipt. Do not submit company-paid purchases as employee claims."],
     related=["Employee claim list, review queue and reimbursement payment."])
_add("exp:review", "Review Employee Claim", "Check submitted claims and take the available approval or return action.",
     "The review queue supports claim decisions before reimbursement.",
     ["Open each claim and inspect its receipt and business purpose.", "Compare date, category and amount with policy.", "Approve, reject or return using the available action and record a clear reason if requested."],
     rules=["Approval alone is not payment; reimbursement must be recorded separately."],
     related=["Claims, reimbursement payments and accounting."])
_add("exp:claim_detail", "Employee claim details", "Review one claim’s requested amount, status and evidence.",
     "Use the detail page for review and reimbursement decisions.",
     ["Check employee, date, business purpose and category.", "Open the attached receipt.", "Use the action allowed by the current claim status."],
     rules=["Only approved claims should proceed to reimbursement; keep the evidence attached to the record."],
     related=["Review queue, claim list and employee reimbursement."])
_add("exp:claim_action exp:claim_reimburse", "Claim decision or reimbursement", "Apply a supported decision or record reimbursement for the selected employee claim.",
     "These actions move the claim through its review and settlement workflow.",
     ["Confirm the claim and its current status.", "Check approved amount and supporting evidence.", "Submit the decision/payment details and verify the updated status."],
     rules=["A decision and a reimbursement are separate steps. Reimbursement affects cash/bank and accounting records."],
     related=["Claim details, employee records, bank and accounting."])
_add("exp:vendors", "Expense vendors", "Review suppliers/payees used on expense entries.",
     "Vendor records help keep expense payee details consistent.",
     ["Search the vendor list.", "Open the vendor to review details and linked expenses.", "Add or edit vendor details only from verified source records."],
     rules=["Do not delete a vendor that is linked to financial records; deactivate it if that is the supported option."],
     related=["Company expenses, categories and accounting."])
_add("exp:vendor_new exp:vendor_edit exp:vendor_quick_add", "Add or edit expense vendor", "Enter verified supplier/payee details for future expense recording.",
     "Vendor master data standardizes payee information.",
     ["Enter the supplier’s legal/trading name and applicable contact/tax fields.", "Check for an existing vendor before saving a new one.", "Save and verify the vendor selection on the expense form."],
     rules=["Use the actual supplier identity. Do not create a vendor record for an employee/intermediary unless the workflow and evidence support it."],
     related=["Expense forms, vendor list and reports."])
_add("exp:vendor_detail", "Vendor details", "Review one vendor and its linked expense activity.",
     "The detail page supports payee verification before recording expenses.",
     ["Check vendor identity and contact/tax details.", "Review linked expense records.", "Edit verified details or deactivate an unused record if the action is available."],
     related=["Vendor list, company expenses and accounting."])
_add("exp:categories", "Expense categories", "Manage categories and subcategories used to classify expenses.",
     "Category choices drive expense summaries and financial reports.",
     ["Review the category and subcategory list.", "Add or edit classification names as needed.", "Check linked records before deactivating or deleting a category."],
     rules=["Changing category masters may affect future classifications; do not remove categories with recorded transactions."],
     related=["Company expenses, claims and financial reports."])
_add("exp:category_new exp:category_edit exp:subcategory_new exp:subcategory_edit", "Add or edit expense category", "Create or update a category/subcategory used to classify expenses.",
     "Categories group expenses for entry, review and reporting.",
     ["Enter the name and parent category where applicable.", "Save and check the option in an expense/claim form."],
     rules=["Use stable, understandable labels; existing transactions may retain their saved category reference."],
     related=["Expenses, claims and reports."])
_add("exp:reports exp:report", "Expense report", "Review expense totals and detail for the selected report and period.",
     "The report supports financial review by date, category, vendor or other displayed dimensions.",
     ["Choose the financial year and period filters.", "Apply filters and inspect the displayed detail and totals.", "Export only after confirming the report scope."],
     rules=["The report reflects saved records and the selected period; check whether cancelled/pending items are included."],
     related=["Expense register, claims, accounting and financial reports."])

# Accounting and bank management.
_add("acc:home", "Accounting dashboard", "Open chart of accounts, journals, ledgers, periods and mappings.",
     "This is the navigation center for general-ledger review and posting.",
     ["Choose the accounting workflow.", "Confirm the financial period before posting or reviewing entries."],
     rules=["Journal and period actions may be irreversible or require reversal entries."],
     related=["Chart of accounts, journal register, ledger and financial reports."])
_add("acc:accounts", "Chart of accounts", "Review the accounts used to classify financial activity.",
     "The chart defines the ledger accounts referenced by journals and mappings.",
     ["Search or review accounts by code and type.", "Open an account to edit only approved master information.", "Check mappings and existing entries before deactivating an account."],
     rules=["An account with posted journal lines cannot be safely removed; use supported inactive status where available."],
     related=["Journal entries, account ledger and system mappings."])
_add("acc:account_new acc:account_edit", "Add or edit ledger account", "Create or maintain an account code, name, type and related settings.",
     "Accounts classify journal debits and credits.",
     ["Enter a unique code and clear account name.", "Choose the correct account type and parent/settings where shown.", "Save and check mappings before posting entries to the account."],
     rules=["Do not change account type or code without considering historical reporting and linked entries."],
     related=["Chart of accounts, journals, mappings and reports."])
_add("acc:ledger_pick acc:ledger", "Account ledger", "Review account transactions and balances over a selected period.",
     "The ledger explains the balance using posted journal lines.",
     ["Select the account and date/financial period.", "Apply filters and compare opening, debit, credit and closing values.", "Open a journal reference to inspect source details."],
     rules=["Only posted entries are reflected according to the ledger’s current filters."],
     related=["Journal register, chart of accounts and financial statements."])
_add("acc:journal", "Journal register", "Search draft and posted journal entries.",
     "The register is the control point for reviewing accounting entries and their status.",
     ["Filter by period, status or reference.", "Open a journal to review its lines and source.", "Create, post, reverse or delete only according to current status and authorization."],
     rules=["Posted journals should normally be corrected with a reversal and replacement, not silent edits."],
     related=["Journal detail, account ledger and financial periods."])
_add("acc:journal_new", "Create journal entry", "Enter a balanced manual journal for a supported accounting adjustment.",
     "Manual journals record transactions not posted through another workflow.",
     ["Select the accounting date and period.", "Add account lines with descriptions and debit/credit amounts.", "Confirm total debits equal total credits and retain source support.", "Save as draft or post only when reviewed."],
     rules=["The journal must balance. Posting changes the general ledger and may be restricted by a closed period."],
     related=["Chart of accounts, journal register and account ledgers."])
_add("acc:journal_detail", "Journal entry details", "Review a journal header, lines, posting state and source information.",
     "The detail page is used to validate an entry before or after posting.",
     ["Check date, reference and period.", "Confirm every debit/credit line and account.", "Use post, reverse or delete only if offered and appropriate for the status."],
     rules=["Posting is a financial action. Reversal records an offsetting entry; verify the replacement entry separately."],
     related=["Journal register, ledger and financial period."])
_add("acc:periods", "Financial periods", "Review period dates and whether each period is open or closed for posting.",
     "Period controls help prevent entries in a locked reporting period.",
     ["Confirm the period start/end dates.", "Review its status before entering or posting journals.", "Use close/reopen actions only under approved close procedures."],
     rules=["Closing a period may prevent further posting; reopening can affect controlled financial statements."],
     related=["Journal entries, ledgers and financial reports."])
_add("acc:mappings", "Accounting mappings", "Review mappings that connect application events to ledger accounts.",
     "Mappings determine which accounts are used by supported automated posting workflows.",
     ["Review each event and its mapped account.", "Change mappings only from an approved accounting policy.", "Test the effect on a draft/source transaction before posting."],
     rules=["A mapping change may alter future postings, not automatically correct prior journal entries."],
     related=["Chart of accounts, invoices, expenses, payroll and journals."])
_add("bank:accounts", "Bank and cash accounts", "Review bank/cash accounts and open their transactions, imports and reconciliation.",
     "Accounts define the statement source and cash/bank ledger used by bank workflows.",
     ["Select an account to review details or transactions.", "Use Import statement for the corresponding bank account.", "Confirm opening balance and account identity before reconciliation."],
     rules=["Do not create duplicate accounts for the same real bank account; account balances should be source-supported."],
     related=["Statement imports, bank book, transfers and accounting ledger."])
_add("bank:account_new bank:account_edit", "Add or edit bank account", "Maintain a bank/cash account label and its ledger details.",
     "The account record is the destination for imported statement lines and transfers.",
     ["Enter the account name, type and linked ledger account where shown.", "Check the opening balance/date against source records.", "Save and verify the account in the bank list."],
     rules=["Use a unique real-world account and verified opening balance. Changing the ledger link can alter reconciliation context."],
     related=["Bank imports, transactions, reconciliation and accounting."])
_add("bank:account_detail", "Bank account details", "Review one bank/cash account and its current settings and activity.",
     "Use this page to confirm the account before importing or reconciling statements.",
     ["Check account identity, currency and ledger link.", "Open transactions or reconciliation for the desired period.", "Edit settings only when supported by verified records."],
     related=["Accounts, statement imports, bank book and ledger."])
_add("bank:transactions", "Bank statement lines", "Review transactions associated with one bank account.",
     "The transaction list supports import review and reconciliation.",
     ["Filter by date, status or reference.", "Open a line to inspect source data.", "Match, ignore or unmatch only after confirming the underlying transaction."],
     rules=["Importing a line does not necessarily create a general-ledger entry; check status and account mapping."],
     related=["Statement import batches, reconciliation and accounting."])
_add("bank:import_upload bank:imports bank:import_batch bank:import_review bank:import_migration", "Bank statement import", "Upload PDF, CSV or Excel statements into a saved draft, review each extracted row, and approve bank-register changes explicitly.",
       "Extraction and bank import are separate stages. Draft rows do not change the bank register. Imported rows are read back and verified. Accounting classification is a separate follow-up and does not create accounting records in this workflow.",
       ["Choose an account and upload a PDF, CSV/TXT or XLSX statement (maximum 20 MB). PDF text is extracted locally; scanned PDFs require local Tesseract OCR configured on this machine.", "For CSV/Excel, map the date, narration, reference, debit/credit and balance columns, preview, then create a review draft.", "Review the detected or inferred statement dates, correct the From/To fields, and acknowledge any out-of-range transaction dates.", "Inspect each row's comparison class and reason. Edit uncertain rows; exact matches, possible duplicates and proposed modifications are shown separately.", "Open a statement from Import history to select it, then use Regenerate Draft to reprocess that exact retained upload. A new numbered draft version appears with a comparison summary and links to earlier versions.", "Use the horizontal table scroll region to inspect all review columns. Expand Narration / reference and Read justification to read full text; expand Edit / review to correct a draft row.", "Approve only the rows you want to import, then select Import approved rows and verify. The application reads each saved bank transaction back and records the result.", "Use Cancel Draft for an unimported version. Confirm the batch and period, enter a reason, then confirm. The version is marked cancelled; files, extracted rows, decisions and history stay available.", "Download the generated CSV or reopen the retained original file and batch from Import history.", "After import, classify verified rows separately or defer. Use the bank reconciliation screen to link existing journal lines."],
       rules=["PDF extraction is heuristic and ambiguous lines require manual review; this workflow never invents values.", "An inferred period is based on transaction dates when no explicit statement range can be read; confirm it manually.", "Exact rows are not inserted again. Regeneration compares all rows against the current bank transaction register; possible duplicates and modifications need review before approval.", "Regeneration creates a separate version and does not overwrite an approved/imported version. Earlier row decisions and imported bank transaction links remain in their original version.", "Cancellation is available before import starts and only when no bank rows have been imported or verified. Importing, imported, completed, failed-after-partial-import, and already-cancelled versions cannot be cancelled as ordinary drafts.", "Cancellation preserves the source upload, generated CSV, extracted rows and decisions. It never deletes or reverses bank or accounting records. Use the bank transaction correction/reversal workflow if a batch has imported rows.", "Importing a statement never creates an expense, payment, receipt, payroll entry or journal. The accounting review records a follow-up classification only; complete postings in their existing modules after checking for duplicates.", "An unreadable, password-protected, oversized or unsupported scanned PDF shows an error; use an unlocked PDF or CSV/Excel fallback."],
       related=["Bank accounts, transaction review, reconciliation, Expense Book and accounting journal."])
_add("bank:manual_entry", "Manual bank entry", "Add a supported bank or cash transaction directly to an account.",
     "Manual entries are for transactions that should be recorded but are not available through a statement import.",
     ["Confirm the bank/cash account.", "Enter the transaction date, description, direction and amount.", "Provide a reference and linked account/category where shown.", "Save and verify it appears once in the transaction register."],
     rules=["Use bank/cash evidence. Avoid duplicating an imported statement line."], related=["Bank register, reconciliation and accounting."])
_add("bank:reconciliation", "Bank reconciliation", "Compare statement lines with recorded transactions for a selected account and period.",
     "Reconciliation confirms which bank movements are represented in the books.",
     ["Set the statement period and balance.", "Match each line to the correct recorded transaction.", "Investigate unmatched or duplicate items.", "Finish only when the balance difference and exceptions are understood."],
     rules=["Matched status is not proof of correct account classification. Keep unresolved items visible until sourced."],
     related=["Bank imports, bank book, journal register and account ledger."])
_add("bank:transfers", "Bank transfers", "Record or review movements between bank and cash accounts.",
     "Transfers connect two cash/bank accounts without treating the movement as revenue or expense.",
     ["Select the source and destination accounts.", "Enter the transfer date, amount and reference.", "Review both sides and save once.", "Verify the transfer appears in each relevant account."],
     rules=["Use the same amount/date/reference supported by the transfer evidence; do not duplicate statement-imported activity."],
     related=["Bank accounts, statement lines and ledger."])
_add("bank:book", "Bank or cash book", "Review period activity and balances for the selected account type.",
     "The book provides a cash/bank activity view for financial review.",
     ["Choose the account and date/period filters.", "Apply filters and compare opening, receipts, payments and closing balance.", "Open a line to trace it to source and ledger details."],
     rules=["The view reflects current import/reconciliation and ledger statuses; it is not a substitute for bank statement reconciliation."],
     related=["Bank accounts, statement import, reconciliation and accounting ledger."])
_add("bank:legacy_preview", "Legacy bank text preview", "Inspect legacy bank statement text in the preview workflow.",
     "This is a review aid for older imported data formats.",
     ["Select the available legacy content or account.", "Compare previewed rows with the original source.", "Continue to the supported import workflow only if the values align."],
     rules=["A preview is not a completed import or reconciliation."], related=["Bank statement imports and transactions."])
_add("bank:txn_detail", "Bank transaction details", "Review one imported or recorded bank transaction.",
     "This page helps verify transaction source, match state and linked accounting information.",
     ["Check date, amount, description and reference against the bank statement.", "Review match/ignore status.", "Use unmatch or ignore controls only when the current classification is incorrect."],
     rules=["Changing a match affects reconciliation status; it does not necessarily create or remove a general-ledger entry."],
     related=["Statement batches, reconciliation and journal register."])

# Compliance, document management and reporting.
_add("compliance:home", "Compliance dashboard", "Open statutory registers, payment tracking and due-date tools.",
     "The dashboard organizes current compliance review pages.",
     ["Choose the statutory area.", "Check the period and source totals before recording a payment or filing status."],
     rules=["Displayed estimates/registers are not a substitute for filed returns or government challans."],
     related=["GST, TDS, payroll liabilities, payments and due dates."])
_add("compliance:rules", "Statutory rules", "Review configured statutory rates and applicability settings.",
     "Rules supply configured assumptions to compliance summaries.",
     ["Review the rule name, rate, scope and effective dates.", "Add/edit only from an approved current source.", "Check reports for the applicable period after a change."],
     rules=["Statutory rules change over time; verify current official guidance before using configured values."],
     related=["GST, TDS, payroll compliance and statutory payments."])
_add("compliance:rule_new compliance:rule_edit", "Add or edit statutory rule", "Configure a statutory rule and its effective date/scope.",
     "This page stores a rule used in compliance views.",
     ["Enter the rule from verified statutory guidance.", "Set its effective period and applicability.", "Save and validate a report for the period."],
     rules=["Use an authoritative current source and preserve historical effective dates."],
     related=["Statutory rules, compliance checks and reports."])
_add("compliance:check", "Compliance check", "Review compliance-related indicators and source records for the selected period.",
     "The check page surfaces items for review; it does not file a return automatically.",
     ["Choose the relevant period.", "Review each issue and its source.", "Resolve it in the originating module or follow the statutory filing process."],
     rules=["Confirm figures against official returns, challans and source books before filing."],
     related=["GST registers, TDS, payroll liabilities and payments."])
_add("compliance:gst_output compliance:gst_input", "GST register", "Review output or input GST records for a selected period.",
     "The register supports tax review using invoice/expense source data.",
     ["Choose the reporting period and filters.", "Review taxable amounts, tax components and source references.", "Investigate missing or excluded records in the invoice/expense module."],
     rules=["Confirm classifications and amounts against source invoices and filed returns. A register view is not a filing confirmation."],
     related=["Invoices, expenses, GST compliance and financial reports."])
_add("compliance:tds", "TDS register", "Review TDS-related amounts and source transactions for the selected period.",
     "This page supports TDS review and payment tracking.",
     ["Select the period.", "Review payee, base amount and TDS values shown.", "Compare to the applicable rate and official challan/return."],
     rules=["TDS applicability/rates depend on current rules and facts; verify before filing or payment."],
     related=["Expense/vendor records, statutory rules and payments."])
_add("compliance:payroll", "Payroll statutory liabilities", "Review PF, ESI and professional-tax liabilities from payroll records.",
     "This report supports statutory payroll review and remittance planning.",
     ["Choose the payroll period.", "Review employee and employer components where shown.", "Compare values with payroll records and official challans."],
     rules=["Do not treat a calculated liability as a submitted return or completed remittance."],
     related=["Payroll, employee data, statutory payments and compliance rules."])
_add("compliance:payments", "Statutory payments", "Review or record tax and statutory payment transactions.",
     "This register tracks compliance-related payment evidence and status.",
     ["Filter by period, authority or status.", "Record payment only with the actual challan/reference and date.", "Review cancellation or correction actions before confirming."],
     rules=["A recorded payment should correspond to an actual remittance. Cancellation does not refund money; it changes the system record."],
     related=["GST/TDS/payroll liabilities, bank transactions and accounting."])
_add("compliance:due_dates compliance:due_date_edit", "Compliance due dates", "Review statutory filing/payment deadlines and their status.",
     "The due-date list supports calendar tracking for compliance tasks.",
     ["Filter by period and compliance type.", "Review each due date and current status.", "Edit a deadline only when supported by an official schedule."],
     rules=["Due dates can change; confirm dates from current official notifications."],
     related=["Compliance checks, statutory payments and filing registers."])
_add("docs:list", "Document index", "Search and review files stored in the shared document repository.",
     "The index organizes existing document records and their associated metadata.",
     ["Search or filter by category, owner or status.", "Open a document record to view its metadata.", "Use download only for the intended file and retain it securely."],
     rules=["The index points to stored files; verify the association before downloading or editing metadata."],
     related=["Employee/customer/vendor records and document generation."])
_add("docs:upload", "Upload document", "Add a file and its metadata to the shared document index.",
     "Uploads make supporting files searchable and link them to the correct owner/category.",
     ["Choose a file and the correct category/owner.", "Enter the requested dates or reference metadata.", "Upload and verify the document in the index."],
     rules=["Check file and owner before upload. Uploaded sensitive records should be shared only with authorized users."],
     related=["Document index, employee/customer records and generated documents."])
_add("docs:check", "Document integrity check", "Review document file-presence or integrity results.",
     "This page helps identify missing or inconsistent stored files.",
     ["Run or review the available check.", "Open flagged records and compare file metadata with storage.", "Resolve issues through the document workflow and rerun verification."],
     rules=["A successful metadata check does not establish that the document content is legally valid."],
     related=["Document index and storage."])
_add("docs:edit", "Edit document metadata", "Update metadata for a document already in the shared index.",
     "Metadata helps users find and associate the stored file.",
     ["Confirm the document and owner.", "Edit only the metadata fields that are incorrect.", "Save and verify the list entry."],
     rules=["Metadata edits do not replace or rewrite the original file content."], related=["Document index and storage."])

_add("document_generation:dashboard", "Document generation dashboard", "Open standard documents, agreements, the template library and generated records.",
     "This dashboard is the navigation point for document workflows.",
     ["Choose the library or generation task.", "Check template approval status before generating a document."],
     rules=["Seeded templates may remain under review until approved by an authorized staff user."],
     related=["Template library, clause library, generated documents and document index."])
_add("document_generation:catalogue", "Document catalogue", "Browse document types and the categories available to the application.",
     "The catalogue helps users select the correct document purpose.",
     ["Filter or browse the categories and types.", "Open the appropriate library or generator for a document type."],
     rules=["A catalogue item describes a supported document type; it does not imply that an approved template is available."],
     related=["Document categories, templates and generation."])
_add("document_generation:settings", "Document settings", "Review document-company profile and generation settings.",
     "Settings provide the verified company values used by document placeholders.",
     ["Review current company identity values.", "Enter only verified legal and contact details.", "Save and preview a document to confirm placeholders."],
     rules=["Blank values should remain blank until verified; do not infer legal identifiers or signatories."],
     related=["Business profile, templates and document generation."])
_add("document_generation:library", "Standard document library", "Open document categories, types, templates, versions and clauses.",
     "The library is the controlled source for standard document content.",
     ["Choose the relevant library section.", "Edit draft content or create a new version for a change.", "Submit for review; approval and activation require an authorized staff account."],
     rules=["Approved template/clause content is immutable; create a new version to revise it."],
     related=["Document catalogue, generation and document storage."])
_add("document_generation:categories", "Document category library", "Create or maintain document categories used to organize document types.",
     "Categories group related agreements and forms.",
     ["Review existing categories.", "Add or edit a category using a clear business label.", "Check linked document types before changing a category."],
     rules=["Categories referenced by types or templates should not be removed casually."], related=["Document types and standard library."])
_add("document_generation:types", "Document type library", "Manage the standard types that identify document purpose and numbering rules.",
     "Document types classify generated documents and determine available workflows.",
     ["Review the type code and category.", "Add or edit only a distinct, approved type.", "Check numbering/field settings before generating documents."],
     rules=["Type codes must be unique. Avoid duplicating a type that already represents the same workflow."],
     related=["Categories, template library and document generation."])
_add("document_generation:templates", "Document templates", "Browse templates by document type and lifecycle state.",
     "Only active templates with approved active versions are available to normal generation.",
     ["Filter by status or type.", "Open a template to review its version history.", "Draft changes as a new version, then submit and follow the approval workflow."],
     rules=["Do not use review/inactive legal content as approved production wording."],
     related=["Template versions, clause library and document generation."])
_add("document_generation:template_new document_generation:template_edit document_generation:template_duplicate", "Create or edit document template", "Define a template’s purpose, type, format and draft content.",
     "Templates control the reusable structure and placeholders used to generate documents.",
     ["Choose category/type and applicable party/scenario.", "Enter a clear template code, description and output format.", "Edit the draft version content and attach configured clauses.", "Preview, then submit it for review."],
     rules=["Use only configured placeholder names. Review documents for sensitive or legally significant terms before approval."],
     related=["Document types, template versions, clauses and generation."])
_add("document_generation:template_detail", "Document template details", "Review a template’s configuration, current status and versions.",
     "This page is the control point for reviewing template content and lifecycle actions.",
     ["Check type, applicability and active version.", "Review draft/approved version history.", "Use duplicate or new version for changes rather than overwriting approved content."],
     rules=["Approval and activation require a staff account; approved versions remain protected."],
     related=["Template list, version library, clause library and generated documents."])
_add("document_generation:template_versions document_generation:template_version_edit document_generation:template_new_version document_generation:template_version_action", "Template versions and review", "Review or revise a template version while preserving approved history.",
     "Versioning records what wording was approved and used for generated documents.",
     ["Review version number, status, effective dates and change description.", "Edit only a draft version.", "Submit for review and ask an authorized staff member to preview, approve and activate."],
     rules=["Approved content cannot be edited in place. Create a new version for revisions."],
     related=["Template details, approval queue and document generation."])
_add("document_generation:clauses document_generation:clause_new document_generation:clause_edit document_generation:clause_new_version document_generation:clause_action", "Clause library", "Manage reusable clauses and their approved versions for document templates.",
     "Clauses provide controlled text blocks that can be included in generated documents.",
     ["Review clause text, version and mandatory/default flags.", "Edit a draft or create a new version.", "Submit for review; attach only approved clauses to production templates."],
     rules=["Mandatory clauses are included automatically. Optional clauses may be selected during generation."],
     related=["Template versions, document generation and generated document snapshots."])
_add("document_generation:generate", "Generate document", "Create a document from an active approved template and the selected party’s existing record data.",
     "Generation combines a template, resolved fields and approved clause text into a stored output.",
     ["Select an active template and output format.", "Choose the party/record and complete any explicit fields requested.", "Select optional clauses if offered and review the preview.", "Generate, then open or download the resulting document."],
     rules=["Only configured placeholders are supported; unknown or malformed placeholders are rejected.", "Generated output is a draft document unless the existing lifecycle is advanced separately."],
     related=["Template library, company profile and shared document storage."])
_add("document_generation:template_preview", "Template preview", "Preview a template version with sample values before approval.",
     "Preview helps reviewers check rendering and placeholder structure.",
     ["Review the version and sample output.", "Check placeholder substitutions, clause order and page layout.", "Return to the version record to request changes or approve when authorized."],
     rules=["Sample values are illustrative and do not confirm a real party’s data."],
     related=["Template versions and approval workflow."])
_add("document_generation:placeholder", "Document module page", "Review the document section shown on this page and its currently available actions.",
     "Some document sections are navigation or placeholder screens; use only features visibly available on this page.",
     ["Read the page status and available actions.", "Use the linked standard library or document index for implemented workflows."],
     rules=["A planned or placeholder section does not provide functionality until the page explicitly offers it."],
     related=["Document catalogue, generation and shared document index."])

_add("reporting:dashboard", "Executive dashboard", "Review period-based financial KPIs, payroll, expense, revenue and cash trends.",
     "KPI cards compare the selected period with the previous equivalent period. Click a card or chart heading to open its detailed report.",
     ["Select the period type and any year, month, financial-year part or custom dates, then Apply.",
      "Compare revenue with receipts: revenue uses invoice-date taxable value excluding GST; receipts use actual receipt dates.",
      "Review salary components by payroll month. Deductions are already included in gross salary; employer contributions add to employee cost; net salary is not a second expense.",
      "Review recorded cash movement by payment date; the dashboard does not infer unrecorded cash sources.",
      "Download the selected-period workbook for multiple existing report sheets, or the selected-period management P&L PDF."],
     rules=["Dashboard P&L is a management mixed accrual view, not a statutory financial statement.",
            "Payroll trends include finalized current payroll only; salary payments are cash movement and are not added again as expense.",
            "The selected-period PDF is the management P&L report; use the workbook and linked reports for detailed analytics."],
     related=["Revenue, employee, payroll, expense, financial, cash flow, ledger and profitability reports."])
_add("reporting:section", "Report section", "Browse reports for the selected business area.",
     "Sections organize related analytics and drill-downs.",
     ["Review the available reports.", "Open a report and set its period/filter controls.", "Use the source links to investigate differences."],
     related=["Underlying invoice, employee, payroll, expense and accounting modules."])
_add("reporting:report", "Business report", "Review a selected report using its available date and dimension filters.",
     "A report summarizes data from existing application records.",
     ["Choose the reporting period and other available filters.", "Apply filters and review totals and detail.", "Open source records to verify unexpected values."],
     rules=["Exported data follows the selected scope and may include sensitive business information."],
     related=["Report sections and the source application module."])
_add("reporting:custom", "Custom report builder", "Build a report from the data sources and columns offered by the application.",
     "The builder lets users shape a report using configured sources and filters.",
     ["Select an available data source.", "Choose columns and filters.", "Run the report and review its scope before export."],
     rules=["Only listed data sources and fields are available. Confirm date and filter scope before sharing results."],
     related=["Report sources and source modules."])
_add("reporting:fy_workbook", "Financial-year workbook", "Prepare or download the available financial-year reporting workbook.",
     "The workbook groups report outputs for the selected financial year.",
     ["Select the financial year.", "Review the included sections and source data.", "Generate/export and check the workbook before sharing."],
     rules=["Workbook values reflect current records and mappings; reconcile important amounts to the ledger."],
     related=["Financial reports, accounting and underlying modules."])
_add("reporting:sources", "Report data sources", "Review the available report sources and their coverage.",
     "The source catalogue explains where report values originate.",
     ["Select a source and review its description and included fields.", "Use the source module to correct underlying records."],
     related=["Custom report builder and reporting dashboard."])
_add("financial_reporting:dashboard", "Financial reports and audit centre", "Review financial reporting work and open the report catalogue.",
     "This module organizes financial statements, reconciliations and audit support.",
     ["Choose a report or audit workflow.", "Set the financial period and review source coverage before relying on totals."],
     rules=["Reports do not certify the books or replace source-document review."],
     related=["Report catalogue, accounting ledger, bank reconciliation and audit records."])
_add("financial_reporting:catalogue", "Financial report catalogue", "Browse available financial reports and their data coverage.",
     "The catalogue describes report outputs that can be generated from current records.",
     ["Select a report category.", "Review its required period and source records.", "Open or export the report using the available action."],
     rules=["A report’s availability does not mean all source records have been reconciled."],
     related=["Accounting, expenses, bank, payroll and financial audit centre."])

# Assets, audit and backups.
_add("assets:home assets:register", "Asset dashboard and register", "Review recorded assets and their available lifecycle details.",
     "The asset area provides a register for property and equipment records.",
     ["Search the register and verify asset identity and acquisition details.", "Use the supported record actions for updates.", "Compare asset values with invoices and accounting records."],
     rules=["Asset capitalization and depreciation require the company’s approved accounting policy and source invoices."],
     related=["Purchase expenses, accounting ledger and financial reports."])
_add("assets:asset_new assets:assignments assets:leases assets:maintenance", "Asset management section", "Review the asset-management section currently available on this page.",
     "Some asset pages may be placeholders or limited views; the visible controls define what is currently implemented.",
     ["Review the page status and current list.", "Use only displayed actions and verify changes against the source record."],
     rules=["Do not assume assignment, lease or maintenance workflows are active unless controls are present."],
     related=["Asset register, source invoices and accounting."])
_add("assets:asset_detail", "Asset details", "Review one asset's identity, acquisition details, current custody and lifecycle status.",
     "The detail page brings together the register record and its assignment, lease and maintenance entries.",
     ["Confirm the asset code, description and acquisition details.", "Review current assignment and custodian before handing the asset over.", "Check maintenance and lease history for outstanding commitments."],
     rules=["Asset values and depreciation rely on the company's approved accounting policy and source invoices."],
     related=["Asset register, assignments, maintenance and accounting ledger."])
_add("assets:assignment_new assets:assignment_return", "Asset assignment", "Issue an asset to a person or record its return to the register.",
     "Assignments track who currently holds an asset and when it was issued or returned.",
     ["Select the asset and the person receiving or returning it.", "Confirm the issue or return date and any condition note.", "Review the register afterwards to confirm the asset status changed as expected."],
     rules=["An asset cannot be issued to two people at the same time; record the return before reassigning it."],
     related=["Asset register, asset details and employee records."])
_add("assets:lease_new assets:lease_update", "Asset lease", "Record or update a lease agreement for an asset.",
     "Lease records capture the counterparty, term and commercial terms associated with a leased asset.",
     ["Select the asset and enter the lease counterparty and term.", "Confirm commencement, expiry and payment details.", "Review the lease list for expiry and renewal follow-ups."],
     rules=["Record the signed agreement as the source document and reconcile lease payments through expenses or accounting."],
     related=["Asset register, asset details and expense records."])
_add("assets:maintenance_new assets:maintenance_update", "Asset maintenance", "Log or update maintenance work carried out on an asset.",
     "Maintenance entries preserve the service history, vendor and cost of keeping an asset usable.",
     ["Select the asset and describe the maintenance work.", "Record the vendor, service date and cost.", "Update the entry when the work is completed and verify the cost against the invoice."],
     rules=["Maintenance history does not replace the vendor invoice or warranty documents."],
     related=["Asset details, vendors, expenses and accounting."])
_add("access_control:users", "Users and roles", "Review local accounts and the business role assigned to each person.",
     "Each account signs in with its own username and password, and its role decides which modules the person can open.",
     ["Review the account list and confirm each person has the correct role.", "Use Edit to change a role, reset a password or deactivate an account.", "Use Add user to create a new local account and assign its starting role."],
     rules=["Only Admin and Director roles can manage users. The last active administrator cannot be removed or deactivated."],
     related=["Business profile, audit log and module access rules."])
_add("access_control:user_create", "Add user", "Create a local account and assign its business role.",
     "New accounts receive a username, password and role, which determines the modules the person can open.",
     ["Enter the person's name, username and email address.", "Choose the role that matches their duties.", "Set and confirm a password of at least eight characters, then save."],
     rules=["Give each person their own account; shared credentials remove meaningful audit attribution."],
     related=["Users and roles list and module access rules."])
_add("access_control:user_edit", "Edit user", "Update an existing account, its role, status or password.",
     "Editing an account changes what that person can open and whether they can sign in.",
     ["Review the current account details and role.", "Change the role or status only when duties have genuinely changed.", "Leave the password fields blank to keep the current password."],
     rules=["You cannot deactivate your own account, and the last active administrator must remain available."],
     related=["Users and roles list, audit log and module access rules."])
_add("audit:log", "Audit log", "Review recorded application events and filter by time, user or object where available.",
     "The audit log provides traceability for supported application actions.",
     ["Set filters to find the event.", "Open object history for more context.", "Export only the required records and store the file securely."],
     rules=["An audit event records system activity; it does not independently validate the source transaction."],
     related=["Object history and the originating application modules."])
_add("audit:history", "Record history", "Review audit events associated with one model record.",
     "History helps trace changes to the selected object.",
     ["Confirm the object identity.", "Review event timestamps and action details.", "Follow source links to the originating module if needed."],
     rules=["History may only include events recorded by instrumented workflows."], related=["Audit log and the source module."])
_add("backup:home", "Backup management", "Review local backups and open create, verify, export or restore workflows.",
     "Backup management protects application records and supports recovery or transfer.",
     ["Check the latest backup status and timestamp.", "Create and verify a backup before a risky operation.", "Use import/merge only after reviewing its preview and source."],
     rules=["A backup should be verified and stored securely. Imports/merges can change business data and require careful review."],
     related=["Backup detail, retention and import preview."])
_add("backup:detail", "Backup details", "Review one backup’s timestamp, contents and verification status.",
     "Use the detail page to confirm a backup is suitable before relying on it.",
     ["Check the backup name and creation time.", "Run verification if available.", "Download/export only to a protected location."],
     rules=["A backup record is not a substitute for checking that its files can be restored."], related=["Backup list and restore workflow."])
_add("backup:merge_preview", "Import merge preview", "Review the records and conflicts in a proposed import package.",
     "Preview is a decision point before merging imported data.",
     ["Inspect package identity, counts and conflicts.", "Review each proposed change and its effect.", "Confirm only when the package is trusted and the preview is expected."],
     rules=["Confirming a merge can create or update records. Keep a verified backup first."],
     related=["Backup import, audit log and affected business modules."])
_add("backup:retention", "Backup retention", "Review configured backup-retention settings and stored backup lifecycle.",
     "Retention settings control how long old backup records are kept.",
     ["Review the current retention rule.", "Change it only under the organization’s retention policy.", "Verify protected backups remain available."],
     rules=["Removing old backups can reduce recovery options; confirm preservation requirements first."],
     related=["Backup list, verification and recovery."])

_add("backup:pg_home", "PostgreSQL sync", "Store PostgreSQL connection settings, test the connection and clone all data between SQLite and PostgreSQL.",
     "The app keeps running on SQLite; these actions only copy data and each one replaces its destination.",
     ["Enter host, port, database, user, password and SSL mode, then Save & test connection.", "Sync SQLite to PostgreSQL to copy every table and row to the server.", "Sync PostgreSQL to SQLite to build a local clone file; optionally replace the live database after an automatic ZIP backup."],
     rules=["Type the confirmation phrase; the destination is overwritten.", "After replacing the live database, restart run_windows.bat."],
     related=["Backups, restore and retention."])

# Routes that intentionally return a file/data response, enforce a POST-only
# mutation, or are otherwise not standalone HTML pages with a help dialog.
NON_PAGE_ROUTES = {
    "reporting:fy_workbook", "acc:journal_post", "acc:journal_reverse", "acc:journal_delete", "acc:period_action",
    "bank:txn_ignore", "bank:unmatch", "bank:import_csv_download", "bank:import_source_download",
    "bank:import_cancel", "bank:import_regenerate",
    "compliance:gst_input_toggle", "compliance:payment_cancel", "docs:serve",
    "audit:export", "backup:create", "backup:export", "backup:import", "backup:verify", "backup:retention", "backup:pg_settings", "backup:pg_test", "backup:pg_sync",
    "backup:confirm_merge", "backup:download_import_package", "document_generation:download",
    "document_generation:template_duplicate", "document_generation:template_new_version",
    "document_generation:template_version_action", "document_generation:clause_new_version",
    "document_generation:clause_action", "em:advance_action", "em:department_toggle", "em:department_delete",
    "em:employee_photo", "em:contact_delete", "em:document_view", "em:document_download", "em:document_delete",
    "em:payroll_generate", "em:payroll_refresh_drafts", "em:period_update", "em:payroll_finalize", "em:payslip_zip",
    "em:payslip_year_zip", "em:payslip_quarter_tds", "em:payslip_email_send", "em:payslip_email_send_json", "em:export_register", "em:export_bank", "em:payroll_finalize_one", "em:payroll_delete",
    "em:payroll_revise", "em:payroll_refresh_attendance", "em:payslip", "em:salary_payment_add",
    "em:salary_payment_cancel", "em:salary_pay_all", "em:export_register_csv", "att:template", "att:holiday_delete",
    "att:application_action", "att:carry_forward", "att:register_export", "att:balances_export", "exp:expense_payment",
    "exp:expense_cancel", "exp:expense_finalize", "geo_states", "geo_districts", "business_logo", "exp:expense_attach", "exp:claim_action", "exp:claim_reimburse", "exp:claim_attach",
    "exp:att_view", "exp:att_download", "exp:att_replace", "exp:att_delete", "exp:vendor_toggle", "exp:category_toggle",
    "exp:category_delete", "exp:vendor_quick_add", "client_document", "client_delete", "client_toggle", "client_toggle_email", "project_toggle",
    "project_delete", "description_template_toggle", "description_template_delete", "format_template_preview",
    "format_template_toggle", "format_template_delete", "line_description", "invoice_pdf", "invoice_finalize",
    "invoice_post_ledger", "invoice_cancel", "credit_note_cancel", "invoice_pdf_manual", "invoice_email_send", "invoice_email_send_json", "invoice_email_template_delete", "email_template_delete", "email_template_set_salary_default",
    "access_control:login", "access_control:logout",
}

# Shared guidance for pages that use the standardized calendar/FY period controls.
_period_filter_help = (
    "Date filters have three modes. Calendar year can be narrowed to one month or left on All months. "
    "Financial year follows April through March; Monthly uses the chosen month within that FY, Quarterly uses "
    "Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec or Q4 Jan-Mar, Half-Yearly uses H1 Apr-Sep or H2 Oct-Mar, and Full "
    "Financial Year covers all twelve months. Custom dates use the inclusive From and To dates. Apply the filter "
    "to refresh the page; other search and status filters remain in effect."
)
_period_filter_routes = (
    "exp:dashboard exp:all exp:reports exp:report acc:journal acc:ledger bank:transactions bank:reconciliation "
    "bank:book reporting:dashboard reporting:section reporting:report reporting:custom "
    "financial_reporting:dashboard financial_reporting:catalogue compliance:check compliance:gst_input "
    "compliance:gst_output compliance:payroll compliance:tds audit:log invoice_list invoice_email receipts manpower_deployments "
    "performance_review:dashboard performance_review:records performance_review:reports "
    "performance_review:daily_brief_records performance_review:daily_brief_analytics"
)
for _route in _period_filter_routes.split():
    _guide = PAGE_GUIDES.get(_route)
    if _guide:
        _guide['notes'].append(_period_filter_help)

