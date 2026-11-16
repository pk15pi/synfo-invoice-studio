# Document automation scheduling

The document automation runner is a Django management command and does not depend on a browser page staying open. The project has no task scheduler or supported outbound email configuration, so production Windows deployments should invoke it with Windows Task Scheduler.

From the application directory, use:

```powershell
.venv\Scripts\python.exe manage.py run_document_automation
```

Schedule it hourly. If a previous run still holds the database lease, the new run exits as skipped; the lock automatically expires after 30 minutes if the process stops unexpectedly. Reminder notifications use stable idempotency keys, so repeated scheduler invocations do not create duplicate reminders for the same threshold. Failed rule executions are retained in the automation history and can be retried from the management page.

The command records in-app notifications only. Email delivery is disabled because this application has no supported document-email integration. A run never issues, cancels, deletes or overwrites a document. Document expiry follows the Phase 4 lifecycle rule; renewal only creates follow-up tracking and does not generate a legal document.
