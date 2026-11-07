# Recruiter Performance Review - User Manual

The module is available from the **Performance Review** navigation menu and the Performance Review card on the home page. It uses the existing Employee and candidate records. It is management-only under the current local-only application architecture; the app does not have recruiter logins or individual user roles.

Daily recruiter progress reports are recorded separately in **Performance Review → Daily Brief**. See the independent [Daily Brief User Manual](daily_brief_user_manual.md) for entry, records, date filters, analytics and correction history. Daily Brief does not modify the candidate-level Performance Review records described below.

## Performance Dashboard

Choose a reporting period and apply the filter. Options include this/previous month, current/previous/selected financial quarter, current/previous/selected financial year, calendar year, selected month and custom date range. A financial year starts in the month configured in Performance Settings (April by default). Q1 is the first three financial-year months.

Candidate-verified totals use individual candidate submissions. Summary-only figures are shown in a separate section and never added to candidate-verified totals, selection rates or recruiter rankings. Charts use the stored records for the selected date range. The dashboard shows unique JDs separately from recruiter-JD assignments.

## Record Recruiter Activity

1. Add a JD with its ID, client, job title, optional opening date and status.
2. Assign one or more active employees marked as recruiters. A JD may have several recruiter assignments; a recruiter may work on multiple JDs.
3. Use **Summary-only activity** for aggregate totals when individual candidate records are not available. Include the activity date and the five aggregate values as applicable. These counts remain separate from candidate-level records.
4. Use **Candidate-level outcome** for each named/reference candidate submission. The candidate must first exist in the existing Recruitment placements candidate list. Choose the recruiter assignment, submission date, current stage and outcome.
5. If rejected, record the rejection stage. A rejection reason is optional. A rejection date defaults to the transition date if left blank.
6. Record the selection, offer release, offer acceptance and joining milestone dates when the corresponding outcome is reached. Editing a stage or outcome adds a dated transition row to candidate history.

Duplicate controls prevent the same candidate from appearing twice under one recruiter assignment and prevent a second selection for the same JD. The same candidate may be recorded against another JD. A JD's unique count is per recruiter; assignments are also counted separately. Summary-only values are not reconciled against candidate identities and must not duplicate detailed records.

## Recruiter-wise Records

Use the date filter to see JD assignments, detailed candidates and summary-only activity in separate tables. Edit a JD or candidate to correct its current values. Candidate stage and outcome history remains visible in the expandable history cell. Summary-only edits preserve previous and current values in a separate history record.

Historical records remain linked to former employees. Recruiter selection for new assignments defaults to active/probation/confirmed employees; former employees remain available in historical reports.

## Performance Reports

The report table includes recruiter ID/name, department, designation, unique JDs, assignment count, candidate-verified submissions, unique submitted candidates, rejections, selections, offers, joins and selection rate. Totals appear in the footer. Summary-only counts are shown separately. Monthly and quarterly summaries use the configured financial-year convention.

Formulas:

- **Selection rate** = candidate-verified candidates selected in the period ÷ unique candidates submitted in the period × 100. A zero denominator returns 0%.
- **Rejection rate** = candidate-verified rejections ÷ unique candidates submitted × 100.
- **Joining conversion rate** = candidate-verified joins ÷ unique candidates submitted × 100.
- **Performance score** = normalized joins × configured joining weight + normalized selections × selection weight + selection conversion among resolved selected/rejected candidates × quality weight + normalized unique JDs × JD weight. The default weights are 40/30/20/10 and must total 100%. Count components are normalized to the highest recruiter value in the chosen period. Pending candidates are excluded from the quality-conversion denominator. Recruiters with no selected, rejected or joined outcome are not ranked. Equal scores share a rank; the listing orders ties by joined count, selected count, then employee ID.

Excel export contains seven sheets: Overall Summary, Recruiter Performance, JD-wise Performance, Candidate Outcomes, Rejection Analysis, Monthly Trend and Quarterly Summary. PDF export includes the period, generation date, summary figures, recruiter table, ranking, rejection stages and a monthly chart. Both exports use the active filters and separate aggregate from candidate-verified figures.

## Performance Settings

Select existing active employees to mark them as recruiters; this changes the recruiter flag on the Employee record and does not create a duplicate person. Configure stage labels, rejection stage labels and optional rejection reasons; inactive choices disappear from new-entry forms but historical references remain. Update scoring weights only when their total is 100%. Set the financial-year start month if the reporting calendar changes.

## Review notes

- Inactive former recruiters remain in historical reports when they have assignments or their recruiter flag remains set.
- The system does not infer legal identity or personal information from a candidate name/reference.
- Summary-only figures do not provide rejection-stage breakdowns or candidate-level conversion rates.
- Correct milestone dates are needed for reliable period reporting and PDF/Excel exports.
