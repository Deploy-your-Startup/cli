"""Configure authentication before the first push starts deployment."""

from cli.auth0_commands import configure_auth0

from ..base import WizardStep


class Auth0Step(WizardStep):
    number = 4
    name = "Auth0"

    def check(self, ctx):
        return False

    def run(self, ctx):
        configure_auth0(
            ctx.project_dir, ctx.project_name, ctx.base_domain, ctx.auth0_tenant
        )
