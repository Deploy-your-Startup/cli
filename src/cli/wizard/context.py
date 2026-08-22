"""Bootstrap wizard data container shared across steps."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class BootstrapContext:
    """Mutable state shared across all wizard steps."""

    project_name: str
    base_domain: str
    additional_domains: str
    github_username: str
    postgres_version: str
    sentry_dsn: str
    output_dir: Path
    mode: str = "github"
    kind: str = "fullstack"  # "fullstack" | "pitch"
    provider: str = "hetzner"  # "hetzner" | "byos" (bring your own server)
    docker_registry_host: str = "ghcr.io"

    # Bring-your-own-server inputs (provider == "byos")
    byos_host: str | None = None  # VPS IP or hostname Ansible connects to
    byos_ssh_user: str = "root"

    # Set when every answer came from the command line: steps must then never
    # block on a prompt, because nobody is there to answer it.
    non_interactive: bool = False
    # None means "ask"; True/False answer the domain-ownership question.
    domain_owned: bool | None = None

    # Populated by steps
    hetzner_token: str | None = None
    vault_password: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_zone_id: str | None = None
    cloudflare_nameservers: list[str] | None = None
    # True when base_domain is a subdomain served by an existing parent zone —
    # no registrar/nameserver change is needed, only DNS records in that zone.
    cloudflare_zone_is_subdomain: bool = False

    def require_cloudflare(self) -> tuple[str, str]:
        """Return (api_token, account_id) once both are known.

        Both fields are Optional because earlier wizard steps populate them.
        Every Cloudflare call needs both, and passing `None` through used to
        produce an `Authorization: Bearer None` request that failed deep inside
        the API with an opaque error instead of naming what was missing.
        """
        missing = [
            name
            for name, value in (
                ("--cloudflare-api-token", self.cloudflare_api_token),
                ("--cloudflare-account-id", self.cloudflare_account_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Cloudflare-Zugangsdaten fehlen: " + ", ".join(missing))
        assert self.cloudflare_api_token is not None
        assert self.cloudflare_account_id is not None
        return self.cloudflare_api_token, self.cloudflare_account_id

    @property
    def project_dir(self) -> Path:
        return self.output_dir / self.project_name

    @property
    def deployment_dir(self) -> Path:
        return self.project_dir / "deployment"

    @property
    def full_repo(self) -> str:
        return f"{self.github_username}/{self.project_name}"

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.full_repo}"
