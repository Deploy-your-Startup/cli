"""Preview the resources bootstrap will provision, using project-scoped prices."""

from __future__ import annotations

import os
import tempfile
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import click
import httpx
import yaml

from cli import wizard_output as ui
from cli.hetzner.server_plans import cheapest_plan, monthly_price, require_empty_project

from ..base import WizardStep
from ..context import BootstrapContext


class PublicVarsLoader(yaml.SafeLoader):
    """Read public settings without decrypting or retaining Vault scalars."""


PublicVarsLoader.add_constructor("!vault", lambda _loader, _node: None)


def monthly_cost(value: str) -> Decimal:
    """Validate a user-supplied gross monthly cap without float conversion."""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise click.BadParameter(
            "must be a non-negative decimal amount", param_hint="--max-monthly-cost"
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise click.BadParameter(
            "must be a non-negative decimal amount", param_hint="--max-monthly-cost"
        )
    return amount


def api_url() -> str:
    """Explicit provider boundary for local integration environments."""
    return os.environ.get("HCLOUD_API_URL", "https://api.hetzner.cloud/v1").rstrip("/")


def _read_settings(ctx: BootstrapContext) -> dict:
    settings = {}
    try:
        for name in ("all.yml", "production.yml"):
            path = ctx.deployment_dir / "group_vars" / name
            if not path.exists():
                continue
            values = yaml.load(path.read_text(), Loader=PublicVarsLoader)
            if values is None:
                continue
            if not isinstance(values, dict):
                raise ValueError("group_vars must be a mapping")
            settings.update(values)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise click.ClickException(
            f"Cannot read Hetzner deployment settings: {exc}"
        ) from exc
    return settings


def _pricing(ctx: BootstrapContext) -> dict:
    pricing = {}
    try:
        response = httpx.get(
            f"{api_url()}/pricing",
            headers={"Authorization": f"Bearer {ctx.hetzner_token}"},
            timeout=10,
        )
        response.raise_for_status()
        pricing = response.json()["pricing"]
        if not isinstance(pricing, dict) or not isinstance(
            pricing.get("currency"), str
        ):
            raise ValueError("invalid pricing response")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        # Never print response bodies or exception URLs: they can contain credentials.
        pricing = {}
        ui.warning(
            "Hetzner price could not be determined; review pricing before provisioning."
        )
    return pricing


def _server_overrides(
    ctx: BootstrapContext, settings: dict, pricing: dict
) -> dict[str, str]:
    if not ctx.cheapest_available:
        return {}
    if (
        str(settings.get("master_count", 1)) != "1"
        or str(settings.get("worker_count", 0)) != "0"
    ):
        raise click.ClickException(
            "--cheapest-available requires exactly one master and no workers."
        )
    if "server_type" in settings or "master_server_type" in settings:
        ui.info("Keeping the server type already configured in this project.")
        return {}
    if not pricing or not ctx.hetzner_token:
        raise click.ClickException(
            "Could not verify Hetzner prices for --cheapest-available. Reconnect Hetzner and retry."
        )
    location = settings.get("location")
    if location is not None and not isinstance(location, str):
        raise click.ClickException(
            "The Hetzner location must be a literal location name."
        )
    require_empty_project(api_url(), ctx.hetzner_token)
    plan = cheapest_plan(api_url(), ctx.hetzner_token, pricing, location)
    overrides = {"master_server_type": plan.server_type}
    if "location" not in settings:
        overrides["location"] = plan.location
    return overrides


def _save_overrides(ctx: BootstrapContext, overrides: dict[str, str]) -> None:
    path = ctx.deployment_dir / "group_vars" / "production.yml"
    try:
        original = path.read_text() if path.exists() else ""
        separator = "\n" if original and not original.endswith("\n") else ""
        content = original + separator + yaml.safe_dump(overrides, sort_keys=False)
        # Keep existing Vault blocks and comments as written by Copier/the owner.
        rendered = yaml.load(content, Loader=PublicVarsLoader)
        if not isinstance(rendered, dict) or any(
            rendered.get(k) != v for k, v in overrides.items()
        ):
            raise ValueError("could not append server settings")
        with tempfile.TemporaryDirectory(
            dir=path.parent, prefix=".server-plan-"
        ) as staging:
            temporary_path = Path(staging) / path.name
            temporary_path.write_text(content)
            temporary_path.replace(path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise click.ClickException(
            "Could not save the selected Hetzner server plan."
        ) from exc


class CostStep(WizardStep):
    number = 5
    name = "Hetzner monthly cost"

    def check(self, ctx: BootstrapContext) -> bool:
        # Consent is never persisted: a resumed project may have different settings.
        return False

    def run(self, ctx: BootstrapContext) -> None:
        from .finalize import FinalizeStep

        if FinalizeStep().check(ctx):
            ui.skip_indicator(
                "Project already finalized; no new provisioning requested"
            )
            return

        settings = _read_settings(ctx)
        pricing = _pricing(ctx)
        overrides = _server_overrides(ctx, settings, pricing)
        settings.update(overrides)
        try:
            location = settings.get("location", "fsn1")
            if not isinstance(location, str) or "{{" in location:
                raise ValueError("location must be a literal location name")
            resources = []
            server_count = 0
            for node, default in (("master", 1), ("worker", 0)):
                count = settings.get(f"{node}_count", default)
                if isinstance(count, bool) or not str(count).isdigit():
                    raise ValueError(f"{node}_count must be a non-negative integer")
                count = int(count)
                server_type = settings.get(
                    f"{node}_server_type", settings.get("server_type", "cx23")
                )
                if not isinstance(server_type, str) or "{{" in server_type:
                    raise ValueError(
                        f"{node}_server_type must be a literal server type"
                    )
                if count:
                    resources.append(
                        (
                            f"{count} × {server_type} server ({location})",
                            "server_types",
                            server_type,
                            location,
                            count,
                        )
                    )
                    server_count += count
            if server_count:
                resources.append(
                    (
                        f"{server_count} × primary IPv4 ({location})",
                        "primary_ips",
                        "ipv4",
                        location,
                        server_count,
                    )
                )
            # The generated playbook gates this role; its type/location are fixed.
            load_balancer = settings.get("create_load_balancer", False)
            if not isinstance(load_balancer, bool):
                raise ValueError("create_load_balancer must be true or false")
            if load_balancer:
                resources.append(
                    (
                        "1 × lb11 load balancer (fsn1)",
                        "load_balancer_types",
                        "lb11",
                        "fsn1",
                        1,
                    )
                )
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise click.ClickException(
                f"Cannot determine planned Hetzner resources: {exc}"
            ) from exc

        currency = pricing.get("currency", "unknown currency")
        rows = []
        total_net = Decimal(0)
        total_gross = Decimal(0)
        complete = bool(pricing)
        for label, category, kind, region, count in resources:
            price = monthly_price(pricing, category, kind, region)
            if price is None:
                complete = False
                ui.warning(f"Monthly price unavailable for {kind} in {region}.")
                rows.append((label, "unavailable", "unavailable"))
            else:
                net, gross = (amount * count for amount in price)
                total_net += net
                total_gross += gross
                rows.append((label, self._money(net), self._money(gross)))

        rows.append(
            (
                "Total per month",
                self._money(total_net) if complete else "unavailable",
                self._money(total_gross) if complete else "unavailable",
            )
        )
        width = max(len(row[0]) for row in rows)
        ui.info(
            f"{'Resource':<{width}}  {'Monthly net':>14}  {'Monthly gross':>14}  ({currency})"
        )
        for label, net_text, gross_text in rows:
            ui.info(f"{label:<{width}}  {net_text:>14}  {gross_text:>14}")
        ui.info(
            "Hetzner bills hourly up to this monthly cap; traffic beyond the included volume and the domain are not included."
        )
        if ctx.max_monthly_cost is not None:
            if not complete:
                raise click.ClickException(
                    "Cannot verify --max-monthly-cost because the Hetzner price could not be determined; cancelled before provisioning."
                )
            if total_gross > ctx.max_monthly_cost:
                raise click.ClickException(
                    f"Gross monthly cost {self._money(total_gross)} {currency} exceeds --max-monthly-cost {ctx.max_monthly_cost}; cancelled before provisioning."
                )
        if overrides and not complete:
            raise click.ClickException(
                "Could not verify the total price for --cheapest-available; cancelled before provisioning."
            )
        prompt = (
            f"Provision these resources for about {self._money(total_gross)} {currency}/month?"
            if complete
            else "Provision these resources with an unknown monthly cost?"
        )
        if not ctx.non_interactive and not ui.confirm(prompt, default=False):
            raise click.ClickException(
                "Cancelled before provisioning; nothing was created in Hetzner."
            )
        if overrides:
            _save_overrides(ctx, overrides)

    @staticmethod
    def _money(value: Decimal) -> str:
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
