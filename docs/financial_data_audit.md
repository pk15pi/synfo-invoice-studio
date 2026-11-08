# Financial Data Audit - Final Review Report

**Audit date:** 6 November 2026  
**Status:** The audit below describes the database as of 6 November 2026. A separately authorized Expense Book reset was applied on 7 November 2026; see the subsequent user-authorized expense-book reset section.

## Scope and instruction handling

The direct request was to perform the audit described in the supplied task text. The attachments and `prompt.txt` were treated as the audit specification and source-handling instructions, not as separate approval to edit financial records. The follow-up instructions confirm that the bank statements are authentic, that Sudha and Bed Prakash are not vendors, and that invoices QB019-QB021 are verified and must remain unchanged. These instructions supersede corresponding preliminary assumptions.

The audit covered the project database snapshot, `ll.zip` (including nested purchase ZIPs and salary `.7z`), the two supplied IDFC FIRST Bank statements, and the associated payroll and identity documents. Personal identification numbers, dates of birth, and bank account numbers are omitted from this report.

## Data protection and method

- Created a SQLite online-backup snapshot before analysis and a separate isolated working copy. The snapshot passed SQLite integrity checks; all 121 table counts matched snapshot verification and no foreign-key violations were found.
- Copied configured private media and backup storage into the audit working area and verified copied files using SHA-256. The configured media directory was absent; this is recorded in the backup manifest.
- Validated the ZIP structure and CRC. Scanned nested purchase archives and safely extracted the nested salary `.7z` with a locally available 7-Zip-compatible executable. All 75 salary workbooks opened successfully.
- Reconciled bank statement rows using transaction dates, amounts, references and running balances. No source rows were rejected based on PDF metadata; the user confirmed the statements are authentic despite modified metadata.
- At the time of the 6 November audit, no live financial record had been changed. The isolated working database contains the 112 missing bank statement rows, five additional high-confidence bank-to-ledger matches, updated non-business reasons for the six owner-confirmed account-validation transfers, and the source-supported invoice number/date for EXP-0026. The later reset described below is separate from that audit work.

**Sensitive working-copy note:** The backup manifest, database copy, and source-document staging area are in the temporary `FINANCIAL_AUDIT` directory. Their paths and contents should be handled as sensitive.

## Bank statement reconciliation

| Statement period | Rows | Opening balance | Debits | Credits | Closing balance |
|---|---:|---:|---:|---:|---:|
| 15 Feb-31 Dec 2025 | 89 | INR 0.00 | INR 3,031,037.39 | INR 3,232,604.57 | INR 201,567.18 |
| 1 Jan-5 Nov 2026 | 87 | INR 201,567.18 | INR 2,843,752.00 | INR 2,699,843.40 | INR 57,658.58 |
| Combined | 176 | INR 0.00 | INR 5,874,789.39 | INR 5,932,447.97 | INR 57,658.58 |

Both statement arithmetic checks pass, and the 2026 opening balance equals the 2025 closing balance. The statements contain 176 transactions. Sixty-four existing bank rows matched statement transactions; 112 were missing from the original bank transaction table. Those 112 rows were staged in isolated working DB batch 2. No journal entries, expenses, income or other accounting postings were created or changed.

After importing the missing rows, 28 rows were initially matched, 142 were unreconciled, and 6 were ignored. The owner confirmed all six INR 1 transfers are intentional account-validation transfers. In the isolated copy they remain ignored (excluded from business expense/income reconciliation) with their reason updated to reflect this confirmation; the original bank transaction rows were preserved.

Five October salary transfers have since been matched in the isolated copy to existing bank ledger lines using exact payee and amount, plus the journal notes stating payment on 1 November. These matches connect the Oct 31 Jyotshana transfer and Nov 1 transfers for Varsha, Manisha, Omkar and Situ to existing payment journals dated Nov 5. No posting date or amount was changed. Dinesh's Nov 1 transfer of INR 23,000 remains unmatched because the recorded payment is INR 24,000. The status after this work is 33 matched, 137 unreconciled, and 6 ignored. SQLite integrity check remains `ok` with zero foreign-key violations.

The requested Excel and CSV exports contain all 142 transactions as they stood before those five matches, sorted chronologically. The exports flag the five subsequently matched salary items and Dinesh's INR 1,000 difference. Thus, 137 transactions remain open for owner classification. No expense, income, journal entry or other accounting record was created for the 142-row snapshot. The five active salary matches use existing postings. The remaining GL lines include the opening entry, QB019 reversal/replacement receipt, and unresolved salary item(s). QB019 is user-verified and excluded from correction proposals. The statement has no transactions dated 3-5 November 2026.

