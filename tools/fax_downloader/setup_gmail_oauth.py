from __future__ import annotations

import json
import os
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CLIENT_SECRET_FILE = SCRIPT_DIR / "gmail_client_secret.json"
DEFAULT_TOKEN_FILE = SCRIPT_DIR / "gmail_token.json"
GMAIL_IMAP_SCOPE = ["https://mail.google.com/"]


def _resolve_path(raw: str | None, default_path: Path) -> Path:
    value = (raw or "").strip().strip('"').strip("'")
    p = Path(value) if value else default_path
    if not p.is_absolute():
        candidate_script = (SCRIPT_DIR / p).resolve()
        candidate_repo = (SCRIPT_DIR.parent.parent / p).resolve()
        if candidate_script.exists() or not candidate_repo.exists():
            p = candidate_script
        else:
            p = candidate_repo
    return p


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "Missing dependency: google-auth-oauthlib.\n"
            "Install with: pip install google-auth google-auth-oauthlib"
        )
        return 2

    email_user = (os.environ.get("FAX_DOWNLOADER_EMAIL_USER") or "").strip()
    if not email_user:
        print("Set FAX_DOWNLOADER_EMAIL_USER before running OAuth setup.")
        return 2

    auth_mode = (os.environ.get("FAX_DOWNLOADER_AUTH_MODE") or "oauth").strip().lower()
    if auth_mode != "oauth":
        print("Warning: FAX_DOWNLOADER_AUTH_MODE is not 'oauth'. Token setup will still proceed.")

    client_secret_file = _resolve_path(
        os.environ.get("FAX_DOWNLOADER_OAUTH_CLIENT_SECRET_FILE"),
        DEFAULT_CLIENT_SECRET_FILE,
    )
    token_file = _resolve_path(
        os.environ.get("FAX_DOWNLOADER_OAUTH_TOKEN_FILE"),
        DEFAULT_TOKEN_FILE,
    )

    if not client_secret_file.exists():
        print(
            "Client secret file not found:\n"
            f"{client_secret_file}\n\n"
            "Create an OAuth Desktop App in Google Cloud and download the JSON to this path,\n"
            "or set FAX_DOWNLOADER_OAUTH_CLIENT_SECRET_FILE in .env."
        )
        return 2

    print(f"Starting OAuth browser flow for {email_user} ...")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), GMAIL_IMAP_SCOPE)
    creds = flow.run_local_server(host="127.0.0.1", port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")

    # Safety check: verify saved JSON looks like user credentials.
    try:
        payload = json.loads(token_file.read_text(encoding="utf-8"))
        if not payload.get("refresh_token"):
            print("Warning: token saved but no refresh_token present.")
    except Exception:
        pass

    print(f"OAuth token saved to: {token_file}")
    print("You can now run fax_downloader.py with FAX_DOWNLOADER_AUTH_MODE=oauth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
