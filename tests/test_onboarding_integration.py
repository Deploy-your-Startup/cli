"""Exercise the public onboarding CLI without creating external resources."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def launch(tmp_path, answers, *options, domain_option=("--domain-owned",)):
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "bootstrap",
            "--kind",
            "fullstack",
            "--provider",
            "hetzner",
            *domain_option,
            "--github-username",
            "example-owner",
            "--output-dir",
            str(tmp_path / "projects"),
            *options,
        ],
        input=answers,
        text=True,
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "SENTRY_DSN": "",
            "NO_COLOR": "1",
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "home/.config"),
        },
        timeout=30,
    )


def test_first_launch_is_short_and_cancel_creates_nothing(tmp_path):
    # GIVEN a new user with a project name and domain, using default options.
    # WHEN they press Enter at the final confirmation.
    result = launch(tmp_path, "my-startup\nexample.com\n\n\n")
    # THEN the CLI presents an English plan and cancels without side effects.
    assert result.returncode == 0, result.stderr
    assert "deploy your startup" in result.stdout
    assert ">_" in result.stdout
    assert "Your launch plan" in result.stdout
    assert "example-owner/my-startup" in result.stdout
    assert "provider charges" in result.stdout
    assert "Cancelled. No resources were created." in result.stdout
    assert "Sentry DSN" not in result.stdout
    assert "Extra domains (comma-separated" not in result.stdout
    assert not (tmp_path / "projects").exists()
    assert "Projekt" not in result.stdout


def test_optional_settings_and_invalid_name_are_guided(tmp_path):
    # GIVEN a typo in the project name and a user who wants advanced settings.
    # WHEN they correct it, enter optional domains, then decline creation.
    result = launch(
        tmp_path, "Bad Name\nmy-startup\nexample.com\ny\nwww.example.com\nn\n"
    )
    # THEN retry guidance and optional prompts are understandable in English.
    assert result.returncode == 0, result.stderr
    assert "Use lowercase letters, numbers and hyphens" in result.stdout
    assert "Extra domains (comma-separated, Enter to skip)" in result.stdout
    # Error tracking is its own Sentry question, asked by the wizard.
    assert "Sentry DSN" not in result.stdout
    assert "Cancelled. No resources were created." in result.stdout
    assert not (tmp_path / "projects").exists()


def test_interrupted_onboarding_does_not_create_resources(tmp_path):
    # GIVEN a user who closes the input before answering the questions.
    # WHEN stdin ends during project-name entry.
    result = launch(tmp_path, "")
    # THEN the process exits with an understandable abort and no project.
    assert result.returncode != 0
    assert "Aborted" in result.stderr
    assert not (tmp_path / "projects").exists()


def test_user_without_a_domain_is_offered_registration(tmp_path):
    # GIVEN a new user who has no domain yet and first types something invalid.
    # WHEN they choose to register one and then decline creation.
    result = launch(
        tmp_path, "my-startup\n2\nkeine\nmy-startup.de\n\n\n", domain_option=()
    )
    # THEN the CLI asks before assuming ownership, rejects the non-domain and
    # plans the registration instead of treating the domain as owned.
    assert result.returncode == 0, result.stderr
    assert "Do you already have a domain?" in result.stdout
    assert "register a new one through Hetzner" in result.stdout
    assert "Domain to register (for example, example.com)" in result.stdout
    assert "Enter a domain like example.com" in result.stdout
    assert "my-startup.de (register through Hetzner)" in result.stdout
    assert "Cancelled. No resources were created." in result.stdout
    assert not (tmp_path / "projects").exists()


def test_user_with_a_domain_is_asked_for_it(tmp_path):
    # GIVEN a new user who already owns a domain.
    # WHEN they say so and enter it, then decline creation.
    result = launch(tmp_path, "my-startup\n1\nexample.com\n\n\n", domain_option=())
    # THEN the plan uses that domain without a registration.
    assert result.returncode == 0, result.stderr
    assert "Your domain (for example, example.com)" in result.stdout
    assert "register through Hetzner" not in result.stdout.split("Your launch plan")[1]
    assert "Cancelled. No resources were created." in result.stdout


def test_launch_plan_shows_the_pinned_default_template(tmp_path):
    # GIVEN a new user who does not choose a template version.
    # WHEN they review the launch plan and cancel.
    result = launch(tmp_path, "my-startup\nexample.com\n\n\n")
    # THEN the plan names the tested template release this CLI pins.
    assert result.returncode == 0, result.stderr
    assert "Template  Django/FastAPI v0.1.8" in result.stdout


def test_explicit_or_custom_templates_keep_their_version(tmp_path):
    # GIVEN a user who picks a template version, or a custom template.
    # WHEN they review the launch plan and cancel.
    pinned = launch(
        tmp_path, "my-startup\nexample.com\n\n\n", "--template-version", "main"
    )
    custom = launch(
        tmp_path,
        "my-startup\nexample.com\n\n\n",
        "--template",
        "https://example.com/acme/vue-template.git",
    )
    # THEN their choice wins, and a custom template follows its HEAD.
    assert "Template  Django/FastAPI main" in pinned.stdout, pinned.stderr
    assert "Template  vue-template HEAD" in custom.stdout, custom.stderr


def test_pitch_launch_pins_builtin_and_preserves_explicit_templates(tmp_path):
    # GIVEN a user choosing a landing page without creating external resources.
    options = (
        "--kind",
        "pitch",
        "--project-name",
        "my-startup",
        "--base-domain",
        "example.com",
    )
    # WHEN they inspect the default, an explicit release and a custom template.
    default = launch(tmp_path, "\n\n", *options)
    explicit = launch(tmp_path, "\n\n", *options, "--template-version", "v2.0.0")
    custom = launch(
        tmp_path, "\n\n", *options, "--template", "https://example.com/pitch.git"
    )
    # THEN the plan names the pinned release, preserves overrides and cancels.
    for result, version in [
        (default, "v0.1.2"),
        (explicit, "v2.0.0"),
        (custom, "HEAD"),
    ]:
        assert result.returncode == 0, result.stderr
        assert f"Template  Pitch {version}" in result.stdout
        assert "Cancelled. No resources were created." in result.stdout
    assert not (tmp_path / "projects").exists()


def test_shared_owner_launch_uses_the_compatible_published_default(tmp_path):
    # GIVEN the published default template and a new shared-cluster owner.
    # WHEN the real CLI inspects its capability, presents the plan and is cancelled.
    result = launch(
        tmp_path,
        "\n\n",
        "--shared-cluster",
        "--without-auth",
        "--project-name",
        "cluster-owner",
        "--base-domain",
        "owner.example.com",
    )
    # THEN no unpublished template ref or cloud resources are needed for the plan.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Template  Django/FastAPI v0.1.8" in result.stdout
    assert "Cancelled. No resources were created." in result.stdout
    assert not (tmp_path / "projects").exists()
    readme = (ROOT / "README.md").read_text()
    assert "CLI `v0.1.28` pins the compatible Django template `v0.1.8`" in readme
    assert "startup bootstrap --kind pitch --template-version v0.1.2" in readme
    assert "startup sync --source-version v0.1.7 --dry-run" in readme


def run_interactive_bootstrap(
    tmp_path, answers, *options, detect_account=False, environment=None
):
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "bootstrap",
            *([] if detect_account else ["--github-username", "example-owner"]),
            *options,
        ],
        input=answers,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        check=False,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "SENTRY_DSN": "",
            "NO_COLOR": "1",
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "home/.config"),
            **(environment or {}),
        },
    )


def test_interactive_stack_and_target_choices_precede_credentials(tmp_path):
    # GIVEN a new user starting from bootstrap without fixed kind/provider flags.
    scenarios = [
        ("1", "2\n1\nexample.com\n\nn\n", "Django/FastAPI v0.1.8", "Hetzner"),
        (
            "2",
            "1\nexample.com\n\nn\n",
            "Vue + Django/FastAPI v0.1.7",
            "Bring your own server",
        ),
        ("3", "example.com\nn\n", "Pitch v0.1.2", "Cloudflare Pages"),
        (
            "4",
            "1\nexample.com\n\nn\n",
            "Vue + NestJS v0.1.0",
            "Bring your own server",
        ),
    ]
    for stack, answers, template, target in scenarios:
        # WHEN choosing a stack and destination, then declining the launch plan.
        result = run_interactive_bootstrap(
            tmp_path, f"my-startup\n{stack}\n{tmp_path / 'projects'}\n" + answers
        )
        # THEN choices are visible in order and cancellation creates nothing.
        assert result.returncode == 0, result.stdout + result.stderr
        output = result.stdout
        assert (
            output.index("Project name")
            < output.index("Which stack")
            < output.index("Project folder")
        )
        if target != "Cloudflare Pages":
            assert output.index("Project folder") < output.index("Where should it run?")
        assert template in output and target in output
        assert "Cancelled. No resources were created." in output
        assert not (tmp_path / "projects").exists()
        if target == "Bring your own server":
            assert "register a new one through Hetzner" not in output
        if target == "Cloudflare Pages":
            assert "Where should it run?" not in output


def test_custom_template_and_flags_skip_interactive_stack_selection(tmp_path):
    # GIVEN an automation/custom-template user with all project choices supplied.
    # WHEN reviewing the launch plan using the existing options.
    result = run_interactive_bootstrap(
        tmp_path,
        "n\nn\n",
        "--kind",
        "fullstack",
        "--provider",
        "byos",
        "--project-name",
        "my-startup",
        "--output-dir",
        str(tmp_path / "projects"),
        "--base-domain",
        "example.com",
        "--template",
        "https://example.com/custom.git",
        "--template-version",
        "v3.2.1",
    )
    # THEN explicit inputs are preserved and no new stack prompt is inserted.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "custom v3.2.1" in result.stdout
    assert "Which stack" not in result.stdout
    assert not (tmp_path / "projects").exists()


def test_byos_does_not_silently_accept_unsupported_domain_purchase(tmp_path):
    # GIVEN BYOS with the domain-order flag used by Hetzner.
    # WHEN reviewing that unsupported combination.
    result = run_interactive_bootstrap(
        tmp_path,
        "",
        "--kind",
        "fullstack",
        "--provider",
        "byos",
        "--buy-domain",
        "--project-name",
        "my-startup",
        "--output-dir",
        str(tmp_path / "projects"),
    )
    # THEN setup stops before creating resources and explains how to proceed.
    assert result.returncode != 0
    assert "Register it with your chosen registrar" in result.stderr
    assert not (tmp_path / "projects").exists()


@pytest.fixture
def github_environment(tmp_path):
    """A local gh boundary with persisted account state; no real credentials."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    account = tmp_path / "github-account"
    account.write_text("first-owner")
    calls = tmp_path / "github-calls"
    gh = binaries / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "account = Path(os.environ['TEST_GITHUB_ACCOUNT'])\n"
        "with open(os.environ['TEST_GITHUB_CALLS'], 'a') as log:\n"
        "    log.write(' '.join(args) + '\\n')\n"
        "if args == ['api', '--hostname', 'github.com', 'user', '-q', '.login']:\n"
        "    print(account.read_text())\n"
        "    sys.exit(0 if account.read_text() else 1)\n"
        "if args[:2] == ['auth', 'login']:\n"
        "    if os.environ.get('TEST_LOGIN_FAIL'): sys.exit(1)\n"
        "    account.write_text('' if os.environ.get('TEST_LOGIN_UNVERIFIED') else 'second-owner')\n"
        "    sys.exit(0)\n"
        "if args == ['auth', 'setup-git', '--hostname', 'github.com']:\n"
        "    sys.exit(1 if os.environ.get('TEST_SETUP_FAIL') else 0)\n"
        "sys.exit(99)\n"
    )
    gh.chmod(0o755)
    return {
        "PATH": str(binaries),
        "GH_TOKEN": "",
        "GITHUB_TOKEN": "",
        "TEST_GITHUB_ACCOUNT": str(account),
        "TEST_GITHUB_CALLS": str(calls),
    }


