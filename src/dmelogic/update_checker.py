"""
update_checker.py — Check for application updates from GitHub releases
"""

import json
import logging
import os
import threading
import tempfile
import subprocess
import webbrowser
from typing import Optional, Callable, Dict, Any
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from dmelogic.version import APP_VERSION, GITHUB_API_URL, GITHUB_RELEASES_URL, compare_versions

logger = logging.getLogger(__name__)


class UpdateInfo:
    """Container for update information."""
    
    def __init__(self, version: str, release_notes: str, download_url: str, 
                 html_url: str, published_at: str):
        self.version = version
        self.release_notes = release_notes
        self.download_url = download_url  # Direct download for the installer
        self.html_url = html_url  # GitHub release page
        self.published_at = published_at
    
    def __repr__(self):
        return f"UpdateInfo(version={self.version})"


def check_for_updates(timeout: int = 10) -> Optional[UpdateInfo]:
    """
    Check GitHub releases for a newer version.
    
    Args:
        timeout: Request timeout in seconds
        
    Returns:
        UpdateInfo if a newer version is available, None otherwise
    """
    try:
        logger.info(f"Checking for updates... Current version: {APP_VERSION}")
        
        # Create request with headers (GitHub API requires User-Agent)
        request = Request(
            GITHUB_API_URL,
            headers={
                'User-Agent': f'DMELogic/{APP_VERSION}',
                'Accept': 'application/vnd.github.v3+json'
            }
        )
        
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        latest_version = data.get('tag_name', '').replace('v', '')
        
        if not latest_version:
            logger.warning("Could not determine latest version from GitHub")
            return None
        
        logger.info(f"Latest version on GitHub: {latest_version}")
        
        # Compare versions
        if compare_versions(latest_version, APP_VERSION) > 0:
            logger.info(f"Update available: {latest_version}")
            
            # Find the installer asset (look for .exe file)
            download_url = None
            for asset in data.get('assets', []):
                asset_name = asset.get('name', '').lower()
                if asset_name.endswith('.exe') and 'setup' in asset_name:
                    download_url = asset.get('browser_download_url')
                    break
            
            # Fallback to release page if no direct download
            if not download_url:
                download_url = data.get('html_url', GITHUB_RELEASES_URL)
            
            return UpdateInfo(
                version=latest_version,
                release_notes=data.get('body', 'No release notes available.'),
                download_url=download_url,
                html_url=data.get('html_url', GITHUB_RELEASES_URL),
                published_at=data.get('published_at', '')
            )
        else:
            logger.info("Application is up to date")
            return None
            
    except HTTPError as e:
        if e.code == 404:
            logger.info("No releases found on GitHub")
        else:
            logger.warning(f"HTTP error checking for updates: {e.code}")
        return None
    except URLError as e:
        logger.warning(f"Network error checking for updates: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Error parsing update response: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error checking for updates: {e}")
        return None


def check_for_updates_async(callback: Callable[[Optional[UpdateInfo]], None]) -> None:
    """
    Check for updates in a background thread.
    
    Args:
        callback: Function to call with the UpdateInfo result (or None)
    """
    def _check():
        result = check_for_updates()
        callback(result)
    
    thread = threading.Thread(target=_check, daemon=True)
    thread.start()


def open_download_page(update_info: Optional[UpdateInfo] = None) -> None:
    """Open the GitHub releases page in the default browser."""
    url = update_info.html_url if update_info else GITHUB_RELEASES_URL
    webbrowser.open(url)


