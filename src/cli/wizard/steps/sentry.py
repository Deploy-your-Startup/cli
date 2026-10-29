"""Create the Sentry project and vault its DSN before the first push."""

from cli.sentry import configure_sentry

from ..base import WizardStep


class SentryStep(WizardStep):
    number = 4
    name = "Sentry"

    def check(self, ctx):
        return False

    def run(self, ctx):
        assert ctx.sentry_org is not None
        configure_sentry(
            ctx.project_dir, ctx.project_name, ctx.sentry_org, ctx.sentry_team
        )
