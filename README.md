# Deploy Your Startup

**Your code. Your servers. Ready to ship.**

The `startup` CLI takes you from a project template to a live application in your
own GitHub and cloud accounts. Reuse the same workflow for your next startup:
project creation, encrypted secrets, infrastructure, deployment and updates.

Generated projects are ready for coding agents from the first commit, with
`AGENTS.md`, predictable service commands and automated checks. Code and
infrastructure stay in your accounts, where you can inspect and change them.
Shared deployment workflows use service commands so applications can bring their
own stack.

**Harness Engineering for startups: Speed, Control and Scalability.**
Build quickly with reusable agent instructions, tools and automated checks,
while keeping ownership and room to grow.

[Website](https://deploy-your-startup.com) ·
[Get started](https://deploy-your-startup.com/get-started) ·
[Report an issue](https://github.com/Deploy-your-Startup/cli/issues)

## Your first startup

Start on macOS with Git, Chrome, a GitHub account and a Hetzner Cloud account.
Bring a domain or register one during setup. Servers and domains are billed by
your providers.

```bash
curl -fsSL https://deploy-your-startup.com/install.sh | bash -s -- --onboard
```

The installer provides Python 3.14 and the latest stable CLI release, connects
GitHub and checks your setup. The wizard asks for your project name and whether
you already own a domain; without one it offers to register it through Hetzner.
It then shows a launch plan before creating anything. Extra domains and error
tracking are optional.
Interactive terminals show a brief launch animation once, then stop. Set
`STARTUP_NO_ANIMATION=1` to disable it; redirected output, CI and `NO_COLOR` stay static.
Each CLI release bootstraps a tested release of the default application
template; the launch plan shows which one.

If Git is missing, run `xcode-select --install` first. The terminal explains any
missing prerequisites. To resume the wizard after installation:

```bash
startup doctor
startup bootstrap --kind fullstack --provider hetzner
```

The first-deploy path creates Django/FastAPI, Postgres and HTTPS on k3s in your
own accounts. An existing shared deployment repository is preserved. Advanced
options, including Vue templates, are available through `startup bootstrap --help`.

Bootstrap requests a deployment; it does not prove the site is live. Follow the
Actions link, point DNS at your server when needed, then verify your site over
HTTPS. For local development, follow the generated project's README.

## Use the CLI from a coding agent

The CLI ships an agent skill that teaches Claude Code, Codex and OpenCode how
to install, set up and operate `startup`: bootstrap, deployment verification,
secrets and updates, including when to ask before costly or destructive steps.
Install it for every agent found in your home directory:

```bash
startup skills install
```

Use `--agent claude|codex|opencode` to choose agents and `--dry-run` to preview
the paths. Re-run it after updating the CLI so the skill matches your version.
Then start a new agent session and ask it, for example, to launch a landing page
for your domain.

Without the CLI installed yet, add the same skill with a skill installer or as a
Claude Code plugin:

```bash
npx skills add Deploy-your-Startup/cli --skill deploy-your-startup
```

```text
/plugin marketplace add Deploy-your-Startup/cli
/plugin install deploy-your-startup@deploy-your-startup
```

The agent offers the official installer when `startup` is missing.

## Update or remove the CLI

Run the installer again to install the currently published CLI release:

```bash
curl -fsSL https://deploy-your-startup.com/install.sh | bash
```

This updates the CLI only. Use `startup sync` for shared deployment workflows and
roles, and `startup template update` for application template changes.

To remove the managed CLI installation:

```bash
curl -fsSL https://deploy-your-startup.com/uninstall.sh | bash
```

Removal asks for confirmation and preserves projects, Keychain credentials and
cloud resources. Servers continue running and billing until you remove them
separately. Installations made directly with uv are managed with `uv tool uninstall deploy-your-startup-cli`; the website uninstaller handles its own installations.

## Working with coding agents

Open the generated repository in your coding agent and let it read `AGENTS.md`.
The project supplies service commands for local development, linting and tests;
its workflows use the same commands. Describe the application you want, review
the changes and run the relevant checks before deploying.

The CLI also accepts explicit bootstrap options for automation. See
`startup bootstrap --help` for project name, domain, provider, template and
optional authentication settings. `--yes` requires the necessary answers up
front; browser account setup may still require a person to sign in.

## Commands

| Command | Purpose |
|---|---|
| `startup doctor` | Check local first-deploy prerequisites |
| `startup bootstrap` | Create a project with guided account and deployment setup |
| `startup ansible` | Provision, deploy, back up, restore and upgrade infrastructure |
| `startup secrets` | Manage encrypted project configuration |
| `startup sync` | Update shared workflows and roles in your GitHub account |
| `startup template` | Adopt and update application templates with Copier |
| `startup auth0` | Configure and validate optional application login |
| `startup hetzner` | Set up Hetzner projects, tokens and domains through a browser |
| `startup deploy` | Create a repository from a GitHub template |

Use `startup <command> --help` to inspect options. The guided bootstrap is the
complete first-deploy flow; lower-level repository creation is also available
through `startup deploy create --repo-name my-startup`.

## Deploy and operate

Run from your generated project's root. Local commands retrieve the project's
Vault password from macOS Keychain when `--vault-password` is omitted.

```bash
startup ansible deploy --working-directory deployment --environment production --service backend
startup ansible kubeconfig --working-directory deployment --environment production
startup ansible backup --working-directory deployment --environment production
startup ansible update-vms --working-directory deployment --environment production
```

For restore, verify the backup artifacts and explicitly confirm with `--yes`.
Without an explicit backup directory, the CLI selects the latest matching
database and media artifacts under `~/Backups/<project>`; use `--backup-dir`,
`--db-file` or `--media-file` to choose the files you intend to restore. Inspect
`startup ansible restore --help` before overwriting existing data.

Back up before cluster upgrades. Use `startup ansible k3s-upgrade` or
`startup ansible cert-manager-upgrade` with the project's working directory and
production environment. Review the selected target version before running them.

## Manage secrets

Run from the generated project's root. Bootstrap stores the project Vault
password in Keychain; omit `-p` to retrieve it. Preview changes first:

```bash
startup secrets list-vaults --repo deployment
startup secrets update -r deployment --field-random backend_db_password --dry-run
```

Review the encrypted preview in `dry-run-output/`, then repeat without
`--dry-run` to apply it. Coordinate changes with the affected service: updating
a Vault field alone does not change a running database's password.

For a supplied inline secret, use stdin so the value stays out of shell history
and process arguments. This example uses zsh, the default macOS shell. Run the
command, then enter the value at the hidden prompt:

```zsh
vault_file="$PWD/deployment/group_vars/production.yml"
read -rs "?Secret: " secret_value && printf '%s' "$secret_value" | startup secrets update \
  -r "$vault_file" --field-stdin api_key --create-in "$vault_file" --dry-run
unset secret_value
```

Review the preview, then repeat without `--dry-run` to apply it. `--create-in`
adds a field without an existing Vault block; otherwise a missing field is an
error. Use an absolute path because a relative `--create-in` is resolved against
the directory selected by `-r`.

`get-field` prints a decrypted value. Advanced operations such as
`rotate-password` require care: encryption-password rotation does not update
Keychain or the GitHub Actions secret automatically. Inspect the relevant
`startup secrets <command> --help` before proceeding. Prefer stdin over
value-bearing arguments such as `--field-set`.

## Update the shared deployment foundation

```bash
startup sync --dry-run
startup sync
```

Sync copies `Deploy-your-Startup/deploy-template` into your private
`<github-user>/deploy-your-startup` repository and enables private workflow access
for your other private repositories. Projects consume shared workflows and
Ansible roles from that copy. Use the owner/source/target options in
`startup sync --help` when customizing this setup.

Local Ansible commands refresh `deployment/.shared-roles` from Git. CI exports
the bundled roles and uses `--no-refresh` to consume that exported copy. Keep
local refresh enabled and update shared roles through sync rather than editing
`.shared-roles` directly.

The first Git-backed setup records `shared-roles.ref` and `shared-roles.sha256`;
review and commit both files. Subsequent runs reuse that exact commit, and CI
exports must match its checksum. To update roles, first sync the shared
deployment repository, then run
`startup ansible pin --working-directory deployment --version <reviewed-ref>`.
Review and commit both new pin files. `startup sync` alone does not advance an
existing project's pin.

## Update an application template

Copier records your project's template source, baseline and public settings in
`.copier-answers.yml`. Keep that file committed. From a clean project checkout,
preview and apply the same tested target tag or commit:

```bash
startup template update --version <template-tag-or-commit> --dry-run
startup template update --version <template-tag-or-commit>
```

The preview runs an update in a temporary Git clone and shows tracked diffs and
new file names. Resolve conflicts and run affected project checks before
committing the updated files and Copier answers. Existing
`deployment/group_vars` files are preserved. Updates do not commit, push,
deploy or decrypt Vault. The default target is the template's HEAD; pass a tag
or commit for repeatable updates.

For a project without Copier answers, record its original baseline first:

```bash
startup template adopt --version <baseline-tag-or-commit>
```

Review and commit the answers. Adoption records existing customizations; it does
not apply historical template changes. Shared deployment updates remain a
separate `startup sync` operation.

## Optional Auth0 login

Templates declaring Auth0 support in `startup-template.yml` can configure login
during bootstrap with `--auth0-tenant <tenant>`. Install the official Auth0 CLI
and run `auth0 login` first. For non-interactive bootstrap with such a template,
pass the tenant or explicitly choose `--without-auth`.

For an existing project, use `startup auth0 setup --help`. After deployment,
`startup auth0 validate --tenant <tenant> --base-domain <domain>` opens a temporary
browser for a real login and checks the protected API and secure HttpOnly
session without saving authentication state.

## Contributing

Clone this repository and install the locked development dependencies. Use
global uv, mise and the Python version committed in `.python-version`:

```bash
git clone https://github.com/Deploy-your-Startup/cli.git
cd cli
uv sync --locked --extra dev
uv run startup --help
mise run lint
mise run test
```

`mise run format` applies formatting and fixes; `mise run lint` only checks
formatting, lint and types. Both use the project-pinned tools through uv. The
underlying `make.sh` exposes the same commands.

To install your checkout as a global tool, run
`uv tool install --reinstall deploy-your-startup-cli --from .` from this repository.
For focused tests, see [tests/README.md](tests/README.md). Read
[AGENTS.md](AGENTS.md) for contributor conventions and product direction.
Maintainers publish releases as described in [CLI releases](docs/releases.md).

## Support and license

Report reproducible problems in [GitHub Issues](https://github.com/Deploy-your-Startup/cli/issues).
Include your OS, command and redacted error output. Keep passwords, tokens and
decrypted configuration out of reports.

MIT (as declared in `pyproject.toml`).
