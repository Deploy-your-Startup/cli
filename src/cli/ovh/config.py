"""Configuration constants for OVH / OpenStack browser automation.

The OVH compute credential is an OpenStack *Application Credential* (the closest
equivalent to a Hetzner API token). It is created in the OpenStack Horizon
dashboard (Identity -> Application Credentials), which offers a one-click
"Download clouds.yaml" — that file IS the secret we store.

Horizon lives at a region-specific URL that differs per OVH account, so the base
URL is overridable via OVH_HORIZON_URL. The flow leans on manual login + the
clouds.yaml download (captured via Playwright), so it stays robust even when the
exact Horizon DOM differs.
"""

import os
from pathlib import Path

# ── OVH / Horizon URLs ────────────────────────────────────────────────
# Override for your region/account, e.g.
#   export OVH_HORIZON_URL=https://horizon.cloud.ovh.net
HORIZON_BASE_URL = os.environ.get("OVH_HORIZON_URL", "https://horizon.cloud.ovh.net")
HORIZON_APP_CREDENTIALS_URL = f"{HORIZON_BASE_URL}/identity/application_credentials/"
# OVH Manager (where OpenStack users are created if the user has no Horizon login)
OVH_MANAGER_USERS_URL = (
    "https://www.ovh.com/manager/#/public-cloud/pci/projects/"
)

# ── Default region ────────────────────────────────────────────────────
# Used only to annotate a generated clouds.yaml when Horizon does not embed one.
DEFAULT_REGION = os.environ.get("OVH_REGION", "GRA11")

# ── Local config ──────────────────────────────────────────────────────
CONFIG_DIR = Path(
    os.environ.get(
        "OVH_BOOTSTRAP_CONFIG",
        Path.home() / ".config" / "ovh-bootstrap",
    )
)
CLOUDS_FILE = CONFIG_DIR / "clouds.yaml"

# ── Chrome profile (mirrors the Hetzner flow; real Chrome for extensions) ──
CHROME_CHANNEL = "chrome"
CHROME_USER_DATA_DIR = CONFIG_DIR / "chrome-profile"


def chrome_launch_args() -> list[str]:
    return ["--disable-blink-features=AutomationControlled"]


# ── Timeouts (ms) ─────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 60_000
LOGIN_WAIT_TIMEOUT = 300_000  # 5 min for manual login/2FA
DOWNLOAD_WAIT_TIMEOUT = 300_000  # 5 min to let the user create + download

# ── Horizon CSS selectors (best-effort; the flow degrades to manual) ──
SELECTORS_CREATE_BUTTON = (
    'a:has-text("Create Application Credential"), '
    'a:has-text("Create Credential"), '
    'button:has-text("Create Application Credential"), '
    '#application_credentials__action_create'
)

SELECTORS_NAME_INPUT = (
    'input[name="name"], '
    'input[placeholder*="Name" i], '
    '#id_name'
)

SELECTORS_SUBMIT_BUTTON = (
    '.modal-footer button.btn-primary, '
    'button[type="submit"], '
    'button:has-text("Create Application Credential"), '
    'input[type="submit"]'
)

SELECTORS_DOWNLOAD_CLOUDS_YAML = (
    'a:has-text("Download clouds.yaml"), '
    'a:has-text("clouds.yaml"), '
    'button:has-text("Download clouds.yaml")'
)
