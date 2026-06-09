"""
RingCentral Integration Service

Provides SMS, Fax, and Click-to-Call functionality via RingCentral REST API.
Implements 3-legged OAuth flow with secure token storage.

Environment Variables (optional, can also be configured in Settings):
    RINGCENTRAL_CLIENT_ID: OAuth client ID
    RINGCENTRAL_CLIENT_SECRET: OAuth client secret
    RINGCENTRAL_SERVER_URL: API server (https://platform.ringcentral.com or .devtest.com)
    RINGCENTRAL_REDIRECT_URI: OAuth callback (default: http://127.0.0.1:8765/callback)

Security:
    - Tokens stored in Windows Credential Manager (via keyring)
    - Fallback to encrypted JSON if keyring unavailable
    - No secrets stored in plaintext
"""

import os
import json
import time
import base64
import hashlib
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, Callable, List, Tuple, Union
from urllib.parse import urlencode, parse_qs, urlparse
from pathlib import Path

import requests

from dmelogic.config import debug_log


# -----------------------------------------------------------------------------
# Token Storage (Windows Credential Manager with fallback)
# -----------------------------------------------------------------------------

KEYRING_SERVICE = "DMELogic_RingCentral"
KEYRING_USERNAME = "oauth_tokens"

# Token file location for fallback
def _get_token_file() -> Path:
    """Get encrypted token file path."""
    if os.name == 'nt':
        local_appdata = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        token_dir = Path(local_appdata) / "DMELogic" / "secure"
    else:
        token_dir = Path.home() / ".dmelogic" / "secure"
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / "rc_tokens.enc"


def _get_machine_key() -> bytes:
    """Generate a machine-specific encryption key."""
    machine_id = (
        os.environ.get('COMPUTERNAME', 'computer') +
        os.environ.get('USERDOMAIN', 'domain') +
        os.environ.get('USERNAME', 'user') +
        os.environ.get('PROCESSOR_IDENTIFIER', 'cpu')
    )
    return hashlib.sha256(machine_id.encode()).digest()


def _simple_encrypt(data: str) -> str:
    """Simple XOR encryption with machine key (better than plaintext)."""
    key = _get_machine_key()
    data_bytes = data.encode('utf-8')
    encrypted = bytearray(len(data_bytes))

    for idx, byte in enumerate(data_bytes):
        encrypted[idx] = byte ^ key[idx % len(key)]

    return base64.b64encode(encrypted).decode('ascii')


def _simple_decrypt(encrypted: str) -> str:
    """Decrypt text that was produced by _simple_encrypt."""
    key = _get_machine_key()
    encrypted_bytes = base64.b64decode(encrypted.encode('ascii'))
    data_bytes = bytearray(len(encrypted_bytes))

    for idx, byte in enumerate(encrypted_bytes):
        data_bytes[idx] = byte ^ key[idx % len(key)]

    return data_bytes.decode('utf-8')


class TokenStorage:
    """Helper for persisting OAuth tokens securely."""

    @staticmethod
    def save_tokens(tokens: Dict[str, Any]) -> bool:
        """Persist OAuth tokens using keyring with file fallback."""
        tokens_json = json.dumps(tokens)

        # Try Windows Credential Manager via keyring first
        try:
            import keyring  # type: ignore[import-not-found]
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, tokens_json)
            debug_log("RingCentral tokens saved to Windows Credential Manager")
            return True
        except ImportError:
            debug_log("keyring not available, using encrypted file fallback")
        except Exception as exc:
            debug_log(f"keyring save failed: {exc}, using fallback")

        # Fallback: encrypt and store on disk
        try:
            encrypted = _simple_encrypt(tokens_json)
            token_file = _get_token_file()
            token_file.write_text(encrypted, encoding='ascii')
            debug_log(f"RingCentral tokens saved to encrypted file: {token_file}")
            return True
        except Exception as exc:
            debug_log(f"Failed to save tokens: {exc}")
            return False

    @staticmethod
    def load_tokens() -> Optional[Dict[str, Any]]:
        """Retrieve stored OAuth tokens from keyring or encrypted file."""
        try:
            import keyring  # type: ignore[import-not-found]
            tokens_json = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
            if tokens_json:
                debug_log("RingCentral tokens loaded from Windows Credential Manager")
                return json.loads(tokens_json)
        except ImportError:
            pass
        except Exception as exc:
            debug_log(f"keyring load failed: {exc}")

        try:
            token_file = _get_token_file()
            if token_file.exists():
                encrypted = token_file.read_text(encoding='ascii')
                tokens_json = _simple_decrypt(encrypted)
                debug_log("RingCentral tokens loaded from encrypted file")
                return json.loads(tokens_json)
        except Exception as exc:
            debug_log(f"Failed to load tokens from file: {exc}")

        return None

    @staticmethod
    def delete_tokens() -> bool:
        """Remove stored OAuth tokens from all backends."""
        success = False

        try:
            import keyring  # type: ignore[import-not-found]
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
            success = True
        except ImportError:
            pass
        except Exception:
            pass

        try:
            token_file = _get_token_file()
            if token_file.exists():
                token_file.unlink()
                success = True
        except Exception:
            pass

        return success


