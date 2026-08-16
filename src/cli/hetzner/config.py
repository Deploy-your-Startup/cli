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


# DNS management is addressed per domain number (D0123456789), which the
# product overview exposes as ?domain_number=… on each domain link. The older
# dns.php?dnsaction2=… paths now answer with "Seite nicht gefunden".
def konsoleh_dns_url(domain_number: str) -> str:
    return f"{KONSOLEH_BASE_URL}/domains/{domain_number}/dns"


def konsoleh_nameserver_url(domain_number: str) -> str:
    return f"{konsoleh_dns_url(domain_number)}/update_nameservers"


# The nameserver inputs carry ids (ns1…ns5) and no name attribute, so the old
# input[name="newdns[]"] selector matches nothing.
SELECTORS_KONSOLEH_NS_FIELD = "#ns1, #ns2, #ns3, #ns4, #ns5, input[name='newdns[]']"

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

# The console is an Angular app behind a static shell: `domcontentloaded` fires
# while the body is still empty. Anything that reads or clicks the projects list
# must wait for one of these first — an existing project card, or the "new
# project" button for an account without any projects.
SELECTORS_PROJECTS_READY = (
    "a.project-card, "
    "[data-projectname], "
    'button:has-text("Neues Projekt"), '
    'button:has-text("New project")'
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
    '[data-test="tokens-add-btn"], '
    'button:has-text("API-Token hinzufügen"), '
    'button:has-text("Add API token"), '
    'button:has-text("Generate API token"), '
    'button:has-text("API-Token generieren"), '
    'button:has-text("Generate"), '
    'button:has-text("Generieren"), '
    '[data-testid*="generate-token"]'
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
    'input[value="readwrite"]'
)

# Token dialog accept button. The dialog's <hc-dialog> wrapper is a zero-size
# (display:inline, 0x0) element, so it is useless as a scope/visibility anchor —
# we target the dialog's fields directly by their unique data-test attributes
# instead. The accept button's data-test is distinct from the page-level add
# button ("tokens-add-btn"), so no scoping is needed to avoid clicking the wrong
# one.
SELECTORS_TOKEN_SUBMIT = (
    '[data-test="confirm-dialog-accept"] button, '
    '[data-test="confirm-dialog-accept"], '
    'hc-dialog-footer button:has-text("API-Token hinzufügen"), '
    'hc-dialog-footer button:has-text("Add API token")'
)

SELECTORS_TOKEN_VALUE = [
    ".click-to-copy__content",
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
    'a:has-text("Security"), a:has-text("Sicherheit"), [data-testid*="security"]'
)

SELECTORS_API_TOKENS_LINK = 'a:has-text("API Tokens"), a:has-text("API-Tokens")'
