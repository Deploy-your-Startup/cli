"""Configuration constants for Hetzner browser automation."""

import os
from pathlib import Path

# ── Hetzner Cloud Console URLs ────────────────────────────────────────
HETZNER_BASE_URL = "https://console.hetzner.cloud"
HETZNER_REGISTER_URL = "https://accounts.hetzner.com/signUp"
HETZNER_LOGIN_URL = "https://accounts.hetzner.com/login"
HETZNER_PROJECTS_URL = f"{HETZNER_BASE_URL}/projects"

# ── Hetzner KonsoleH URLs (domain registration) ─────────────────────
KONSOLEH_BASE_URL = "https://konsoleh.hetzner.com"
KONSOLEH_ORDER_URL = f"{KONSOLEH_BASE_URL}/order.php"
KONSOLEH_DOMAINS_URL = f"{KONSOLEH_BASE_URL}/domain.php"
KONSOLEH_HANDLES_URL = f"{KONSOLEH_BASE_URL}/contact.php"

# ── Hetzner Default Nameservers ──────────────────────────────────────
HETZNER_NAMESERVERS = [
    "hydrogen.ns.hetzner.com",
    "oxygen.ns.hetzner.com",
    "helium.ns.hetzner.de",
]

# ── Local config ─────────────────────────────────────────────────────
CONFIG_DIR = Path(
    os.environ.get(
        "HETZNER_BOOTSTRAP_CONFIG",
        Path.home() / ".config" / "hetzner-bootstrap",
    )
)
TOKEN_FILE = CONFIG_DIR / "hetzner.env"

# ── Chrome Profile (for Apple Passwords extension support) ───────────
# Uses real Chrome instead of Playwright's bundled Chromium so that
# native extensions like Apple Passwords work.
CHROME_CHANNEL = "chrome"  # Use system Chrome
CHROME_USER_DATA_DIR = CONFIG_DIR / "chrome-profile"


def chrome_launch_args() -> list[str]:
    """Build Chrome launch args."""
    return ["--disable-blink-features=AutomationControlled"]


# ── Timeouts (ms) ───────────────────────────────────────────────────
DEFAULT_TIMEOUT = 60_000
LOGIN_WAIT_TIMEOUT = 300_000  # 5 min for manual login/2FA
NAVIGATION_TIMEOUT = 30_000

# ── Cloud Console CSS Selectors ──────────────────────────────────────
# Collected here so Hetzner UI changes only require a single-file update.

SELECTORS_NEW_PROJECT_BUTTON = (
    'button:has-text("New project"), '
    'button:has-text("Neues Projekt"), '
    'a:has-text("New project"), '
    'a:has-text("Neues Projekt"), '
    '[data-testid="projects-new-project-button"]'
)

SELECTORS_ADD_BUTTON_FALLBACK = (
    'button:has-text("+"), '
    'button[aria-label*="add" i], '
    'button[aria-label*="new" i], '
    'button[aria-label*="create" i]'
)

SELECTORS_PROJECT_NAME_INPUT = (
    'input[name="name"], '
    'input[placeholder*="Project" i], '
    'input[placeholder*="Projekt" i], '
    'input[placeholder*="Name" i]'
)

SELECTORS_SUBMIT_BUTTON = (
    'button[type="submit"], '
    'button:has-text("Add"), '
    'button:has-text("Hinzufügen"), '
    'button:has-text("Create"), '
    'button:has-text("Erstellen"), '
    'button:has-text("Save"), '
    'button:has-text("Speichern")'
)

SELECTORS_GENERATE_TOKEN_BUTTON = (
    'button:has-text("Generate API token"), '
    'button:has-text("API-Token generieren"), '
    'button:has-text("Generate"), '
    'button:has-text("Generieren"), '
    '[data-testid*="generate-token"]'
)

SELECTORS_TOKEN_DESCRIPTION_INPUT = (
    'input[name="description"], '
    'input[placeholder*="Description" i], '
    'input[placeholder*="Beschreibung" i], '
    'input[name="name"]'
)

SELECTORS_TOKEN_READWRITE = (
    'label:has-text("Read & Write"), '
    'label:has-text("Lesen & Schreiben"), '
    'input[value="readwrite"], '
    '[data-testid*="readwrite"]'
)

SELECTORS_TOKEN_SUBMIT = (
    'button:has-text("Generate API token"), '
    'button:has-text("API-Token generieren"), '
    'button[type="submit"]'
)

SELECTORS_TOKEN_VALUE = [
    '[data-testid*="token-value"]',
    ".token-display code",
    ".token-value",
    "code",
    "pre",
    "input[readonly]",
    ".modal code",
    ".modal input[readonly]",
    '[class*="token"] code',
    '[class*="token"] input',
]

SELECTORS_COPY_BUTTON = (
    'button:has-text("Copy"), button:has-text("Kopieren"), button[aria-label*="copy" i]'
)

SELECTORS_SECURITY_LINK = (
    'a:has-text("Security"), a:has-text("Sicherheit"), [data-testid*="security"]'
)

SELECTORS_API_TOKENS_LINK = 'a:has-text("API Tokens"), a:has-text("API-Tokens")'