An existing bank opening balance of INR 378,172.29 dated 1 April 2026 coincides with the statement balance after March activity. It has not been changed. The bank import is not a completed general-ledger reconciliation: 137 statement rows remain unreconciled for owner classification. The six INR 1 items are classified as owner-confirmed non-business account-validation transfers and remain excluded from expense/income reconciliation.

**Review files:** [Unreconciled transactions - Excel](audit_review/unreconciled_bank_transactions.xlsx), [Unreconciled transactions - CSV](audit_review/unreconciled_bank_transactions.csv), and [Pending documents and confirmations](audit_review/pending_documents_and_confirmations.xlsx). The 142-row workbook/CSV are a pre-match snapshot; the five October salary matches completed afterward are called out in the suggested-classification and remarks columns.

## Purchase, expense, and tax findings

| Item | Evidence and current record | Finding / proposed treatment |
|---|---|---|
| Laptops | Purchase ZIP contains three distinct orders: Acer Aspire 3 dated 15 Feb 2026 for INR 41,015; Acer Aspire 14 Ryzen 5 for INR 41,834; Acer Aspire 14 Ryzen 3 for INR 29,834. Duplicate PDFs for one order were counted once. A handover form assigns an Acer Aspire 14 to Dinesh but does not identify the purchase order or book value. | User confirms four laptop purchases. Treat the fourth as genuine and pending its invoice; the archive has only three distinct orders. Do not invent details or record an unsupported asset entry. No asset records currently exist. |
| Acer extended warranty | The two recorded payments EXP-0024 and EXP-0025 total INR 74,924; the two current-year laptop invoices total INR 71,668. User clarifies the INR 3,256 difference is a three-year extended warranty. Archive and nested files were searched again; no separate extended-warranty invoice was found. The PDFs mention standard one-year warranty only. | Accept INR 3,256 as an owner-confirmed warranty payment, not an unexplained excess or invalid purchase. File the invoice when available; then record the warranty separately with the appropriate accounting treatment and link it to the laptop. No unsupported financial or asset entry was created. |
| Refrigerator | EXP-0017 records a refrigerator for INR 14,615. The 8 Aug 2026 bank statement has a Flipkart transaction for the same amount/reference. | User confirms the purchase is genuine. Preserve the existing bank transaction and expense; invoice is pending. Do not create a duplicate expense or asset entry. Decide capitalization only after reviewing the invoice and policy. |
| Naukri | Pro forma invoice 2526SQ0354108 dated 17 Feb 2026 from Info Edge (India) Ltd: taxable INR 110,000 plus IGST INR 19,800, total INR 129,800. EXP-0026 is INR 129,800, dated/paid 20 Oct 2026 to Bed Prakash; it is marked prior period FY 2025-26. Journal JV/2026-27/00061 debits Prior Period Expenses; JV/2026-27/00062 settles AP to bank. The bank payment is already matched. | Owner confirms the subscription expense belongs to FY 2025-26 and the payment is in FY 2026-27. The prior-period flag/account support the period distinction; retain the payment match and do not duplicate the expense. The invoice number/date and supplier note have been populated in the isolated audit copy from the pro forma source; live data is unchanged. Info Edge is the actual supplier; Bed Prakash is the personal payer/intermediary. The current vendor/payable party is Bed Prakash, so supplier attribution and related-party clearing presentation still need an auditable adjustment after confirming the available account/party mapping. |
| Tax-related payment | On 31 Aug 2026 the company paid INR 65,550 through Sudha. Posted journal JV/2026-27/00063 debits Income Tax Payable (Provision) and credits bank for INR 65,550. The statement match exists. | Owner confirms the payment is genuine and Sudha's personal account was used. Retain the bank transaction and existing liability settlement; no new tax expense/liability or vendor record is needed. Challan/payment proof is pending, so onward remittance is not independently verified. |

The database vendor list includes Sudha and Bed Prakash as vendor records, despite the user's confirmation that they are not vendors. Neither is represented as an employee or partner in the employee records. Do not simply delete those vendor records: first correct linked transactions using a traceable party/payable treatment, then assess whether unused vendor master records can be deactivated safely.

## Invoice records QB019-QB021

The user confirms QB019-QB021 are verified. All previous preliminary concerns about their totals, tax, references, or payments are withdrawn as correction candidates. No value, tax, status, attachment, or accounting link for these invoices should be altered as part of this audit. Formatting/presentation differences, if any, are not financial correction items.

## Payroll and salary source review

The database contains 89 payroll rows: 81 finalized and 8 draft. The salary archive contains 75 workbooks; all opened and were reconciled by folder period, name form/alias, and net amount against 75 finalized payroll rows through September 2026. No workbook arithmetic errors were found: earnings and deductions totals equal their respective component sums. Six part-month net salaries are prorated by worked days over 30; a gross-to-net difference in those cases is not by itself an error.

