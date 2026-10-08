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

Full-stack Hetzner bootstrap requests deployment, prints the required DNS A
record once the server exists, and verifies the latest Actions runs, DNS, TLS
and `/api/health`. It waits up to 900 seconds by default. Failed or unfinished
checks return a nonzero exit code while preserving the configured project.
Correct the DNS record at its authoritative provider, then repeat only the
read-only verification:

```bash
startup verify --working-directory ./my-startup --timeout 900
```

Use `--verify-timeout <seconds>` to adjust bootstrap's wait, or `--no-verify` to
request deployment in the background. Background setup is not proof that the
site is live. For BYOS or custom infrastructure, pass `--server-ip <IPv4>` to
verification; a custom application can use `--health-path` for an endpoint
returning HTTP 200 with `{"status":"ok"}`. For local development, follow the
generated project's README.

### Landing page on Cloudflare Pages

Validate an idea before building the application: the pitch template creates a
static [Astro](https://astro.build) landing page that deploys to Cloudflare Pages
on every push to `main`. It needs a GitHub account, a free Cloudflare account and
a domain; no server is created. This flow requires CLI `v0.1.6` or later.

```bash
startup bootstrap --kind pitch --template-version v0.1.0
```

The wizard opens the Cloudflare dashboard and asks for an API token with these
account permissions: **Cloudflare Pages: Edit**, **Account Settings: Read** and
**Zone: Edit**. Cloudflare then has to serve DNS for the domain:

- A subdomain of a zone already on Cloudflare needs no further changes.
- A domain registered with Hetzner gets its nameservers switched in KonsoleH.
- A domain at another registrar: set the displayed Cloudflare nameservers there.
- Without a domain, the wizard opens Hetzner domain registration for you to
  review and confirm.

Bootstrap renders the
[pitch template](https://github.com/Deploy-your-Startup/pitch-template), creates
the GitHub repository, stores the Cloudflare token and account ID as repository
secrets, pushes the code and connects the domain and its `www` subdomain to the
Pages project. Nameserver changes can take a while to propagate; verify the site
over HTTPS once the Actions run has finished. Edit `frontend/src/pages/index.astro`
and run `./make.sh run` to work on the page locally.
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

## Deploy several startups to one cluster

CLI `v0.1.9` pins the compatible Django template `v0.1.3`. Create one cluster
owner with `startup bootstrap --kind fullstack --provider hetzner --shared-cluster`.
This provisions infrastructure in your account and puts the owner's application
in its own namespace. Verify Actions, DNS and HTTPS before attaching another
startup. Cluster failures and upgrades affect every application using it.

For Vue, select `--template https://github.com/Deploy-your-Startup/vue-django-template.git
--template-version v0.1.2`. Older templates without shared-cluster support are
rejected before account or server setup.

From the owner's project, export its public connection settings:

```bash
startup cluster export --working-directory deployment --output ../cluster-connection.yml
```

Create another project with its own domain and repository:

```bash
startup bootstrap --yes --kind fullstack \
  --cluster ../cluster-connection.yml \
  --project-name second-startup --base-domain second.example.com --without-auth
```

Point the second domain at the same cluster ingress. Its Vault, deployment SSH
key, namespace, database and media volumes are independent. Deploy normally with
`startup ansible deploy`; cluster provisioning, package updates, Ubuntu release upgrades and cluster
upgrades must run from the owner project. Attached projects cannot perform these
operations through the CLI. No additional server is provisioned by attachment.

This mode currently requires public SSH access and mutually trusted projects
managed by the same operator. Deployment keys have administrative server access;
it is not a security boundary between independent customers. The default k3s
network-policy controller must remain enabled. Each namespace receives resource
defaults, quotas and a network policy allowing its own pods, ingress from
`kube-system`, DNS and public IPv4 destinations. Private external dependencies
need an explicitly reviewed network-policy change. Quotas limit scheduling and
container budgets; they do not reserve capacity or limit hostPath disk usage.

Postgres and media remain pinned to their data node. Adding workers does not
provide storage failover. Review quotas for your workload and take separately
verified backups for every startup before shared infrastructure changes. A
public descriptor contains addresses and identity, never credentials; regenerate
it at a new path after changing nodes. Existing startups keep their current
namespace and infrastructure unless explicitly migrated with backup/restore.

For reviewing an unpublished shared deployment branch, use
`--deployment-ref <reviewed-ref>` during bootstrap with a template supporting
`deploy_ref`. The generated workflows and initial local shared-role checkout use that ref;
an explicit local role pin remains authoritative.

### Ubuntu LTS release upgrades

`update-vms` updates packages within the installed Ubuntu release. Use
`os-upgrade` for a consecutive LTS release upgrade. Preview is the default:

```bash
startup ansible os-upgrade --working-directory deployment --environment production --target-version 26.04 --dry-run
```

The command requires the release upgrader installed on each node, `Prompt=lts`
in `/etc/update-manager/release-upgrades`, and a pinned shared deployment release
containing `os-upgrade-playbook.yml`. After syncing such a release, use
`startup ansible pin --working-directory deployment --version <synced-commit-sha>`
and review and commit both role pin files. Sync alone does not move an existing
immutable project pin.

Execution requires a healthy k3s cluster, a target offered by Ubuntu and a
reviewed path in the shared role policy. The initial policy is 24.04 → 26.04.
Preview can inspect future LTS targets; execution remains blocked until their
paths are reviewed. Local integration tests use disposable Ubuntu nodes and
simulate release upgrades, kernel reboots and k3s; they do not establish live
26.04 compatibility. Qualify the upgrade on a disposable deployment matching
your production setup before upgrading production.

Verify database, media and cluster backups plus a VM recovery route, then run:

```bash
startup ansible os-upgrade --working-directory deployment --environment production --target-version 26.04 --execute --backup-confirmed --health-url https://app.example.com/health
```

Repeat `--health-url` for additional applications. Checks require HTTPS with a
valid certificate and HTTP 200 without redirects, before changes and after
each node. `--limit` accepts a single hostname, inventory group or glob and
restricts the selected environment. Nodes are upgraded one at a time; each
must pass package, k3s, workload and application checks before the next starts.
Multi-node clusters are drained without forcing eviction or deleting local
pod data. Single-node clusters incur downtime during upgrade and reboot.

If the controller disconnects, rerun the same command. The driver waits for an
active upgrade and verifies completed nodes rather than upgrading them again.
A failed node stops the fleet; it may remain cordoned. Inspect
`/var/lib/startup/os-upgrade/upgrade.log`, `result.json` and
`/var/log/dist-upgrade` through your server console before recovery. A failed
or interrupted node-side upgrade requires operator recovery; the command does
not retry it or roll it back automatically. Use the verified VM recovery route
when necessary, and uncordon a node only after it is healthy.

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
deploy or decrypt Vault. Commit local template changes before using `HEAD` so
Copier records a reachable baseline for later updates. The default target is the
template's HEAD; pass a tag or commit for repeatable updates.

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

For an encrypted whole-file secret, pipe its contents to
`startup secrets update -r deployment --file-stdin <filename> --dry-run`,
then repeat without `--dry-run`. Exact UTF-8 contents are preserved.

Saved Hetzner tokens are reused only for their recorded project. For isolated
automation, set `HETZNER_BOOTSTRAP_TOKEN_FILE` to a dedicated absolute path.
An explicitly supplied token takes precedence over saved credentials.
