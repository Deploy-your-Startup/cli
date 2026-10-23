"""``startup deploy``: create a deployment repository and its GitHub setup."""

import click


@click.group()
def deploy():
    """Deployment operations"""


@deploy.command("create")
@click.option("--repo-name", required=True, help="Name of the repository to create")
@click.option(
    "--repo-description",
    default="Created by DeployYourStartup.com",
    help="Description of the repository",
)
@click.option(
    "--repo-private/--repo-public",
    default=True,
    help="Whether the repository should be private",
)
@click.option(
    "--template-owner", default="Deploy-your-Startup", help="Template repository owner"
)
@click.option(
    "--template-repo",
    default="django-backend-template",
    help="Template repository name",
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def create_deployment(
    repo_name, repo_description, repo_private, template_owner, template_repo, verbose
):
    """Deploy an application from a GitHub template"""
    from cli.deploy import deploy_github_repo

    raise click.exceptions.Exit(
        deploy_github_repo(
            repo_name=repo_name,
            repo_description=repo_description,
            repo_private=repo_private,
            template_owner=template_owner,
            template_repo=template_repo,
            verbose=verbose,
        )
    )


@deploy.command("github")
@click.option("--repo-name", required=True, help="Name of the repository to create")
@click.option(
    "--repo-description",
    default="Created by DeployYourStartup.com",
    help="Description of the repository",
)
@click.option(
    "--repo-private/--repo-public",
    default=True,
    help="Whether the repository should be private",
)
@click.option(
    "--template-owner",
    default="Deploy-your-Startup",
    help="Owner of the template repository",
)
@click.option(
    "--template-repo",
    default="django-backend-template",
    help="Name of the template repository",
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def github_deploy(
    repo_name, repo_description, repo_private, template_owner, template_repo, verbose
):
    """Deploy a GitHub repository from a template"""
    from cli.deploy import deploy_github_repo

    raise click.exceptions.Exit(
        deploy_github_repo(
            repo_name=repo_name,
            repo_description=repo_description,
            repo_private=repo_private,
            template_owner=template_owner,
            template_repo=template_repo,
            verbose=verbose,
        )
    )
