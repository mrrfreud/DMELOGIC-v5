# Google Sheets + Apps Script Fax Intake (Simplified, No Google OCR)

This is the simplified replacement for the older two-step script.
It keeps your same function names and behavior goal:

- Save RingCentral fax attachments from Gmail
- Clean inbox items after save
- No Google OCR step (DMELogic 5 performs OCR locally)

## Target local folder

You said your current target is:

C:\ProgramData\DMELogic5\NEW ORDERS

Apps Script cannot write directly to a Windows local path.
The bridge is:

1. Apps Script saves into a Google Drive folder named NEW ORDERS.
2. Google Drive for desktop syncs that folder to Windows.
3. DMELogic watches the synced local path (or a junction pointing to it).

## Script to use

Use this file as your code source:

tools/fax_downloader/google_apps_script_fax_only.gs

Paste all contents into your Apps Script Code.gs.

## Why this version is safer and simpler

- No Advanced Drive OCR API usage
- No Drive.Files.insert dependency
- No second OCR pass
- Keeps old entry points:
  - saveFaxesAndCleanInbox
  - ocrAndMoveFaxes (now a compatibility wrapper that runs save only)

## Setup steps

1. Create or identify the Google Drive folder you want (recommended name: NEW ORDERS).
2. Open your Google Sheet, then Extensions > Apps Script.
3. Replace Code.gs with the script from tools/fax_downloader/google_apps_script_fax_only.gs.
4. In FAX_SETTINGS, set one of:
   - targetFolderId (recommended), or
   - targetFolderName = 'NEW ORDERS'
5. Run saveFaxesAndCleanInbox once manually and approve permissions.
6. Add a time-driven trigger for saveFaxesAndCleanInbox every 5 to 10 minutes.

## Keep compatibility with old triggers

If an existing trigger still points to ocrAndMoveFaxes, it will still work.
That function now logs that OCR is disabled and calls saveFaxesAndCleanInbox.

## Google Drive sync to local path

If you must keep C:\ProgramData\DMELogic5\NEW ORDERS exactly, use one of these:

- Option A: Point DMELogic to the Drive-synced folder path directly.
- Option B: Create a junction from C:\ProgramData\DMELogic5\NEW ORDERS to the synced folder path.

Example junction command (run elevated, adjust target path):

cmd /c mklink /J "C:\ProgramData\DMELogic5\NEW ORDERS" "C:\Users\<YourUser>\My Drive\NEW ORDERS"

## Recommended Gmail query (already in script)

is:unread from:RingCentral subject:"New Fax Message from" has:attachment -label:DMELogic/Processed

## Operational notes

- Messages are moved to Trash after successful save (matches old behavior).
- Files are renamed with timestamp + sender + original name for easier traceability.
- Unsupported extensions are skipped.
- Script lock prevents overlapping runs from stepping on each other.

## If no files appear

1. Confirm targetFolderId is correct.
2. Temporarily broaden query to: is:unread has:attachment
3. Run manually and inspect Execution Logs.
4. Confirm Drive desktop is signed in and syncing.
