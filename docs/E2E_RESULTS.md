# End-to-end results (Stage 9)

Test: `accounting/test_e2e.py` → `EndToEndTests.test_full_scenario`. It runs on a fresh test DB and never touches `db.sqlite3`.
Regenerate this table with `set QB_E2E_REPORT=e2e.md` (PowerShell: `$env:QB_E2E_REPORT='e2e.md'`), then run `manage.py test accounting.test_e2e`.

## Scenario (FY 2026-27, all amounts in INR)
1. Company profile, HDFC bank with opening balance 2,00,000 on 1 Apr 2026, customer, project (carried on the invoice line).
2. Invoice E2E-001, 5 Oct: 1,00,000 + CGST 9,000 + SGST 9,000 = 1,18,000, finalized.
3. Receipt, 20 Oct: 60,000 to the bank, with deductions of 2,500 (TDS 2,000 + bank charges 500). AR is cleared by 62,500.
4. Credit note, 25 Oct: 1,000 + GST 180 = 1,180.
5. Vendor expense, 10 Oct: 10,000, no GST. It posts on save; company expenses have no approval workflow.
6. Partial vendor payment, 22 Oct: 5,000, leaving the bill Partially Paid.
7. Payroll for October: 50,000 gross, finalized. No statutory rules are configured in the test DB, so net = 50,000. An advance of 6,000 was paid on 15 Oct.
8. October salary paid 20,000 on 2 Nov (status partial), then 30,000 on 4 Nov (status paid).
9. November payroll recovers 3,000 of the advance, so November net = 47,000. Advance balance: 3,000.
10. Claim of 5,000, approved, 3,000 reimbursed on 28 Oct. Payable: 2,000.
11. A 7-row CSV statement is imported (header guessed, running balance) and every row is matched to its ledger line.
12. Statutory payment of October CGST, 7 Nov: 8,910 (9,000 − 90 credit note).
13. October closed. A manual entry and a vendor payment dated 30 Oct are both rejected, and the entry count is unchanged.
14. Manual accrual of 700 on 10 Nov, reversed on 11 Nov in the open period. AP and the trial balance are unchanged.
15. `create_backup` + `verify` into a temp BACKUP_DIR: verify OK, 0 problems.

Bank: 2,00,000 + 60,000 − 5,000 − 6,000 − 3,000 − 50,000 − 8,910 = **1,87,090**.
P&L expenses: 10,000 + 5,000 + 500 + 50,000 + 50,000 = **1,15,500**. Income: 1,00,000 − 1,000 = **99,000**. Loss: **16,500**.

Sign note: `party_balance` rows are debit-positive (−10,000 = a credit of 10,000). `balance()` on a liability account is natural (credit-positive).

