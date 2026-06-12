# Fax Downloader Automation (Windows)

This folder implements a silent, scheduled fax attachment downloader using native Windows Task Scheduler.

## Files

- `fax_downloader.py`: Connects to IMAP, downloads matching unread fax attachments, writes into `New Rx`, and marks messages seen/deleted.
- `.env.example`: Template for configuration.
- `run_fax_downloader.ps1`: Loads `.env`, runs the Python script from this repo venv, appends logs.
- `run_silently.vbs`: Launches the PowerShell wrapper hidden (no console popup).
- `install_fax_downloader_task.ps1`: Creates/updates a repeating Scheduled Task.

## 1) Configure credentials and destination

1. Copy `.env.example` to `.env` in this same folder.
2. Fill in:
   - `FAX_DOWNLOADER_EMAIL_USER`
   - `FAX_DOWNLOADER_EMAIL_PASS` (password mode only)
3. Adjust optional values as needed:
   - `FAX_DOWNLOADER_AUTH_MODE` (`password` or `oauth`)
   - `FAX_DOWNLOADER_OAUTH_CLIENT_SECRET_FILE` (OAuth mode only)
   - `FAX_DOWNLOADER_OAUTH_TOKEN_FILE` (OAuth mode only)
   - `FAX_DOWNLOADER_SAVE_FOLDER` (default: `C:\ProgramData\DMELogic\New Rx`)
   - `FAX_DOWNLOADER_SEARCH_CRITERIA`
   - `FAX_DOWNLOADER_MARK_DELETED`

## OAuth setup (recommended for Gmail)

1. Install OAuth dependencies in your venv:

```powershell
pip install google-auth google-auth-oauthlib
```

2. In Google Cloud Console, create an OAuth Desktop App credential and download
   the JSON file to:

```text
tools\fax_downloader\gmail_client_secret.json
```

3. In `.env`, set:

```text
FAX_DOWNLOADER_AUTH_MODE=oauth
FAX_DOWNLOADER_EMAIL_USER=your_mailbox@gmail.com
```

4. Run one-time OAuth token bootstrap:

```powershell
python .\tools\fax_downloader\setup_gmail_oauth.py
```

5. Complete the browser consent prompt. A token file is created at
   `tools\fax_downloader\gmail_token.json`.

## 2) Test once manually

Run in PowerShell from repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\fax_downloader\run_fax_downloader.ps1
```

Check log file:

- `tools\fax_downloader\fax_downloader_task.log`

## 3) Install the 30-minute scheduled task

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\fax_downloader\install_fax_downloader_task.ps1
```

Default task name: `Automated Fax Downloader`

Optional flags:

- `-RepeatMinutes 30`
- `-CurrentUserOnly` (run only while logged in)
- `-NoHighestPrivileges` (disable elevated run level)

## 4) Verify in Task Scheduler

- Open Task Scheduler.
- Locate task `Automated Fax Downloader`.
- Use **Run** to test.
- Confirm new files are saved to your configured `New Rx` folder.

## Notes

- This implementation uses environment variables from `.env` instead of hardcoding secrets in source.
- Duplicate attachment names are preserved by auto-suffixing (`name_1.pdf`, `name_2.pdf`, ...).
- Attachments are saved with atomic writes to reduce partial-file risk.
- If `FAX_DOWNLOADER_MARK_DELETED=1`, processed messages are deleted and expunged; otherwise messages are marked as read.

## Alternative: Google Sheets + Apps Script (no local IMAP OAuth)

If Gmail IMAP OAuth is blocked or unreliable in your environment, use the Apps Script flow instead:

- See `tools/fax_downloader/GOOGLE_APPS_SCRIPT_SETUP.md`
- Script source: `tools/fax_downloader/google_apps_script_fax_only.gs`

That workflow runs inside your Google account, saves fax attachments to Drive, keeps the legacy `saveFaxesAndCleanInbox` entry point, and removes Google OCR so DMELogic can OCR locally.
