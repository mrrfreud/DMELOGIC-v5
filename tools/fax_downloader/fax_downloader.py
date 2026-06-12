from __future__ import annotations

import imaplib
import email
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from email.header import decode_header
from pathlib import Path
from typing import Iterable


LOG = logging.getLogger("fax_downloader")
_SCRIPT_DIR = Path(__file__).resolve().parent
_GMAIL_IMAP_SCOPE = "https://mail.google.com/"


def _setup_logging() -> None:
    level = os.environ.get("FAX_DOWNLOADER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


@dataclass(frozen=True)
class Config:
    email_user: str
    auth_mode: str
    email_pass: str
    oauth_token_file: Path
    imap_server: str
    save_folder: Path
    search_criteria: str
    mailbox: str
    mark_deleted: bool


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _resolve_path(raw_value: str | None, default_path: Path) -> Path:
    value = (raw_value or "").strip().strip('"').strip("'")
    p = Path(value) if value else default_path
    if not p.is_absolute():
        candidate_script = (_SCRIPT_DIR / p).resolve()
        candidate_repo = (_SCRIPT_DIR.parent.parent / p).resolve()
        if candidate_script.exists() or not candidate_repo.exists():
            p = candidate_script
        else:
            p = candidate_repo
    return p


def load_config() -> Config:
    email_user = _required_env("FAX_DOWNLOADER_EMAIL_USER")
    auth_mode = (os.environ.get("FAX_DOWNLOADER_AUTH_MODE") or "password").strip().lower()
    email_pass = (os.environ.get("FAX_DOWNLOADER_EMAIL_PASS") or "").strip()
    oauth_token_file = _resolve_path(
        os.environ.get("FAX_DOWNLOADER_OAUTH_TOKEN_FILE"),
        _SCRIPT_DIR / "gmail_token.json",
    )
    imap_server = (os.environ.get("FAX_DOWNLOADER_IMAP_SERVER") or "imap.gmail.com").strip()
    save_folder = Path(
        (os.environ.get("FAX_DOWNLOADER_SAVE_FOLDER") or r"C:\ProgramData\DMELogic\New Rx").strip()
    )
    search_criteria = (
        os.environ.get("FAX_DOWNLOADER_SEARCH_CRITERIA")
        or '(UNSEEN FROM "RingCentral" SUBJECT "New Fax Message from")'
    )
    mailbox = (os.environ.get("FAX_DOWNLOADER_MAILBOX") or "inbox").strip()
    mark_deleted_raw = (os.environ.get("FAX_DOWNLOADER_MARK_DELETED") or "1").strip().lower()
    mark_deleted = mark_deleted_raw in {"1", "true", "yes", "y", "on"}

    if auth_mode not in {"password", "oauth"}:
        raise RuntimeError("FAX_DOWNLOADER_AUTH_MODE must be 'password' or 'oauth'")
    if auth_mode == "password" and not email_pass:
        raise RuntimeError("Missing required environment variable: FAX_DOWNLOADER_EMAIL_PASS")

    return Config(
        email_user=email_user,
        auth_mode=auth_mode,
        email_pass=email_pass,
        oauth_token_file=oauth_token_file,
        imap_server=imap_server,
        save_folder=save_folder,
        search_criteria=search_criteria,
        mailbox=mailbox,
        mark_deleted=mark_deleted,
    )


def _decode_mime_words(value: str | None) -> str:
    if not value:
        return ""
    decoded_parts: list[str] = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            enc = encoding or "utf-8"
            try:
                decoded_parts.append(chunk.decode(enc, errors="replace"))
            except LookupError:
                decoded_parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(chunk)
    return "".join(decoded_parts)


def _safe_filename(name: str) -> str:
    name = _decode_mime_words(name).strip()
    if not name:
        return "attachment"

    # Keep this Windows-safe while preserving readability.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "attachment"


def _iter_attachments(msg: email.message.Message) -> Iterable[tuple[str, bytes]]:
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue

        disposition = part.get("Content-Disposition") or ""
        filename = part.get_filename()
        if not filename and "attachment" not in disposition.lower():
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        yield _safe_filename(filename or "attachment"), payload


def _dedupe_path(target_dir: Path, filename: str) -> Path:
    base = Path(filename)
    stem = base.stem or "attachment"
    suffix = base.suffix

    candidate = target_dir / f"{stem}{suffix}"
    idx = 1
    while candidate.exists():
        candidate = target_dir / f"{stem}_{idx}{suffix}"
        idx += 1
    return candidate


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        temp_name = tmp.name
    Path(temp_name).replace(path)


def _load_oauth_access_token(token_file: Path) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "OAuth mode requires google-auth and google-auth-oauthlib. "
            "Install: pip install google-auth google-auth-oauthlib"
        ) from exc

    if not token_file.exists():
        raise RuntimeError(
            f"OAuth token file not found: {token_file}. "
            "Run setup_gmail_oauth.py once to create it."
        )

    try:
        creds = Credentials.from_authorized_user_file(
            str(token_file),
            scopes=[_GMAIL_IMAP_SCOPE],
        )
    except Exception as exc:
        raise RuntimeError(f"Could not read OAuth token file {token_file}: {exc}") from exc

    try:
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_file.parent.mkdir(parents=True, exist_ok=True)
                token_file.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise RuntimeError(
                    "OAuth token is invalid and has no refresh token. "
                    "Re-run setup_gmail_oauth.py."
                )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to refresh OAuth token: {exc}") from exc

    if not creds.token:
        raise RuntimeError("OAuth auth could not obtain an access token.")
    return creds.token