def github_plan(tmp_path, environment, answers):
    return run_interactive_bootstrap(
        tmp_path,
        answers,
        "--kind",
        "pitch",
        "--project-name",
        "my-startup",
        "--output-dir",
        str(tmp_path / "projects"),
        "--base-domain",
        "example.com",
        detect_account=True,
        environment=environment,
    )


def test_complete_question_order_confirms_github_before_creation(
    tmp_path, github_environment
):
    # GIVEN a fresh project and an authenticated GitHub CLI.
    # WHEN selecting Django, an existing server, the current account and cancelling.
    result = run_interactive_bootstrap(
        tmp_path,
        f"my-startup\n1\n{tmp_path / 'projects'}\n1\nexample.com\nn\ny\nn\n",
        detect_account=True,
        environment=github_environment,
    )
    # THEN all six decisions occur in order without any resource-creation calls.
    assert result.returncode == 0, result.stdout + result.stderr
    prompts = [
        "Project name",
        "Which stack",
        "Project folder",
        "Where should it run?",
        "GitHub account: first-owner",
        "Create this project and start setup?",
    ]
    positions = [result.stdout.index(prompt) for prompt in prompts]
    assert positions == sorted(positions)
    assert f"Project path: {tmp_path / 'projects' / 'my-startup'}" in result.stdout
    assert "first-owner/my-startup" in result.stdout
    assert Path(github_environment["TEST_GITHUB_CALLS"]).read_text().splitlines() == [
        "api --hostname github.com user -q .login"
    ]
    assert not (tmp_path / "projects").exists()


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("TEST_LOGIN_UNVERIFIED", "Could not verify the GitHub account after login"),
        ("TEST_SETUP_FAIL", "Could not configure GitHub Git credentials"),
    ],
)
def test_incomplete_github_connection_does_not_start_creation(
    tmp_path, github_environment, failure, message
):
    # GIVEN native login succeeds but account verification or Git setup fails.
    github_environment[failure] = "1"
    # WHEN rejecting the old account and connecting another account.
    result = github_plan(tmp_path, github_environment, "n\n")
    # THEN a single login attempt ends with actionable guidance and no resources.
    assert result.returncode != 0
    assert message in result.stderr
    assert "Your launch plan" not in result.stdout
    assert (
        Path(github_environment["TEST_GITHUB_CALLS"]).read_text().count("auth login")
        == 1
    )
    assert not (tmp_path / "projects").exists()