## Expected vs actual
| # | Check | Expected | Actual | Result |
|---|---|---:|---:|---|
| 1 | 1. Bank opening balance (ledger) | 200,000.00 | 200,000.00 | OK |
| 2 | 2. AR after invoice | 118,000.00 | 118,000.00 | OK |
| 3 | 3. AR after receipt (118000 - 62500) | 55,500.00 | 55,500.00 | OK |
| 4 | 3. TDS receivable | 2,000.00 | 2,000.00 | OK |
| 5 | 4. AR after credit note (55500 - 1180) | 54,320.00 | 54,320.00 | OK |
| 6 | 5. AP after expense | -10,000.00 | -10,000.00 | OK |
| 7 | 6. AP after partial payment | -5,000.00 | -5,000.00 | OK |
| 8 | 7. Oct gross | 50,000.00 | 50,000.00 | OK |
| 9 | 7. Oct salary payable = net | 50,000.00 | 50,000.00 | OK |
| 10 | 8. Salary payable after full payment | 0.00 | 0.00 | OK |
| 11 | 9. Nov advance recovery | 3,000.00 | 3,000.00 | OK |
| 12 | 9. Advance balance (6000 - 3000) | 3,000.00 | 3,000.00 | OK |
| 13 | 9. Nov salary payable = Nov net | 47,000.00 | 47,000.00 | OK |
| 14 | 10. Reimbursement payable | -2,000.00 | -2,000.00 | OK |
| 15 | 12. CGST payable after payment | 0.00 | 0.00 | OK |
| 16 | Bank ledger balance | 187,090.00 | 187,090.00 | OK |
| 17 | 11. Statement rows imported | 7.00 | 7.00 | OK |
| 18 | 11. Reconciliation book balance | 187,090.00 | 187,090.00 | OK |
| 19 | 11. Statement balance | 187,090.00 | 187,090.00 | OK |
| 20 | 11. Reconciliation difference | 0.00 | 0.00 | OK |
| 21 | 11. Unexplained difference | 0.00 | 0.00 | OK |
| 22 | 11. Unreconciled bank items | 0.00 | 0.00 | OK |
| 23 | 11. Unreconciled book items | 0.00 | 0.00 | OK |
| 24 | Re-post: journal entry count unchanged | 14.00 | 14.00 | OK |
| 25 | TB difference | 0.00 | 0.00 | OK |
| 26 | BS difference | 0.00 | 0.00 | OK |
| 27 | P&L income (invoice 100000 - credit note 1000) | 99,000.00 | 99,000.00 | OK |
| 28 | P&L expenses (vendor + claim + bank charges + 2x payroll cost) | 115,500.00 | 115,500.00 | OK |
| 29 | P&L profit | -16,500.00 | -16,500.00 | OK |
| 30 | BS current-year earnings = P&L profit | -16,500.00 | -16,500.00 | OK |
| 31 | AR (ledger) | 54,320.00 | 54,320.00 | OK |
| 32 | AP (ledger, natural credit balance) | 5,000.00 | 5,000.00 | OK |
| 33 | Salary payable (ledger) = Nov net unpaid | 47,000.00 | 47,000.00 | OK |
| 34 | Reimbursement payable (ledger) | 2,000.00 | 2,000.00 | OK |
| 35 | Cash-flow opening (before 1 Apr) | 0.00 | 0.00 | OK |
| 36 | Cash-flow closing = bank ledger | 187,090.00 | 187,090.00 | OK |
| 37 | Cash-flow closing = reconciliation book balance | 187,090.00 | 187,090.00 | OK |
| 38 | Cash-flow difference | 0.00 | 0.00 | OK |
| 39 | Mgmt vs ledger difference: Revenue (invoice taxable value) | 1,000.00 | 1,000.00 | OK |
| 40 | Mgmt vs ledger difference: Company expenses | 0.00 | 0.00 | OK |
| 41 | Mgmt vs ledger difference: Employee claims expense | 0.00 | 0.00 | OK |
| 42 | Mgmt vs ledger difference: Accounts receivable (as of end) | 0.00 | 0.00 | OK |
| 43 | Mgmt vs ledger difference: Accounts payable (as of end) | 0.00 | 0.00 | OK |
| 44 | Mgmt vs ledger difference: Reimbursement payable (as of end) | 0.00 | 0.00 | OK |
| 45 | Mgmt vs ledger difference: Salary payable (as of end) | 0.00 | 0.00 | OK |
| 46 | Mgmt vs ledger difference: Salary + employer cost | 0.00 | 0.00 | OK |
| 47 | 13. Entries after blocked postings | 14.00 | 14.00 | OK |
| 48 | 14. AP after entry + reversal (unchanged) | 5,000.00 | 5,000.00 | OK |
| 49 | Audit events recorded (> 0) | 1.00 | 1.00 | OK |
| 50 | 15. Backup verify problems | 0.00 | 0.00 | OK |

The Revenue row in the management-vs-ledger reconciliation shows 1,000 on purpose. The management revenue report is gross of credit notes, while the ledger nets them, and the report explains the gap. Every other measure ties at 0, and the bank reconciliation has 0 difference, 0 unexplained and 0 unreconciled items.

Other assertions not shown in the table:
- The trial balance and balance sheet both have `balanced=True`.
- The re-posted trial balance is identical, and re-posting created 0 new entries.
- Audit events exist for Invoice, InvoicePayment, CreditNote, CompanyExpense, EmployeeExpenseClaim, EmployeePayroll, SalaryPayment, EmployeeAdvance, JournalEntry, FinancialPeriod, BankTransaction and StatutoryPayment.
- The reconciliation page returns 200.