def _auth_with_password(mail: imaplib.IMAP4_SSL, cfg: Config) -> None:
    auth_error: imaplib.IMAP4.error | None = None
    candidates: list[str] = []
    for value in (cfg.email_pass, "".join(cfg.email_pass.split())):
        if value and value not in candidates:
            candidates.append(value)

    for password in candidates:
        try:
            mail.login(cfg.email_user, password)
            return
        except imaplib.IMAP4.error as exc:
            auth_error = exc

    if auth_error is None:
        raise RuntimeError(f"IMAP login failed for {cfg.email_user}: no password value was provided")

    msg = str(auth_error)
    if "AUTHENTICATIONFAILED" in msg.upper():
        raise RuntimeError(
            f"IMAP auth failed for {cfg.email_user}. "
            "Verify the configured password is valid for IMAP for this same mailbox."
        )
    raise RuntimeError(f"IMAP login failed for {cfg.email_user}: {msg}")


def _auth_with_oauth(mail: imaplib.IMAP4_SSL, cfg: Config) -> None:
    access_token = _load_oauth_access_token(cfg.oauth_token_file)
    xoauth = f"user={cfg.email_user}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")
    try:
        status, _ = mail.authenticate("XOAUTH2", lambda _: xoauth)
    except imaplib.IMAP4.error as exc:
        raise RuntimeError(f"IMAP OAuth auth failed for {cfg.email_user}: {exc}") from exc
    if status != "OK":
        raise RuntimeError(f"IMAP OAuth auth failed for {cfg.email_user}: server returned {status}")


def _authenticate(mail: imaplib.IMAP4_SSL, cfg: Config) -> None:
    if cfg.auth_mode == "oauth":
        _auth_with_oauth(mail, cfg)
    else:
        _auth_with_password(mail, cfg)


def download_faxes() -> int:
    _setup_logging()

    try:
        cfg = load_config()
    except RuntimeError as exc:
        LOG.error("Configuration error: %s", exc)
        return 2

    cfg.save_folder.mkdir(parents=True, exist_ok=True)

    saved_files = 0
    processed_messages = 0

    mail: imaplib.IMAP4_SSL | None = None
    try:
        mail = imaplib.IMAP4_SSL(cfg.imap_server)
        try:
            _authenticate(mail, cfg)
        except RuntimeError as exc:
            LOG.error("%s", exc)
            return 1

        status, _ = mail.select(cfg.mailbox)
        if status != "OK":
            LOG.error("Failed to select mailbox: %s", cfg.mailbox)
            return 1

        status, data = mail.search(None, cfg.search_criteria)
        if status != "OK":
            LOG.error("Search failed with criteria: %s", cfg.search_criteria)
            return 1

        if not data or not data[0]:
            LOG.info("No matching unread fax emails found.")
            return 0

        for mail_id in data[0].split():
            fetch_status, fetch_data = mail.fetch(mail_id, "(RFC822)")
            if fetch_status != "OK" or not fetch_data or fetch_data[0] is None:
                LOG.warning("Skipping message %s due to fetch failure.", mail_id)
                continue

            raw_email = fetch_data[0][1]
            if not isinstance(raw_email, (bytes, bytearray)):
                LOG.warning("Skipping message %s due to unexpected payload type.", mail_id)
                continue

            msg = email.message_from_bytes(raw_email)
            attachment_count = 0

            for filename, payload in _iter_attachments(msg):
                local_path = _dedupe_path(cfg.save_folder, filename)
                _atomic_write(local_path, payload)
                attachment_count += 1
                saved_files += 1
                LOG.info("Saved attachment: %s", local_path)

            if attachment_count == 0:
                LOG.info("No attachments found in message %s; leaving message state unchanged.", mail_id)
                continue

            processed_messages += 1
            if cfg.mark_deleted:
                mail.store(mail_id, "+FLAGS", "\\Deleted")
            else:
                mail.store(mail_id, "+FLAGS", "\\Seen")

        if cfg.mark_deleted and processed_messages > 0:
            mail.expunge()

        LOG.info("Complete. Processed %d email(s), saved %d attachment(s).", processed_messages, saved_files)
        return 0

    except Exception:
        LOG.exception("Fax download failed.")
        return 1
    finally:
        if mail is not None:
            try:
                mail.close()
            except Exception:
                pass
            try:
                mail.logout()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(download_faxes())
