/**
 * DMELogic Fax Intake (Gmail -> Drive)
 *
 * Keeps the familiar function names:
 * - saveFaxesAndCleanInbox()
 * - ocrAndMoveFaxes()  // compatibility wrapper; OCR is intentionally disabled
 *
 * This script only downloads fax attachments and cleans inbox items.
 * DMELogic 5 performs OCR locally after files land in NEW ORDERS.
 */

const FAX_SETTINGS = {
  // Preferred: set this to the exact Google Drive folder ID for NEW ORDERS.
  // Example URL: https://drive.google.com/drive/folders/<FOLDER_ID>
  targetFolderId: '1M-TUknHF90_8A_d5sbIUHOnxt66imLUx',

  // Fallback if targetFolderId is blank.
  targetFolderName: 'NEW ORDERS',

  // RingCentral fax search. Keep unread+attachment to avoid noise.
  gmailQuery: 'is:unread from:RingCentral subject:"New Fax Message from" has:attachment -label:DMELogic/Processed',

  // Optional label to avoid reprocessing if messages are not trashed.
  processedLabel: 'DMELogic/Processed',

  // Keep old behavior: delete message thread after successful save.
  deleteThreadAfterSave: true,

  // If not deleting, mark read to prevent repeated pickups.
  markReadWhenNotDeleting: true,

  // Allowed attachment extensions. Empty array means allow all.
  allowedExtensions: ['pdf', 'tif', 'tiff', 'jpg', 'jpeg', 'png'],

  // Safety cap per run.
  maxThreadsPerRun: 50
};

function saveFaxesAndCleanInbox() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(25000)) {
    Logger.log('Another run is active; skipping this cycle.');
    return;
  }

  try {
    const targetFolder = resolveTargetFolder_();
    const processedLabelObj = getOrCreateLabel_(FAX_SETTINGS.processedLabel);
    const tz = Session.getScriptTimeZone();

    const threads = GmailApp.search(FAX_SETTINGS.gmailQuery, 0, FAX_SETTINGS.maxThreadsPerRun);

    let savedFiles = 0;
    let skippedFiles = 0;
    let failedFiles = 0;
    let touchedThreads = 0;

    threads.forEach((thread) => {
      let threadSavedAny = false;
      const messages = thread.getMessages();

      messages.forEach((message) => {
        const attachments = message.getAttachments({
          includeInlineImages: false,
          includeAttachments: true
        });

        attachments.forEach((attachment) => {
          const originalName = attachment.getName() || 'fax_attachment';
          const ext = extensionOf_(originalName);

          if (FAX_SETTINGS.allowedExtensions.length > 0 && ext && FAX_SETTINGS.allowedExtensions.indexOf(ext) === -1) {
            skippedFiles += 1;
            Logger.log('Skipped unsupported extension: %s', originalName);
            return;
          }

          try {
            const savedName = buildSavedName_(message, originalName, tz);
            const blob = attachment.copyBlob().setName(savedName);
            targetFolder.createFile(blob);

            savedFiles += 1;
            threadSavedAny = true;
            Logger.log('Saved: %s', savedName);
          } catch (err) {
            failedFiles += 1;
            Logger.log('Failed to save "%s": %s', originalName, String(err));
          }
        });
      });

      if (!threadSavedAny) {
        return;
      }

      touchedThreads += 1;

      if (processedLabelObj) {
        thread.addLabel(processedLabelObj);
      }

      if (FAX_SETTINGS.deleteThreadAfterSave) {
        thread.moveToTrash();
      } else if (FAX_SETTINGS.markReadWhenNotDeleting) {
        thread.markRead();
      }
    });

    Logger.log(
      'Done. ThreadsScanned=%s ThreadsTouched=%s Saved=%s Skipped=%s Failed=%s',
      threads.length,
      touchedThreads,
      savedFiles,
      skippedFiles,
      failedFiles
    );
  } finally {
    lock.releaseLock();
  }
}

function ocrAndMoveFaxes() {
  // Compatibility wrapper for old trigger names. OCR is intentionally removed.
  Logger.log('Google OCR step removed. Running saveFaxesAndCleanInbox only.');
  saveFaxesAndCleanInbox();
}

function runFaxPipeline() {
  saveFaxesAndCleanInbox();
}

function resolveTargetFolder_() {
  const folderId = String(FAX_SETTINGS.targetFolderId || '').trim();
  if (folderId) {
    return DriveApp.getFolderById(folderId);
  }

  const folderName = String(FAX_SETTINGS.targetFolderName || '').trim();
  if (!folderName) {
    throw new Error('Set FAX_SETTINGS.targetFolderId or targetFolderName.');
  }

  const folders = DriveApp.getFoldersByName(folderName);
  if (!folders.hasNext()) {
    throw new Error('Target Drive folder not found: ' + folderName);
  }

  return folders.next();
}

function getOrCreateLabel_(labelName) {
  const clean = String(labelName || '').trim();
  if (!clean) return null;
  return GmailApp.getUserLabelByName(clean) || GmailApp.createLabel(clean);
}

function buildSavedName_(message, originalName, tz) {
  const stamp = Utilities.formatDate(message.getDate(), tz, 'yyyyMMdd_HHmmss');
  const sender = sanitizeForFile_(extractSender_(message.getFrom()) || 'sender');
  const safeOriginal = sanitizeForFile_(originalName || 'fax_attachment');
  return stamp + '_' + sender + '_' + safeOriginal;
}

function extractSender_(fromText) {
  const raw = String(fromText || '').trim();
  if (!raw) return '';
  return raw.replace(/<[^>]*>/g, '').replace(/["']/g, '').trim();
}

function extensionOf_(name) {
  const idx = String(name || '').lastIndexOf('.');
  if (idx < 0) return '';
  return String(name).substring(idx + 1).toLowerCase();
}

function sanitizeForFile_(text) {
  const clean = String(text || '')
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');

  return clean.substring(0, 120) || 'fax_attachment';
}