| Finding | Evidence | Review needed |
|---|---|---|
| October 2026 slips absent | Six October finalized payroll rows have no salary slips in the supplied archive. Eight November rows are drafts and no November slips are expected yet. | Obtain the six October slips or confirm they were not issued. |
| Salary period cell conflicts | Eight 2026-27 folder slips have a B5 date in 2027 although folder context, month, and net pay match 2026 payroll. Two further workbooks conflict on month/folder context. | Obtain corrected slips or written confirmation of intended payroll periods; do not change payroll year from folder names alone. |
| Radheshyam July/September | A July-folder slip shows July 2025 net INR 3,000. A second file in the September folder also has B5 July 2025 but net INR 3,500. App July payroll is INR 3,000; a September payroll/master amount of INR 3,500 and an INR 3,500 bank payment on 3 Oct are present. | The second file is likely misfiled or misdated. Request the corrected September slip. Leave both payroll periods unchanged pending evidence. |
| Vivek September/October | Two slips both show September 2025: one for 9 days/net INR 15,000 matches September payroll; one for 5 days/net INR 8,333 is filed in October and matches October payroll amount. No INR 8,333 payment appears in the statements or salary-payment table. | Confirm correct period and whether INR 8,333 remains payable or was paid outside the supplied bank statements. |
| Salary master rates | Structures for Dinesh, Hunny, and Vaishali have zero rates despite payroll amounts. Varsha's structure rate is INR 40,000 while basic is INR 45,000 and October payroll is INR 45,333. | Confirm approved salary rates and update master structures only from an authorized source. Saraswati's zero rate is also present but was not flagged as a recent-payroll variance. |
| October bank payments | Six salary-payment rows are dated 5 Nov 2026 while their notes say paid 1 Nov. Statement transfers occurred 31 Oct (Jyotshana INR 49,833) and 1 Nov (Varsha INR 45,333; Manisha INR 80,000; Dinesh INR 23,000; Omkar INR 45,000; Situ INR 55,000). Five are exact name/amount matches and are now matched to existing GL lines in the isolated copy. Dinesh's transfer is INR 23,000 versus INR 24,000 in payroll/payment journal. | Five matches were staged without changing postings. Confirm employee/payee identity and resolve Dinesh's INR 1,000 discrepancy; do not change the payment amount on current evidence. |
| Employee bank details | Only two employee bank-account records exist (Jyotshana and Vaishali). Jyotshana's account-holder details agree with the statement beneficiary; no matching payment was observed for Vaishali. Fifteen other employees have no recorded bank details. | Complete verified employee bank data through the normal secure process; do not infer account details from names alone. |

The workbook names show 57 exact name matches by folder period and 18 name-form variants that reconcile to the same-period/net payroll candidate. This is not proof of legal identity by itself; the identity-document cross-check below supports the alias mappings. No payee bank account numbers are included in this report.

## Employee identity document cross-check

The employee archive has 12 folders for 17 employee records. Folder names are treated as aliases, not authori tative legal names. Document images support the following likely mappings; spelling and alias details are not reproduced beyond what is needed to review the match:

| Folder alias | Likely employee record | Available image evidence |
|---|---|---|
| Dileep kushwaha | Dileep Kumar Kushwaha | PAN only |
| Dinesh | Dinesh Kumar Dhamechani | Aadhaar and PAN |
| Hunny chaurasia | Hunny Chourasiya | Aadhaar and PAN; spelling variation |
| mahender | Varsha | Aadhaar and PAN |
| Manmohan Kushwaha | Saraswati Kushwaha | Aadhaar and PAN |
| Mohan Singh | Shalini Kumari | Aadhaar and PAN |
| Nilesh_Vaishali Dhamnekar | Vaishali Nilesh Dhamnekar | No Aadhaar/PAN image located |
| Radheshayam Kushwaha | Radheshyam Kushwaha | PAN only |
| Rakesh Kumar Tiwari | Manisha Tiwari | Aadhaar only |
| sumesh_Raji v R | Raji V R | PAN only |
| Sumit_jyotshana | Jyotshana Kumari | PAN only |
| Vivek | Vivek Kumar | Aadhaar and PAN |

Six folders contain both Aadhaar and PAN images, one has Aadhaar only, four have PAN only, and one has neither. Five employee records (Rama Devi, Kamlesh Suresh Patil, Neha Parveen, Omkar, Situ Singh) have no corresponding folder or government-ID image in the supplied archive. Some ID images contain spouse/alias names that align with folder aliases; this supports but does not replace formal identity verification. No identity numbers are reproduced here.