def download_update(update_info: UpdateInfo, 
                    progress_callback: Optional[Callable[[int, int], None]] = None) -> Optional[str]:
    """
    Download the update installer to a temp directory.
    
    Args:
        update_info: The update information
        progress_callback: Optional callback(downloaded_bytes, total_bytes)
        
    Returns:
        Path to the downloaded file, or None if failed
    """
    try:
        if not update_info.download_url.endswith('.exe'):
            # If it's not a direct download, just open the page
            open_download_page(update_info)
            return None
        
        logger.info(f"Downloading update from: {update_info.download_url}")
        
        request = Request(
            update_info.download_url,
            headers={'User-Agent': f'DMELogic/{APP_VERSION}'}
        )
        
        # Create temp file for download
        temp_dir = tempfile.gettempdir()
        filename = f"DMELogic_Setup_{update_info.version}.exe"
        filepath = os.path.join(temp_dir, filename)
        
        with urlopen(request, timeout=60) as response:
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 8192
            
            with open(filepath, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size:
                        progress_callback(downloaded, total_size)
        
        logger.info(f"Update downloaded to: {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"Error downloading update: {e}")
        return None


def install_update(installer_path: str) -> bool:
    """
    Launch the installer and close the application.
    
    Args:
        installer_path: Path to the downloaded installer
        
    Returns:
        True if installer was launched successfully
    """
    try:
        if not os.path.exists(installer_path):
            logger.error(f"Installer not found: {installer_path}")
            return False
        
        logger.info(f"Launching installer: {installer_path}")
        
        # Launch installer in a separate process
        if os.name == 'nt':  # Windows
            subprocess.Popen([installer_path], shell=True)
        else:
            subprocess.Popen(['open', installer_path])
        
        return True
        
    except Exception as e:
        logger.error(f"Error launching installer: {e}")
        return False


def get_last_update_check() -> Optional[str]:
    """Get the timestamp of the last update check from settings."""
    try:
        # Use the centralized settings loader so we don't accidentally
        # desync/overwrite settings managed elsewhere.
        from dmelogic.settings import invalidate_settings_cache, load_settings

        invalidate_settings_cache()
        settings = load_settings(create_if_missing=False) or {}
        if isinstance(settings, dict):
            return settings.get("last_update_check")
    except Exception:
        pass
    return None


def set_last_update_check(timestamp: str) -> None:
    """Save the timestamp of the last update check to settings."""
    try:
        # IMPORTANT: Never create or clobber settings.json here.
        # Creating a partial settings file (e.g., only last_update_check)
        # can wipe db_folder and cause the app to fall back to a default DB,
        # which looks like "missing orders".
        from dmelogic.settings import invalidate_settings_cache, load_settings, save_settings

        invalidate_settings_cache()
        settings = load_settings(create_if_missing=False)
        if not isinstance(settings, dict) or not settings:
            return

        updated = dict(settings)
        updated["last_update_check"] = timestamp
        save_settings(updated)
    except Exception as e:
        logger.warning(f"Could not save update check timestamp: {e}")


def skip_version(version: str) -> None:
    """Mark a version to be skipped in future update checks."""
    try:
        from dmelogic.settings import invalidate_settings_cache, load_settings, save_settings

        invalidate_settings_cache()
        settings = load_settings(create_if_missing=False)
        if not isinstance(settings, dict) or not settings:
            return

        updated = dict(settings)
        updated["skipped_version"] = version
        save_settings(updated)
    except Exception as e:
        logger.warning(f"Could not save skipped version: {e}")


def get_skipped_version() -> Optional[str]:
    """Get the version that was marked to be skipped."""
    try:
        from dmelogic.settings import invalidate_settings_cache, load_settings

        invalidate_settings_cache()
        settings = load_settings(create_if_missing=False) or {}
        if isinstance(settings, dict):
            return settings.get("skipped_version")
    except Exception:
        pass
    return None


def should_check_for_updates() -> bool:
    """
    Determine if we should check for updates based on settings and last check time.
    Currently checks once per day.
    """
    from datetime import datetime, timedelta
    
    last_check = get_last_update_check()
    if not last_check:
        return True
    
    try:
        last_check_time = datetime.fromisoformat(last_check)
        return datetime.now() - last_check_time > timedelta(days=1)
    except Exception:
        return True