# -----------------------------------------------------------------------------
# OAuth Callback Server
# -----------------------------------------------------------------------------

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to receive OAuth callback."""
    
    authorization_code: Optional[str] = None
    error: Optional[str] = None
    state: Optional[str] = None
    
    def log_message(self, format, *args):
        """Suppress HTTP server logs."""
        pass
    
    def do_GET(self):
        """Handle OAuth callback GET request."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        if 'code' in params:
            OAuthCallbackHandler.authorization_code = params['code'][0]
            OAuthCallbackHandler.state = params.get('state', [None])[0]
            self._send_success_response()
        elif 'error' in params:
            OAuthCallbackHandler.error = params.get('error_description', params['error'])[0]
            self._send_error_response(OAuthCallbackHandler.error)
        else:
            self._send_error_response("No authorization code received")
    
    def _send_success_response(self):
        """Send success HTML page."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Authorization Successful</title></head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h1 style="color: #28a745;">✓ Authorization Successful</h1>
            <p>RingCentral has been connected to DMELogic.</p>
            <p>You can close this window and return to the application.</p>
            <script>setTimeout(function() { window.close(); }, 3000);</script>
        </body>
        </html>
        """
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def _send_error_response(self, error: str):
        """Send error HTML page."""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Authorization Failed</title></head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h1 style="color: #dc3545;">✗ Authorization Failed</h1>
            <p>Error: {error}</p>
            <p>Please close this window and try again.</p>
        </body>
        </html>
        """
        self.send_response(400)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())


# -----------------------------------------------------------------------------
# RingCentral Service
# -----------------------------------------------------------------------------

@dataclass
class RingCentralConfig:
    """RingCentral API configuration."""
    client_id: str
    client_secret: str
    server_url: str = "https://platform.ringcentral.com"
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    
    @classmethod
    def from_env(cls) -> Optional['RingCentralConfig']:
        """Load configuration from environment variables."""
        client_id = os.environ.get('RINGCENTRAL_CLIENT_ID')
        client_secret = os.environ.get('RINGCENTRAL_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            return None
        
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            server_url=os.environ.get('RINGCENTRAL_SERVER_URL', 'https://platform.ringcentral.com'),
            redirect_uri=os.environ.get('RINGCENTRAL_REDIRECT_URI', 'http://127.0.0.1:8765/callback')
        )
    
    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> Optional['RingCentralConfig']:
        """Load configuration from application settings."""
        rc_settings = settings.get('ringcentral', {})
        client_id = rc_settings.get('client_id')
        client_secret = rc_settings.get('client_secret')
        
        if not client_id or not client_secret:
            return None
        
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            server_url=rc_settings.get('server_url', 'https://platform.ringcentral.com'),
            redirect_uri=rc_settings.get('redirect_uri', 'http://127.0.0.1:8765/callback')
        )


class RingCentralService:
    """
    RingCentral API integration service.
    
    Provides:
    - OAuth 3-legged authorization flow
    - SMS sending (A2P SMS)
    - Fax sending
    - Click-to-call (RingOut)
    - Token refresh management
    
    Usage:
        service = RingCentralService(config)
        
        # Connect (opens browser for OAuth)
        if service.authorize():
            # Send SMS
            result = service.send_sms("+15551234567", "Hello from DMELogic!")
            
            # Send Fax
            result = service.send_fax("+15559876543", pdf_path, cover_text="Patient docs")
            
            # Initiate call
            result = service.initiate_call("+15551234567")
    """
    
    # OAuth scopes - empty list lets RingCentral use app's default permissions
    SCOPES = []
    
    def __init__(self, config: RingCentralConfig):
        """Initialize RingCentral service."""
        self.config = config
        self._tokens: Optional[Dict[str, Any]] = None
        self._token_expiry: Optional[datetime] = None
        self._callback_server: Optional[HTTPServer] = None
        self._oauth_state: Optional[str] = None
        
        # Load existing tokens
        self._load_tokens()
    
    @property
    def is_connected(self) -> bool:
        """Check if we have valid (or refreshable) tokens."""
        if not self._tokens:
            return False
        
        # Check if we have a refresh token (can get new access token)
        return bool(self._tokens.get('refresh_token'))
    
    @property
    def access_token(self) -> Optional[str]:
        """Get valid access token, refreshing if needed."""
        if not self._tokens:
            return None
        
        # Check if token is expired
        if self._token_expiry and datetime.now() >= self._token_expiry:
            if not self._refresh_token():
                return None
        
        return self._tokens.get('access_token')
    
    def _load_tokens(self):
        """Load tokens from secure storage."""
        tokens = TokenStorage.load_tokens()
        if tokens:
            self._tokens = tokens
            # Calculate expiry from stored timestamp
            expires_at = tokens.get('expires_at')
            if expires_at:
                self._token_expiry = datetime.fromisoformat(expires_at)
            debug_log("RingCentral: Loaded existing tokens")
    
    def _save_tokens(self, tokens: Dict[str, Any]):
        """Save tokens to secure storage."""
        # Add expiry timestamp
        expires_in = tokens.get('expires_in', 3600)
        tokens['expires_at'] = (datetime.now() + timedelta(seconds=expires_in - 60)).isoformat()
        
        self._tokens = tokens
        self._token_expiry = datetime.fromisoformat(tokens['expires_at'])
        TokenStorage.save_tokens(tokens)
    
    def _get_auth_header(self) -> Dict[str, str]:
        """Get authorization header for API requests."""
        token = self.access_token
        if not token:
            raise RuntimeError("Not authenticated with RingCentral")
        return {"Authorization": f"Bearer {token}"}
    
    def _get_basic_auth(self) -> str:
        """Get Basic auth header for token requests."""
        credentials = f"{self.config.client_id}:{self.config.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"
    
    def get_authorization_url(self) -> str:
        """Generate OAuth authorization URL."""
        self._oauth_state = secrets.token_urlsafe(32)
        
        params = {
            'response_type': 'code',
            'client_id': self.config.client_id,
            'redirect_uri': self.config.redirect_uri,
            'state': self._oauth_state,
            'scope': ' '.join(self.SCOPES)
        }
        
        auth_url = f"{self.config.server_url}/restapi/oauth/authorize?{urlencode(params)}"
        debug_log(f"RingCentral: Authorization URL generated")
        return auth_url
    
    def authorize(self, timeout: int = 120, open_browser: bool = True) -> bool:
        """
        Perform 3-legged OAuth authorization.
        
        Opens browser for user to authorize, then captures the callback.
        
        Args:
            timeout: Seconds to wait for callback
            open_browser: Whether to automatically open browser
            
        Returns:
            True if authorization successful
        """
        # Parse redirect URI to get port
        parsed = urlparse(self.config.redirect_uri)
        port = parsed.port or 8765
        
        # Reset callback handler state
        OAuthCallbackHandler.authorization_code = None
        OAuthCallbackHandler.error = None
        OAuthCallbackHandler.state = None
        
        # Start callback server
        try:
            server = HTTPServer(('127.0.0.1', port), OAuthCallbackHandler)
            server.timeout = 1  # Check every second
        except OSError as e:
            debug_log(f"RingCentral: Failed to start callback server: {e}")
            return False
        
        # Open browser to authorization URL
        auth_url = self.get_authorization_url()
        if open_browser:
            webbrowser.open(auth_url)
        
        # Wait for callback
        start_time = time.time()
        try:
            while time.time() - start_time < timeout:
                server.handle_request()
                
                if OAuthCallbackHandler.authorization_code:
                    # Verify state
                    if OAuthCallbackHandler.state != self._oauth_state:
                        debug_log("RingCentral: OAuth state mismatch")
                        return False
                    
                    # Exchange code for tokens
                    return self._exchange_code(OAuthCallbackHandler.authorization_code)
                
                if OAuthCallbackHandler.error:
                    debug_log(f"RingCentral: OAuth error: {OAuthCallbackHandler.error}")
                    return False
        finally:
            server.server_close()
        
        debug_log("RingCentral: OAuth timeout")
        return False
    
    def authorize_async(self, callback: Callable[[bool, str], None], timeout: int = 120) -> None:
        """
        Perform OAuth authorization in background thread.
        
        Args:
            callback: Function to call with (success, error_message)
            timeout: Seconds to wait
        """
        def _auth_thread():
            try:
                success = self.authorize(timeout=timeout, open_browser=True)
                if success:
                    callback(True, "")
                else:
                    error = OAuthCallbackHandler.error or "Authorization cancelled or timed out"
                    callback(False, error)
            except Exception as e:
                callback(False, str(e))
        
        thread = threading.Thread(target=_auth_thread, daemon=True)
        thread.start()
    
    def _exchange_code(self, code: str) -> bool:
        """Exchange authorization code for tokens."""
        url = f"{self.config.server_url}/restapi/oauth/token"
        
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.config.redirect_uri
        }
        
        headers = {
            'Authorization': self._get_basic_auth(),
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        try:
            response = requests.post(url, data=data, headers=headers, timeout=30)
            response.raise_for_status()
            
            tokens = response.json()
            self._save_tokens(tokens)
            debug_log("RingCentral: Successfully obtained tokens")
            return True
            
        except requests.RequestException as e:
            debug_log(f"RingCentral: Token exchange failed: {e}")
            return False
    
    def _refresh_token(self) -> bool:
        """Refresh access token using refresh token."""
        if not self._tokens or not self._tokens.get('refresh_token'):
            return False
        
        url = f"{self.config.server_url}/restapi/oauth/token"
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self._tokens['refresh_token']
        }
        
        headers = {
            'Authorization': self._get_basic_auth(),
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        try:
            response = requests.post(url, data=data, headers=headers, timeout=30)
            response.raise_for_status()
            
            tokens = response.json()
            self._save_tokens(tokens)
            debug_log("RingCentral: Token refreshed")
            return True
            
        except requests.RequestException as e:
            debug_log(f"RingCentral: Token refresh failed: {e}")
            # Token may be completely expired - need re-authorization
            self._tokens = None
            self._token_expiry = None
            return False
    
    def disconnect(self) -> bool:
        """
        Disconnect from RingCentral (revoke tokens and clear storage).
        
        Returns:
            True if disconnected successfully
        """
        # Revoke token if we have one
        if self._tokens and self._tokens.get('access_token'):
            try:
                url = f"{self.config.server_url}/restapi/oauth/revoke"
                data = {'token': self._tokens['access_token']}
                headers = {
                    'Authorization': self._get_basic_auth(),
                    'Content-Type': 'application/x-www-form-urlencoded'
                }
                requests.post(url, data=data, headers=headers, timeout=10)
            except Exception:
                pass  # Best effort
        
        # Clear stored tokens
        self._tokens = None
        self._token_expiry = None
        TokenStorage.delete_tokens()
        debug_log("RingCentral: Disconnected")
        return True
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the RingCentral connection.
        
        Returns:
            Dict with 'success', 'message', and optionally 'account_info'
        """
        if not self.is_connected:
            return {'success': False, 'message': 'Not connected to RingCentral'}
        
        headers = self._get_auth_header()
        candidates: List[Tuple[str, str]] = [
            ("extension", f"{self.config.server_url}/restapi/v1.0/account/~/extension/~"),
            ("numbers", f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/phone-number?perPage=1"),
            ("account", f"{self.config.server_url}/restapi/v1.0/account/~"),
        ]

        failures: List[str] = []
        for label, url in candidates:
            try:
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json() if response.content else {}

                if label == "extension":
                    ext_name = data.get('name') or data.get('contact', {}).get('firstName') or 'RingCentral extension'
                    ext_id = data.get('id')
                    return {
                        'success': True,
                        'message': f"Connected to {ext_name}",
                        'account_info': data,
                        'endpoint_used': label,
                        'extension_id': ext_id,
                    }

                if label == "numbers":
                    count = len(data.get('records', []) or [])
                    return {
                        'success': True,
                        'message': f"Connected to RingCentral (phone numbers endpoint OK, {count} found)",
                        'account_info': {'phone_numbers_found': count},
                        'endpoint_used': label,
                    }

                main_number = data.get('mainNumber') or 'RingCentral'
                return {
                    'success': True,
                    'message': f"Connected to {main_number}",
                    'account_info': data,
                    'endpoint_used': label,
                }

            except requests.HTTPError as e:
                status = getattr(e.response, 'status_code', None)
                if status == 403:
                    failures.append(f"{label}:403")
                    continue
                if status == 401:
                    return {
                        'success': False,
                        'message': 'RingCentral token expired or unauthorized. Please reconnect.',
                    }
                failures.append(f"{label}:{status or 'http_error'}")
            except requests.RequestException as e:
                failures.append(f"{label}:{e}")

        if failures and all(item.endswith(':403') for item in failures if ':' in item):
            return {
                'success': True,
                'limited_permissions': True,
                'message': (
                    'Connected to RingCentral (limited scope for test endpoints). '
                    'Core token is valid; some optional read endpoints are restricted.'
                ),
                'account_info': {},
            }

        detail = '; '.join(failures) if failures else 'no response from RingCentral endpoints'
        return {'success': False, 'message': f'Connection test failed: {detail}'}
    
    # -------------------------------------------------------------------------
    # SMS Methods
    # -------------------------------------------------------------------------
    
    def get_sms_capable_numbers(self, settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Get phone numbers that can send SMS.
        
        First tries to fetch from RingCentral API. If that fails (e.g., due to
        insufficient permissions), falls back to manually configured numbers
        in settings.
        """
        if not self.is_connected:
            return []
        
        numbers = []
        
        # Try RingCentral API first
        try:
            url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/phone-number"
            params = {'usageType': 'DirectNumber', 'perPage': 100}
            response = requests.get(url, headers=self._get_auth_header(), params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            for record in data.get('records', []):
                features = record.get('features', [])
                if 'SmsSender' in features or 'A2PSmsSender' in features:
                    numbers.append({
                        'number': record.get('phoneNumber'),
                        'label': record.get('label', ''),
                        'features': features
                    })
            
            if numbers:
                return numbers
                
        except requests.RequestException as e:
            debug_log(f"RingCentral: Failed to get SMS numbers from API: {e}")
        
        # Fallback: Check for manually configured phone number in settings
        if settings is None:
            try:
                from dmelogic.settings import load_settings  # Lazy import to avoid cycles
                settings = load_settings()
            except Exception as ex:
                debug_log(f"RingCentral: Unable to load settings for manual number fallback: {ex}")
                settings = {}
        
        if settings:
            rc_settings = settings.get('ringcentral', {})
            manual_number = rc_settings.get('phone_number', '').strip()
            if manual_number:
                # Normalize the number
                digits = ''.join(c for c in manual_number if c.isdigit())
                if len(digits) == 10:
                    manual_number = f"+1{digits}"
                elif len(digits) == 11 and digits[0] == '1':
                    manual_number = f"+{digits}"
                
                numbers.append({
                    'number': manual_number,
                    'label': 'Configured Number',
                    'features': ['ManualConfig']
                })
                debug_log(f"RingCentral: Using manually configured number: {manual_number}")
        
        return numbers
    
    def send_sms(
        self,
        to_number: str,
        message: str,
        from_number: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send an SMS message.
        
        Args:
            to_number: Recipient phone number (E.164 format preferred: +15551234567)
            message: Message text (max 1000 chars for single SMS)
            from_number: Sender number (optional, will use first available)
            
        Returns:
            Dict with 'success', 'message_id', 'status', 'error'
        """
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected to RingCentral'}
        
        # Get sender number if not specified
        if not from_number:
            sms_numbers = self.get_sms_capable_numbers()
            if not sms_numbers:
                return {'success': False, 'error': 'No SMS-capable phone numbers found'}
            from_number = sms_numbers[0]['number']
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/sms"
        
        payload = {
            'from': {'phoneNumber': from_number},
            'to': [{'phoneNumber': to_number}],
            'text': message
        }
        
        try:
            response = requests.post(
                url,
                headers={**self._get_auth_header(), 'Content-Type': 'application/json'},
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            return {
                'success': True,
                'message_id': result.get('id'),
                'status': result.get('messageStatus', 'Sent'),
                'to': to_number,
                'from': from_number,
                'created_at': result.get('creationTime')
            }
            
        except requests.RequestException as e:
            error_detail = str(e)
            try:
                if hasattr(e, 'response') and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get('message', str(e))
            except Exception:
                pass
            
            debug_log(f"RingCentral: SMS send failed: {error_detail}")
            return {'success': False, 'error': error_detail}
    
    # -------------------------------------------------------------------------
    # Fax Methods
    # -------------------------------------------------------------------------
    
    def send_fax(
        self,
        to_number: str,
        file_path: Union[str, Path],
        cover_text: Optional[str] = None,
        cover_page_text: Optional[str] = None,
        from_number: Optional[str] = None,
        attachments: Optional[List[Union[str, Path]]] = None,
        to_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a fax.
        
        Args:
            to_number: Recipient fax number
            file_path: Primary document path to fax
            cover_text: Optional cover page text
            cover_page_text: Optional additional cover page text
            from_number: Sender fax number (optional)
            attachments: Additional files to include (cover, supporting docs)
            to_name: Recipient name (shown on RingCentral cover page To: field)
            
        Returns:
            Dict with 'success', 'message_id', 'status', 'error'
        """
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected to RingCentral'}
        
        primary_path = Path(file_path)
        if not primary_path.exists():
            return {'success': False, 'error': f'File not found: {primary_path}'}

        all_paths: List[Path] = [primary_path]

        if attachments:
            for extra in attachments:
                if not extra:
                    continue
                extra_path = Path(extra)
                if not extra_path.exists():
                    return {'success': False, 'error': f'File not found: {extra_path}'}
                all_paths.append(extra_path)
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/fax"
        
        # Build multipart form data
        to_entry = {'phoneNumber': to_number}
        if to_name:
            to_entry['name'] = to_name
        fax_data = {
            'to': [to_entry],
            'faxResolution': 'High',
            'coverIndex': 0  # Disable RingCentral built-in cover page; we use our own
        }
        
        if cover_text:
            fax_data['coverPageText'] = cover_text
        
        if from_number:
            fax_data['from'] = {'phoneNumber': from_number}
        
        content_types = {
            '.pdf': 'application/pdf',
            '.tif': 'image/tiff',
            '.tiff': 'image/tiff',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        }

        def _content_type(path: Path) -> str:
            return content_types.get(path.suffix.lower(), 'application/octet-stream')
        
        try:
            files = [('json', (None, json.dumps(fax_data), 'application/json'))]
            handles = []

            try:
                for path in all_paths:
                    handle = path.open('rb')
                    handles.append(handle)
                    files.append(('attachment', (path.name, handle, _content_type(path))))

                response = requests.post(
                    url,
                    headers=self._get_auth_header(),
                    files=files,
                    timeout=120  # Fax uploads can be slow
                )
                response.raise_for_status()
            finally:
                for handle in handles:
                    try:
                        handle.close()
                    except Exception:
                        pass
            
            result = response.json()
            return {
                'success': True,
                'message_id': result.get('id'),
                'status': result.get('messageStatus', 'Queued'),
                'to': to_number,
                'created_at': result.get('creationTime')
            }
            
        except requests.RequestException as e:
            error_detail = str(e)
            try:
                if hasattr(e, 'response') and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get('message', str(e))
            except Exception:
                pass
            
            debug_log(f"RingCentral: Fax send failed: {error_detail}")
            return {'success': False, 'error': error_detail}
    
    def get_fax_status(self, message_id: str) -> Dict[str, Any]:
        """
        Check fax delivery status.
        
        Args:
            message_id: The fax message ID
            
        Returns:
            Dict with status info
        """
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected'}
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/message-store/{message_id}"
        
        try:
            response = requests.get(url, headers=self._get_auth_header(), timeout=15)
            response.raise_for_status()
            
            result = response.json()
            return {
                'success': True,
                'message_id': message_id,
                'status': result.get('messageStatus'),
                'fax_resolution': result.get('faxResolution'),
                'fax_pages': result.get('faxPageCount'),
                'error_code': result.get('faxErrorCode')
            }
            
        except requests.RequestException as e:
            return {'success': False, 'error': str(e)}
    
    # -------------------------------------------------------------------------
    # Call Methods (RingOut)
    # -------------------------------------------------------------------------
    
    def initiate_call(
        self,
        to_number: str,
        from_number: Optional[str] = None,
        caller_id: Optional[str] = None,
        play_prompt: bool = True
    ) -> Dict[str, Any]:
        """
        Initiate a call using RingOut (click-to-call).
        
        RingOut calls your phone first, then connects you to the destination.
        
        Args:
            to_number: Number to call
            from_number: Your phone number (will ring first). If None, uses extension's direct number.
            caller_id: Caller ID to show on recipient's phone
            play_prompt: Whether to play "Please hold while connecting" prompt
            
        Returns:
            Dict with 'success', 'call_id', 'status', 'error'
        """
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected to RingCentral'}
        
        # Get caller's phone if not specified
        if not from_number:
            try:
                url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~"
                response = requests.get(url, headers=self._get_auth_header(), timeout=10)
                response.raise_for_status()
                ext_info = response.json()
                
                # Try to get direct number
                contact = ext_info.get('contact', {})
                from_number = contact.get('businessPhone') or contact.get('mobilePhone')
                
                if not from_number:
                    return {'success': False, 'error': 'No from_number specified and no direct number found'}
            except Exception as e:
                return {'success': False, 'error': f'Could not determine from number: {e}'}
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/ring-out"
        
        payload = {
            'from': {'phoneNumber': from_number},
            'to': {'phoneNumber': to_number},
            'playPrompt': play_prompt
        }
        
        if caller_id:
            payload['callerId'] = {'phoneNumber': caller_id}
        
        try:
            response = requests.post(
                url,
                headers={**self._get_auth_header(), 'Content-Type': 'application/json'},
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            return {
                'success': True,
                'call_id': result.get('id'),
                'status': result.get('status', {}).get('callStatus', 'InProgress'),
                'to': to_number,
                'from': from_number
            }
            
        except requests.RequestException as e:
            error_detail = str(e)
            try:
                if hasattr(e, 'response') and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get('message', str(e))
            except Exception:
                pass
            
            debug_log(f"RingCentral: Call initiation failed: {error_detail}")
            return {'success': False, 'error': error_detail}
    
    def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        Get status of a RingOut call.
        
        Args:
            call_id: The call ID from initiate_call
            
        Returns:
            Dict with call status
        """
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected'}
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/ring-out/{call_id}"
        
        try:
            response = requests.get(url, headers=self._get_auth_header(), timeout=10)
            response.raise_for_status()
            
            result = response.json()
            status = result.get('status', {})
            return {
                'success': True,
                'call_id': call_id,
                'call_status': status.get('callStatus'),
                'caller_status': status.get('callerStatus'),
                'callee_status': status.get('calleeStatus')
            }
            
        except requests.RequestException as e:
            return {'success': False, 'error': str(e)}
    
    def cancel_call(self, call_id: str) -> Dict[str, Any]:
        """Cancel an in-progress RingOut call."""
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected'}
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/ring-out/{call_id}"
        
        try:
            response = requests.delete(url, headers=self._get_auth_header(), timeout=10)
            if response.status_code == 204:
                return {'success': True, 'message': 'Call cancelled'}
            response.raise_for_status()
            return {'success': True}
            
        except requests.RequestException as e:
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # Inbox Methods - Fetch Incoming Messages
    # -------------------------------------------------------------------------
    
    def get_messages(
        self,
        message_type: Optional[str] = None,
        direction: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        read_status: Optional[str] = None,
        per_page: int = 100
    ) -> Dict[str, Any]:
        """
        Fetch messages from RingCentral message store.
        
        Args:
            message_type: 'SMS', 'Fax', 'Pager', 'VoiceMail', or None for all
            direction: 'Inbound', 'Outbound', or None for all
            date_from: ISO date string for start of range
            date_to: ISO date string for end of range
            read_status: 'Read', 'Unread', or None for all
            per_page: Number of results per page (max 1000)
            
        Returns:
            Dict with 'success', 'messages' list, 'total', 'error'
        """
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected to RingCentral', 'messages': []}
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/message-store"
        
        params = {
            'perPage': min(per_page, 1000),
            # Use detailed view so attachments (e.g., image MMS) and full
            # metadata are included for all message types
            'view': 'Detailed',
        }
        
        if message_type:
            params['messageType'] = message_type
        if direction:
            params['direction'] = direction
        if date_from:
            params['dateFrom'] = date_from
        if date_to:
            params['dateTo'] = date_to
        if read_status:
            params['readStatus'] = read_status
        
        try:
            response = requests.get(
                url,
                headers=self._get_auth_header(),
                params=params,
                timeout=30
            )
            response.raise_for_status()
            
            data = response.json()
            records = data.get('records', [])
            
            messages = []
            for record in records:
                msg = {
                    'id': record.get('id'),
                    'type': record.get('type'),
                    'direction': record.get('direction'),
                    'subject': record.get('subject', ''),
                    'read_status': record.get('readStatus'),
                    'created_at': record.get('creationTime'),
                    'last_modified': record.get('lastModifiedTime'),
                    'from_number': '',
                    'from_name': '',
                    'to_number': '',
                    'to_name': '',
                    'message_status': record.get('messageStatus'),
                    'fax_page_count': record.get('faxPageCount'),
                    'attachments': []
                }
                
                # Parse from/to
                from_info = record.get('from', {})
                msg['from_number'] = from_info.get('phoneNumber', from_info.get('extensionNumber', ''))
                msg['from_name'] = from_info.get('name', '')
                
                to_list = record.get('to', [])
                if to_list:
                    msg['to_number'] = to_list[0].get('phoneNumber', to_list[0].get('extensionNumber', ''))
                    msg['to_name'] = to_list[0].get('name', '')
                
                # Parse attachments (fax documents, MMS images, etc.)
                for att in record.get('attachments', []):
                    # RingCentral uses different field names: 'uri' for fax, 'contentUri' for MMS
                    attachment_uri = att.get('uri') or att.get('contentUri') or ''
                    msg['attachments'].append({
                        'id': att.get('id'),
                        'uri': attachment_uri,
                        'type': att.get('type'),
                        'content_type': att.get('contentType')
                    })
                
                messages.append(msg)
            
            return {
                'success': True,
                'messages': messages,
                'total': data.get('paging', {}).get('totalElements', len(messages))
            }
            
        except requests.RequestException as e:
            error_detail = str(e)
            try:
                if hasattr(e, 'response') and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get('message', str(e))
            except Exception:
                pass
            
            debug_log(f"RingCentral: Failed to fetch messages: {error_detail}")
            return {'success': False, 'error': error_detail, 'messages': []}
    
    def get_incoming_sms(self, date_from: Optional[str] = None, unread_only: bool = False) -> List[Dict[str, Any]]:
        """
        Convenience method to fetch incoming SMS messages.
        
        Args:
            date_from: ISO date string for start of range (default: last 7 days)
            unread_only: If True, only return unread messages
            
        Returns:
            List of SMS message dicts
        """
        if not date_from:
            from datetime import datetime, timedelta
            date_from = (datetime.now() - timedelta(days=7)).isoformat()
        
        result = self.get_messages(
            message_type='SMS',
            direction='Inbound',
            date_from=date_from,
            read_status='Unread' if unread_only else None
        )
        
        return result.get('messages', [])
    
    def get_incoming_faxes(self, date_from: Optional[str] = None, unread_only: bool = False) -> List[Dict[str, Any]]:
        """
        Convenience method to fetch incoming fax messages.
        
        Args:
            date_from: ISO date string for start of range (default: last 7 days)
            unread_only: If True, only return unread messages
            
        Returns:
            List of fax message dicts
        """
        if not date_from:
            from datetime import datetime, timedelta
            date_from = (datetime.now() - timedelta(days=7)).isoformat()
        
        result = self.get_messages(
            message_type='Fax',
            direction='Inbound',
            date_from=date_from,
            read_status='Unread' if unread_only else None
        )
        
        return result.get('messages', [])
    
    def get_unread_count(self) -> Dict[str, int]:
        """
        Get count of unread messages by type.
        
        Returns:
            Dict with 'sms', 'fax', 'total' counts
        """
        counts = {'sms': 0, 'fax': 0, 'total': 0}
        
        if not self.is_connected:
            return counts
        
        # Get unread SMS
        sms_result = self.get_messages(
            message_type='SMS',
            direction='Inbound',
            read_status='Unread',
            per_page=1  # We just need the count
        )
        if sms_result.get('success'):
            counts['sms'] = sms_result.get('total', 0)
        
        # Get unread Fax
        fax_result = self.get_messages(
            message_type='Fax',
            direction='Inbound',
            read_status='Unread',
            per_page=1
        )
        if fax_result.get('success'):
            counts['fax'] = fax_result.get('total', 0)
        
        counts['total'] = counts['sms'] + counts['fax']
        return counts
    
    def mark_as_read(self, message_id: str) -> Dict[str, Any]:
        """
        Mark a message as read.
        
        Args:
            message_id: The message ID to mark as read
            
        Returns:
            Dict with 'success', 'error'
        """
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected'}
        
        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/message-store/{message_id}"
        
        try:
            response = requests.put(
                url,
                headers={**self._get_auth_header(), 'Content-Type': 'application/json'},
                json={'readStatus': 'Read'},
                timeout=10
            )
            response.raise_for_status()
            return {'success': True}
            
        except requests.RequestException as e:
            error_detail = str(e)
            try:
                if hasattr(e, 'response') and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get('message', str(e))
            except Exception:
                pass
            return {'success': False, 'error': error_detail}

    def delete_message(self, message_id: str, purge: bool = False) -> Dict[str, Any]:
        """Delete a message from the RingCentral message store."""
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected'}

        if not message_id:
            return {'success': False, 'error': 'Message ID is required'}

        url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/message-store/{message_id}"
        params = {'purge': 'True'} if purge else None

        try:
            response = requests.delete(
                url,
                headers=self._get_auth_header(),
                params=params,
                timeout=15
            )

            if response.status_code in (200, 202, 204):
                return {'success': True}

            response.raise_for_status()
            return {'success': True}

        except requests.RequestException as e:
            error_detail = str(e)
            try:
                if hasattr(e, 'response') and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get('message', str(e))
            except Exception:
                pass
            return {'success': False, 'error': error_detail}

    def download_message_attachment(self, attachment_uri: str) -> Optional[Tuple[bytes, str]]:
        """Download a generic message attachment (e.g., SMS/MMS image)."""
        if not self.is_connected:
            debug_log("RingCentral: Not connected, cannot download message attachment")
            return None

        if not attachment_uri:
            debug_log("RingCentral: Empty attachment URI; cannot download message attachment")
            return None

        url = f"{self.config.server_url}{attachment_uri}" if attachment_uri.startswith("/") else attachment_uri
        debug_log(f"RingCentral: Downloading message attachment from: {url[:100]}...")

        try:
            response = requests.get(
                url,
                headers=self._get_auth_header(),
                timeout=60
            )
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', 'application/octet-stream')
            debug_log(
                f"RingCentral: Message attachment fetched ({len(response.content)} bytes, content_type={content_type})"
            )
            return response.content, content_type
        except requests.RequestException as e:
            status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            debug_log(f"RingCentral: Message attachment download failed: status={status_code}, error={e}")
            return None
    
    def download_fax_attachment(self, attachment_uri: str, message_id: str = None, attachment_id: str = None) -> Optional[bytes]:
        """Download an attachment from a message.

        Historically this was only used for fax PDFs/TIFFs, but the same
        endpoint is also used for image MMS and other message-store
        attachments. RingCentral sometimes returns a relative URI (starting
        with '/restapi/...' ) instead of a fully-qualified URL, so we need to
        normalize it before making the request.
        
        For MMS attachments, if the direct URI fails, we fall back to
        constructing the content URL from message_id and attachment_id.

        Args:
            attachment_uri: The attachment URI from the message record
            message_id: Optional message ID for fallback URL construction
            attachment_id: Optional attachment ID for fallback URL construction

        Returns:
            Bytes of the attachment content, or None on error.
        """
        if not self.is_connected:
            debug_log("RingCentral: Not connected, cannot download attachment")
            return None

        if not attachment_uri:
            debug_log("RingCentral: Empty attachment URI; cannot download")
            return None

        # Normalize relative URIs like '/restapi/v1.0/...' to a full URL
        if attachment_uri.startswith("/"):
            url = f"{self.config.server_url}{attachment_uri}"
        else:
            url = attachment_uri

        debug_log(f"RingCentral: Attempting to download attachment from: {url[:100]}...")

        # Method 1: Try with Authorization header
        try:
            response = requests.get(
                url,
                headers=self._get_auth_header(),
                timeout=60
            )
            debug_log(f"RingCentral: Response status: {response.status_code}")
            response.raise_for_status()
            debug_log(f"RingCentral: Successfully downloaded {len(response.content)} bytes")
            return response.content

        except requests.RequestException as e:
            # Log detailed error info
            status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            response_text = ''
            if hasattr(e, 'response') and e.response is not None:
                try:
                    response_text = e.response.text[:200]
                except:
                    pass
            debug_log(f"RingCentral: Header auth failed: status={status_code}, error={e}, response={response_text}")
        
        # Method 2: Try with access_token as query parameter (required for some media URLs)
        token = self.access_token
        if token:
            try:
                separator = '&' if '?' in url else '?'
                url_with_token = f"{url}{separator}access_token={token}"
                debug_log(f"RingCentral: Trying with access_token query param...")
                response = requests.get(url_with_token, timeout=60)
                debug_log(f"RingCentral: Token param response status: {response.status_code}")
                response.raise_for_status()
                debug_log(f"RingCentral: Token param succeeded, downloaded {len(response.content)} bytes")
                return response.content
            except requests.RequestException as e2:
                debug_log(f"RingCentral: Token param also failed: {e2}")
            
        # Method 3: Try fallback URL construction for MMS if we have IDs
        if message_id and attachment_id:
            fallback_url = f"{self.config.server_url}/restapi/v1.0/account/~/extension/~/message-store/{message_id}/content/{attachment_id}"
            debug_log(f"RingCentral: Trying fallback URL: {fallback_url[:80]}...")
            try:
                response = requests.get(
                    fallback_url,
                    headers=self._get_auth_header(),
                    timeout=60
                )
                response.raise_for_status()
                debug_log(f"RingCentral: Fallback succeeded, downloaded {len(response.content)} bytes")
                return response.content
            except requests.RequestException as e2:
                debug_log(f"RingCentral: Fallback also failed: {e2}")
            
        return None


# -----------------------------------------------------------------------------
# Singleton Instance Management
# -----------------------------------------------------------------------------

_service_instance: Optional[RingCentralService] = None


def get_ringcentral_service(settings: Optional[Dict[str, Any]] = None) -> Optional[RingCentralService]:
    """
    Get or create the RingCentral service instance.
    
    Args:
        settings: Application settings dict. If None, tries environment variables.
        
    Returns:
        RingCentralService instance or None if not configured
    """
    global _service_instance
    
    if _service_instance is not None:
        return _service_instance
    
    # Try to load config
    config = None
    
    # First try settings
    if settings:
        config = RingCentralConfig.from_settings(settings)
    
    # Then try environment
    if not config:
        config = RingCentralConfig.from_env()
    
    if not config:
        debug_log("RingCentral: Not configured (no credentials in settings or environment)")
        return None
    
    _service_instance = RingCentralService(config)
    return _service_instance


def reset_ringcentral_service():
    """Reset the service instance (for testing or reconfiguration)."""
    global _service_instance
    _service_instance = None