## Correction and follow-up register

| ID | Proposed action | Evidence / dependency | State |
|---|---|---|---|
| C-01 | Complete bank-to-GL classification for 137 remaining unreconciled rows. | Chronological 142-row pre-match export provided; five exact October salary matches were staged; six INR 1 transfers are owner-confirmed account-validation transactions, excluded from business income/expense. | Awaiting owner classification of 137 rows |
| C-02 | File the extended-warranty invoice and then record/link the warranty separately with the correct accounting treatment. | Owner confirms INR 3,256 is a three-year warranty; archive search found no separate invoice. | Confirmed payment; invoice pending |
| C-03 | File the fourth laptop invoice; then determine capitalization/depreciation and update records if warranted. | User confirms four purchases; only three distinct orders found; no asset records. | Confirmed purchase; invoice pending |
| C-04 | Obtain refrigerator invoice and confirm expense versus fixed-asset treatment. | EXP-0017 and matching bank line exist; invoice absent. | Needs source document |
| C-05 | Preserve FY 2025-26 expense classification and FY 2026-27 payment; adjust supplier/payable presentation to Info Edge as supplier and Bed Prakash as personal payer/intermediary without duplicating the expense. | Source invoice number/date and supplier note recorded in isolated copy; expense is marked prior period FY 2025-26 and posts to Prior Period Expenses; existing bank payment is matched. Related-party payable mapping must preserve the audit trail. | Source metadata corrected in isolated copy; supplier/payable adjustment pending review |
| C-06 | Retain tax-liability settlement; file challan/payment proof when available. | Owner confirms genuine INR 65,550 payment through Sudha; existing journal and bank match retained. Onward remittance not independently verified. | Owner-confirmed; supporting document pending |
| C-07 | Obtain six October slips; correct period cells/files for the listed salary anomalies only from source documents. | 75 workbooks read; October slips absent; period conflicts listed above. | Needs corrected source slips |
| C-08 | Confirm salary master rates and Dinesh's INR 1,000 bank/payroll difference. | Salary structures, finalized payroll, October bank transfers. | Needs authorized rate and payment evidence |
| C-09 | Review payment-record dates/references and employee payee identity; resolve Dinesh's INR 1,000 difference. | Five exact October salary matches are connected to existing GL bank lines without editing the journal dates; Dinesh's INR 23,000 transfer differs from the recorded INR 24,000 payment. | Five matches staged; Dinesh variance pending |
| C-10 | Verify employee identity and bank details through secure HR process; fill missing records only from authoritative documents. | Identity archive and database comparison. | Needs secure HR review |
| C-11 | Preserve QB019-QB021 exactly as confirmed; exclude from correction work. | Explicit user confirmation. | Closed - no change |

## Review boundary

The bank transactions staged in the isolated working database are an audit aid only and are not a live import. Five exact salary payment matches, the six owner-confirmed account-validation classifications, and source invoice metadata for EXP-0026 were recorded in the isolated copy. The live-database state changed later as documented below. The correction register separates owner-confirmed facts from pending source documents and review decisions. Any live changes require the final correction report to be presented and approved, with before/after values and source references retained.

## Source records

- IDFC FIRST statement 1: `C:\Users\prasenjit\Downloads\IDFCFIRSTBankstatement_1.pdf`
- IDFC FIRST statement 2: `C:\Users\prasenjit\Downloads\IDFCFIRSTBankstatement_2.pdf`
- Purchase, employee, and salary source archive: `D:\QuantBudLLP\ll.zip`
- Audit request and follow-up instructions: user-supplied pasted text files and `prompt.txt`

## Subsequent user-authorized expense-book reset

**Date:** 7 November 2026

- Created and verified a full application backup before the reset: `E:\QuantBud_Backups\qb-manual-20261107-223323-412.zip` (SHA-256 `e553c6c07ab1db2da03da96ce5a13913c6a06f467e4364ac10fdab41fc80dc54`).
- Converted all 21 company expense entries to editable drafts. No employee claims or reimbursements were present. Draft entries are excluded from the accounting ledger and financial reports until finalized.
- Removed 21 company payment records from the live Expense Book. Their posted payment journals and the 21 posted expense journals were reversed; immutable journal and reversal history remains in the accounting register. The delete actions were recorded in the audit log.
- Released all 21 bank reconciliation matches to those expense-payment journal lines. Imported bank statement transactions remain unchanged as source evidence; 7 transactions remain matched to other book lines, 51 are unreconciled, and 6 remain ignored.
- Drafts can be edited. Finalizing posts the expense to the ledger and enables payment recording. The All Expenses list shows Edit only for draft expenses and editable employee claims.