def test_rejected_github_account_uses_native_login_and_refreshes_plan(
    tmp_path, github_environment
):
    # GIVEN a valid login for an account the user does not want to use.
    # WHEN rejecting it, signing in as another user, then cancelling the plan.
    result = github_plan(tmp_path, github_environment, "n\ny\nn\n")
    # THEN the plan uses only the newly confirmed account and no project exists.
    assert result.returncode == 0, result.stdout + result.stderr
    plan = result.stdout.split("Your launch plan")[1]
    assert "second-owner/my-startup" in plan
    assert "first-owner/my-startup" not in plan
    calls = Path(github_environment["TEST_GITHUB_CALLS"]).read_text()
    assert (
        "auth login --hostname github.com --git-protocol https --scopes repo,workflow,read:packages,write:packages"
        in calls
    )
    assert "auth setup-git --hostname github.com" in calls
    assert not (tmp_path / "projects").exists()


def test_missing_github_login_is_connected_before_review(tmp_path, github_environment):
    # GIVEN no authenticated account.
    Path(github_environment["TEST_GITHUB_ACCOUNT"]).write_text("")
    # WHEN native login succeeds and the user accepts that account, then cancels.
    result = github_plan(tmp_path, github_environment, "y\nn\n")
    # THEN the confirmed authenticated owner appears in the plan.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GitHub is not connected" in result.stdout
    assert "GitHub account: second-owner" in result.stdout
    assert "second-owner/my-startup" in result.stdout.split("Your launch plan")[1]
    assert not (tmp_path / "projects").exists()


