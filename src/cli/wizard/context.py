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
    docker_registry_host: str = "ghcr.io"

    # Populated by steps
    hetzner_token: str | None = None
    vault_password: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_zone_id: str | None = None
    cloudflare_nameservers: list[str] | None = None

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
