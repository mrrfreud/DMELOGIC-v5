"""
version.py — Application identity and version.
Single source of truth for the product name and version number.
"""

# Product identity (user-facing). Derived from the single identity source so a
# build can be re-skinned (e.g. "DMELogic 5" preview) via one flag.
from dmelogic.identity import APP_NAME, APP_TITLE, APP_PUBLISHER, APP_ID  # noqa: F401

# Application version - update this when releasing new versions.
APP_VERSION = "5.0.0"

# GitHub repository for update checks.
GITHUB_REPO = "mrrfreud/DMELOGIC-v5"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"


def get_app_name() -> str:
    """User-facing product name."""
    return APP_NAME


def get_app_title() -> str:
    """Full product title including the Nova assistant branding."""
    return APP_TITLE


def get_version() -> str:
    """Get the current application version."""
    return APP_VERSION


def get_version_tuple() -> tuple:
    """Get version as a tuple of integers for comparison."""
    try:
        return tuple(int(x) for x in APP_VERSION.split('.'))
    except ValueError:
        return (0, 0, 0, 0)


def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two version strings.
    Returns:
        -1 if v1 < v2
         0 if v1 == v2
         1 if v1 > v2
    """
    def parse_version(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.replace('v', '').split('.'))
        except ValueError:
            return (0, 0, 0, 0)
    
    t1 = parse_version(v1)
    t2 = parse_version(v2)
    
    # Pad shorter tuple with zeros
    max_len = max(len(t1), len(t2))
    t1 = t1 + (0,) * (max_len - len(t1))
    t2 = t2 + (0,) * (max_len - len(t2))
    
    if t1 < t2:
        return -1
    elif t1 > t2:
        return 1
    return 0

































































