def test_cancelled_github_login_stops_before_resource_creation(
    tmp_path, github_environment
):
    # GIVEN an account change whose native login is cancelled or fails.
    github_environment["TEST_LOGIN_FAIL"] = "1"
    # WHEN the user rejects the current account.
    result = github_plan(tmp_path, github_environment, "n\n")
    # THEN setup explains the incomplete login without claiming project creation.
    assert result.returncode != 0
    assert "GitHub login did not finish" in result.stderr
    assert "no project resources were created" in result.stderr
    assert "Your launch plan" not in result.stdout
    assert not (tmp_path / "projects").exists()


@pytest.mark.parametrize("variable", ["GH_TOKEN", "GITHUB_TOKEN"])
def test_environment_token_can_be_used_but_not_silently_replaced(
    tmp_path, github_environment, variable
):
    # GIVEN authentication supplied by the execution environment.
    github_environment[variable] = "synthetic-secret-never-print"
    # WHEN accepting the account, or asking to change it.
    accepted = github_plan(tmp_path, github_environment, "y\nn\n")
    rejected = github_plan(tmp_path, github_environment, "n\n")
    # THEN reuse works; changing the effective token needs explicit environment changes.
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert rejected.returncode != 0
    assert "Update or unset those variables" in rejected.stderr
    assert "auth login" not in Path(github_environment["TEST_GITHUB_CALLS"]).read_text()
    for result in (accepted, rejected):
        assert "synthetic-secret-never-print" not in result.stdout + result.stderr
    assert not (tmp_path / "projects").exists()
