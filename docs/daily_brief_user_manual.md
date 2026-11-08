# Daily Brief User Manual

Daily Brief is available under **Performance Review**. It records the daily JD-wise resume activity that management receives by email and enters manually. It is a separate progress log: saving a Daily Brief does not create or edit an operational JD, candidate, client, placement, invoice, payroll, expense or accounting record.

The application follows its existing local management-only access convention. There is no separate recruiter login or role system.

## Entry & Records

1. Select an active employee who is marked as a recruiter in Performance Settings. The selection stores that existing Employee record.
2. Select the activity date. It defaults to today, but earlier dates can be entered.
3. Enter **Selections Achieved**, a non-negative whole-number count of selections the recruiter completed on that activity date.
4. Enter one row for each JD reported. JD Number is text (for example, `07-01`); Total Resumes Submitted must be a whole number of zero or more.
5. Use **+** to add a JD row and **−** to remove one. At least one row remains. The JD and resume totals update from complete valid rows; the saved JD total is maintained from the saved detail rows.
6. Save the report. Recruiter and activity date are unique together. If a report already exists, the page links to it for review/edit rather than overwriting it.
7. Use **View** to inspect all rows and correction history. Use **Edit** to correct the report. Each edit stores before/after snapshots. Historical records are corrected through Edit; there is no delete action.

The records table shows activity date, recruiter, JD count, resume count, selections achieved, expandable JD details, creation time, and View/Edit actions. Search recruiter names or employee IDs and JD Numbers. Searches combine with date and recruiter filters. Use sortable column headings and choose 25, 50 or 100 rows per page.

## Date filters

The records and analytics pages use the shared calendar and financial-year selectors:

| Period | Meaning |
|---|---|
| Calendar year | January 1 through December 31; optionally narrow to a calendar month. |
| Monthly | The chosen month within the selected FY; January–March are in the FY's ending calendar year. |
| Quarterly | Q1 April–June, Q2 July–September, Q3 October–December, or Q4 January–March. |
| Half-yearly | H1 April–September or H2 October–March. |
| Full financial year | April 1 through March 31 of the following year. |
| Custom date range | From and To dates, inclusive. |

Financial reporting periods use the Indian April–March year. All periods filter by **Activity Date**, never by record creation time. The date period combines with recruiter, recruiter-name/ID and JD Number searches.

## Daily Brief Analytics

Every card, chart and recruiter comparison uses the selected period and filters and reads only Daily Brief records and their JD detail rows.

### KPI calculations

- **Total JDs Submitted:** count of saved JD detail rows.
- **Total Resumes Submitted:** sum of `Total Resumes Submitted` across those rows.
- **Recruiters Active:** distinct recruiters with at least one matching Daily Brief.
- **Daily Briefs Submitted:** count of matching parent Daily Brief records.
- **Average Resumes per JD:** total resumes divided by JD detail rows; zero when there are no JD rows.
- **Average JDs per Recruiter:** JD rows divided by distinct recruiters; zero when there are none.
- **Selections Achieved:** sum of the manually entered selection counts for matching Daily Brief records.

### Charts

1. Daily JD submission trend.
2. Daily resume submission trend.
3. Recruiter-wise JD totals.
4. Recruiter-wise resume totals.
5. Grouped recruiter JD/resume productivity comparison.
6. Each recruiter’s share of submitted JD rows.
7. Average resumes per JD by recruiter.

The recruiter comparison table includes selections achieved alongside JD and resume totals. Selection counts are manually reported and are not inferred from candidate records. If no data matches, charts show an empty-state message and averages safely show zero. The same JD Number reported on different activity dates counts each time; it is not deduplicated across days. Resume totals do not imply interview, offer or placement outcomes.
