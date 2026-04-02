"""Configuration constants for Hetzner browser automation."""

import os
from pathlib import Path

# ── Hetzner Cloud Console URLs ────────────────────────────────────────
HETZNER_BASE_URL = "https://console.hetzner.com"
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
    '[data-test="create-project-btn"] button, '
    '[data-test="create-project-btn"], '
    'button:has-text("Neues Projekt"), '
    'button:has-text("New project")'
)

SELECTORS_ADD_BUTTON_FALLBACK = (
    'button:has-text("+"), '
    'button[aria-label*="add" i], '
    'button[aria-label*="new" i], '
    'button[aria-label*="create" i]'
)

SELECTORS_PROJECT_NAME_INPUT = (
    '[data-test="create-project-name-input"] input, '
    'input[name="name"], '
    'input[placeholder*="Project" i], '
    'input[placeholder*="Projekt" i], '
    'input[placeholder*="Name" i]'
)

SELECTORS_SUBMIT_BUTTON = (
    '[data-test="create-project-submit-btn"] button, '
    '[data-test="create-project-submit-btn"], '
    'button[type="submit"], '
    'button:has-text("Add"), '
    'button:has-text("Hinzufügen"), '
    'button:has-text("Create"), '
    'button:has-text("Erstellen"), '
    'button:has-text("Save"), '
    'button:has-text("Speichern")'
)

SELECTORS_GENERATE_TOKEN_BUTTON = (
    '[data-test="tokens-add-btn"], '
    'button:has-text("API-Token hinzufügen"), '
    'button:has-text("Add API token"), '
    'button:has-text("Generate API token"), '
    'button:has-text("API-Token generieren"), '
    'button:has-text("Generate"), '
    'button:has-text("Generieren"), '
    '[data-test*="generate-token"]'
)

SELECTORS_TOKEN_DESCRIPTION_INPUT = (
    '[data-test="description"] [data-test="input"], '
    '[data-test="description"] input, '
    'input[name="description"], '
    'input[placeholder*="Description" i], '
    'input[placeholder*="Beschreibung" i]'
)

SELECTORS_TOKEN_READWRITE = (
    '[data-test="radio-item--read_write"], '
    'label:has-text("Lesen & Schreiben"), '
    'label:has-text("Read & Write"), '
    'input[value="readwrite"], '
    '[data-test*="readwrite"]'
)

SELECTORS_TOKEN_SUBMIT = (
    '.hc-modal__footer [data-test="testAcceptButton"] button, '
    '.hc-modal__footer button:has-text("API-Token hinzufügen"), '
    '.hc-modal__footer button:has-text("Add API token"), '
    '[data-test="testAcceptButton"] button'
)

SELECTORS_TOKEN_VALUE = [
    ".click-to-copy__content",
    '[data-test*="token-value"]',
    '[data-testid*="token-value"]',
    ".token-display code",
    ".token-value",
    "code",
    "pre",
    "input[readonly]",
]

SELECTORS_COPY_BUTTON = (
    ".click-to-copy__box, "
    '[data-copy="Kopieren"], [data-copy="Copy"], '
    'button:has-text("Copy"), button:has-text("Kopieren")'
)

SELECTORS_SECURITY_LINK = (
    'a:has-text("Security"), a:has-text("Sicherheit"), [data-test*="security"]'
)

SELECTORS_API_TOKENS_LINK = 'a:has-text("API Tokens"), a:has-text("API-Tokens")'
