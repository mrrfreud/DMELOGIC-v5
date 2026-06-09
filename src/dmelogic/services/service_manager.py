"""
Agent Order Service Manager — Start/stop/check the Windows service from DMELogic.

Only the "server" PC runs the agent order watcher service.
Client PCs skip service management entirely.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def is_server_mode() -> bool:
    """
    Check if this PC is configured as the server (runs agent order watcher).
    
    Server mode is indicated by:
    1. Registry key: HKCU\\Software\\DMELogic\\IsServer = 1
    2. Or config file: %APPDATA%\\DMELogic\\server_mode.txt exists
    """
    # Check registry first
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\DMELogic", 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, "IsServer")
        winreg.CloseKey(key)
        return value == 1 or value == "1"
    except Exception:
        pass
    
    # Check config file fallback
    config_file = Path(os.environ.get("APPDATA", "")) / "DMELogic" / "server_mode.txt"
    return config_file.exists()


def set_server_mode(enabled: bool) -> bool:
    """Set this PC as server or client mode."""
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\DMELogic")
        winreg.SetValueEx(key, "IsServer", 0, winreg.REG_DWORD, 1 if enabled else 0)
        winreg.CloseKey(key)
        logger.info(f"Server mode set to: {enabled}")
        return True
    except Exception as e:
        logger.error(f"Failed to set server mode: {e}")
        return False


def get_service_status() -> str:
    """
    Get the status of the AgentOrderService.
    
    Returns: "RUNNING", "STOPPED", "NOT_INSTALLED", or "UNKNOWN"
    """
    try:
        # Use CREATE_NO_WINDOW to prevent console flash
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
        result = subprocess.run(
            ["sc", "query", "AgentOrderService"],
            capture_output=True,
            text=True,
            timeout=10,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if result.returncode != 0:
            if "does not exist" in result.stderr or "1060" in result.stderr:
                return "NOT_INSTALLED"
            return "UNKNOWN"
        
        output = result.stdout
        if "RUNNING" in output:
            return "RUNNING"
        elif "STOPPED" in output:
            return "STOPPED"
        elif "PENDING" in output:
            return "PENDING"
        else:
            return "UNKNOWN"
            
    except subprocess.TimeoutExpired:
        return "UNKNOWN"
    except Exception as e:
        logger.error(f"Error checking service status: {e}")
        return "UNKNOWN"


def start_service() -> bool:
    """Start the AgentOrderService."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
        result = subprocess.run(
            ["sc", "start", "AgentOrderService"],
            capture_output=True,
            text=True,
            timeout=30,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if result.returncode == 0 or "already been started" in result.stderr:
            logger.info("AgentOrderService started successfully")
            return True
        else:
            logger.warning(f"Failed to start service: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Error starting service: {e}")
        return False


def stop_service() -> bool:
    """Stop the AgentOrderService."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
        result = subprocess.run(
            ["sc", "stop", "AgentOrderService"],
            capture_output=True,
            text=True,
            timeout=30,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if result.returncode == 0 or "not been started" in result.stderr:
            logger.info("AgentOrderService stopped")
            return True
        else:
            logger.warning(f"Failed to stop service: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Error stopping service: {e}")
        return False


def ensure_service_running() -> bool:
    """
    Ensure the AgentOrderService is running (server mode only).
    
    Returns True if service is running (or not needed), False on error.
    """
    if not is_server_mode():
        logger.debug("Not server mode - skipping service check")
        return True
    
    status = get_service_status()
    logger.info(f"AgentOrderService status: {status}")
    
    if status == "RUNNING":
        return True
    
    if status == "NOT_INSTALLED":
        logger.warning("AgentOrderService not installed - run INSTALL_SERVICE.bat as admin")
        return False
    
    if status == "STOPPED":
        logger.info("Starting AgentOrderService...")
        return start_service()
    
    return False


# Config file for service settings
CONFIG_DIR = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "DMELogic"
CONFIG_FILE = CONFIG_DIR / "agent_service_config.json"


def get_service_config() -> dict:
    """
    Get current service configuration.
    
    Returns dict with:
        poll_interval: int (seconds)
        max_batch_size: int
    """
    defaults = {
        "poll_interval": 5,
        "max_batch_size": 5,
    }
    try:
        if CONFIG_FILE.exists():
            import json
            data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            defaults.update(data)
    except Exception as e:
        logger.warning(f"Error loading service config: {e}")
    return defaults


def set_service_config(poll_interval: int = None, max_batch_size: int = None) -> bool:
    """
    Update service configuration.
    
    Changes take effect within 60 seconds (service reloads config periodically).
    
    Args:
        poll_interval: Polling interval in seconds (1-10800, up to 3 hours)
        max_batch_size: Max orders to process per poll (1-50)
    
    Returns True on success.
    """
    try:
        import json
        
        # Load existing config
        config = get_service_config()
        
        # Update values if provided
        if poll_interval is not None:
            poll_interval = max(1, min(10800, int(poll_interval)))  # Clamp 1-10800 (3 hours)
            config["poll_interval"] = poll_interval
            
        if max_batch_size is not None:
            max_batch_size = max(1, min(50, int(max_batch_size)))  # Clamp 1-50
            config["max_batch_size"] = max_batch_size
        
        # Save
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding='utf-8')
        logger.info(f"Service config updated: {config}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving service config: {e}")
        return False


def install_service_via_nssm(nssm_path: Path, python_exe: Path, service_script: Path) -> bool:
    """
    Install the service using NSSM.
    
    Must be run as Administrator.
    """
    try:
        # Remove existing service first
        subprocess.run([str(nssm_path), "remove", "AgentOrderService", "confirm"],
                      capture_output=True, timeout=30)
        
        # Install new service
        result = subprocess.run(
            [str(nssm_path), "install", "AgentOrderService", str(python_exe), str(service_script)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            logger.error(f"NSSM install failed: {result.stderr}")
            return False
        
        # Configure service
        subprocess.run([str(nssm_path), "set", "AgentOrderService", "DisplayName", 
                       "DMELogic Agent Order Service"], capture_output=True, timeout=10)
        subprocess.run([str(nssm_path), "set", "AgentOrderService", "Description",
                       "Monitors agent_orders folder and creates orders automatically"], 
                       capture_output=True, timeout=10)
        subprocess.run([str(nssm_path), "set", "AgentOrderService", "Start", "SERVICE_AUTO_START"],
                       capture_output=True, timeout=10)
        subprocess.run([str(nssm_path), "set", "AgentOrderService", "AppDirectory",
                       str(service_script.parent.parent)], capture_output=True, timeout=10)
        
        logger.info("AgentOrderService installed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error installing service: {e}")
        return False
