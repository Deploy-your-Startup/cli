"""Save the chosen client before the first push starts deployment."""

from cli.oidc import configure

from ..base import WizardStep


class OidcStep(WizardStep):
    name = "Configure OIDC login"
    number = 4

    def check(self, ctx):
        return False

    def run(self, ctx):
        try:
            configure(
                ctx.project_dir,
                ctx.oidc_issuer,
                ctx.oidc_client_id,
                ctx.oidc_client_secret,
            )
        finally:
            ctx.oidc_client_secret = None
