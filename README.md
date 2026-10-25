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

Start on macOS with Git and a GitHub account. Server access, browser automation
and provider credentials are needed only after choosing a project and target.
Servers and domains are billed by your providers.

```bash
curl -fsSL https://deploy-your-startup.com/install.sh | bash -s -- --onboard
```

The companion installer flow installs GitHub CLI when missing, reuses an existing
GitHub login or environment token, and otherwise runs `gh auth login`. After the
basic checks it reports **Setup complete** and asks whether to create a startup
now. Choose **No** to finish successfully and run `startup bootstrap` later.
If GitHub login is cancelled, the CLI stays installed and the message explicitly
says that GitHub is not connected.

The interactive wizard asks for the startup name, template (Django/FastAPI,
Vue with Django/FastAPI, or Astro), then the parent folder and deployment target.
Server applications offer Hetzner or your existing server (BYOS); Astro uses
Cloudflare Pages. Only then does it ask for domain/provider settings and show
the signed-in GitHub account and proposed repository for confirmation. Decline
that account to sign in again through `gh auth login`; environment tokens must
be changed in the environment instead. An explicit `--github-username` keeps
its owner/organization without an account prompt. Finally, approve the launch
plan before any project resources are created. Extra domains and error tracking
remain optional. Explicit `--kind`, `--provider`, `--template` and
`--template-version` options still support scripted setup; `--kind fullstack`
keeps the Django/FastAPI default and skips the stack picker.

See [the onboarding flow and rollout](docs/onboarding-flow.md). The website must
publish its companion installer only after a CLI release containing this flow;
merging this CLI change alone does not update the public installer.

Onboarding uses the installer's muted blue, short numbered steps and a compact
project summary. Detailed command logs remain visible. Interactive terminals
show a brief launch animation once, before questions. Set
`STARTUP_NO_ANIMATION=1` to disable motion while keeping color; CI also disables
motion. Redirected output, `TERM=dumb` and `NO_COLOR` use plain output. See the
[terminal design reference](docs/terminal-design.md) for the palette and fallbacks.
Each CLI release bootstraps a tested release of the default application
template; the launch plan shows which one.

If Git is missing, run `xcode-select --install` first. The terminal explains any
missing prerequisites. To resume the wizard after installation:

```bash
startup doctor
startup bootstrap
```

`startup doctor` marks satisfied prerequisites with a green `✓` and missing ones
with a red `✗ Missing:` followed by the requirement and available setup guidance.
Redirected output keeps the symbols without terminal color codes.

`startup doctor` checks CLI/GitHub setup without requiring a server, an SSH key,
a cloud account or Chrome. Use `startup doctor --provider hetzner` or
`startup doctor --provider byos` for target-specific checks. Custom public keys
can be supplied to both doctor and bootstrap with `--ssh-public-key ~/.ssh/work.pub`.
This is your **server-access public key**, separate from GitHub authentication and
the generated CI deploy key. Bootstrap validates it before creating resources for
a new project. For BYOS, its private counterpart must be available through your
normal SSH configuration or agent. Create a key with `ssh-keygen -t ed25519` if
needed; existing keys are never overwritten.

Server templates include Postgres and HTTPS on k3s in your own accounts. An
existing shared deployment repository is preserved. BYOS uses a domain you
already own and does not ask for a Hetzner account. Register a domain separately
with your preferred registrar if needed. Advanced options remain available
through `startup bootstrap --help`.

If your account already has a shared deployment repository, preview
`startup sync --source-version v0.1.6 --dry-run`, review it, then run
`startup sync --source-version v0.1.6` before generating another full-stack
project. The current templates require that release's coverage workflow input.
Sync preserves existing application pins; advance those only after review.

Before provisioning, full-stack Hetzner bootstrap shows the planned resources and
a monthly estimate including VAT from your project’s Hetzner pricing. Confirm
the estimate to continue; the default is No. `--yes` accepts the estimate. Add
`--max-monthly-cost <amount>` to enforce a gross monthly cap in the displayed
currency; bootstrap stops if the estimate exceeds it or pricing is unavailable.
Without a cap, unavailable pricing requires interactive confirmation or `--yes`.
Domain registration is billed yearly; the CLI shows Hetzner's price before
the order is placed. Shared-cluster attachments create no infrastructure
and skip this preview.

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

### Bring your own server

BYOS bootstrap installs the deploy SSH key using your existing server access
before pushing. If SSH access fails, setup stops with a repair command; rerun
bootstrap after restoring access. GitHub Actions performs server setup (including
k3s), builds and deploys the application. Local `make.sh setup`, `infrastructure`
and `deploy` commands are not required for this bootstrap path. Follow the Actions
link, point the domain at your server, and verify DNS and HTTPS.

### Landing page on Cloudflare Pages

Cloudflare browser setup uses installed Google Chrome, a dedicated persistent
profile and `playwright-stealth`. Complete sign-in and any Cloudflare checks in
the browser; setup waits for the rendered **User API Tokens** dashboard before
continuing. Stealth does not guarantee acceptance by Cloudflare. If browser
setup stays blocked, choose **Paste an existing token** in the wizard and enter
the token at the hidden prompt.

Validate an idea before building the application: the pitch template creates a
static [Astro](https://astro.build) landing page that deploys to Cloudflare Pages
on every push to `main`. It needs a GitHub account, a free Cloudflare account and
a domain; no server is created. This flow requires CLI `v0.1.6` or later.

```bash
startup bootstrap --kind pitch --template-version v0.1.2
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
the private GitHub repository, stores the Cloudflare token and account ID as
repository secrets, pushes the code and connects the domain and its `www` subdomain to the
Pages project. Nameserver changes can take a while to propagate; verify the site
over HTTPS once the Actions run has finished. Edit `frontend/src/pages/index.astro`
and run `./make.sh run` to work on the page locally.

Before making the repository public, switch its visibility and run the same
`startup bootstrap` command again. It moves the Cloudflare secrets into a
`production` environment that only `main` can deploy to, removes the repository
copies and requires approval before workflows from fork pull requests run. The
deploy workflow uses that environment in public repositories; GitHub's free
plan offers environments for public repositories only.
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

Removal asks for confirmation and preserves projects, stored Vault passwords and
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
| `startup lint-contract` | Check a project's `make.sh` format/lint contract and CI lint gate |
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

Before registering a domain, check it without opening a browser or placing an order:

```bash
startup hetzner domain-check example.com
```

The command checks DENIC WHOIS for `.de` and IANA-discovered RDAP for other
registrable domains. Exit codes are **0** free, **1** registered and **2**
unknown/error. Hetzner's public TLD feed supplies a standard annual registration
estimate in EUR, including 19% German VAT. Renewal prices are unknown because
the feed does not list them separately; premium domains and your tax location
may change the final price. If prices cannot be read, Hetzner shows them before
you confirm the order. Bootstrap checks before buying and asks before opening
registration; an availability check does not reserve the domain.

To register a domain, sign in to Hetzner in the browser that opens:

```bash
startup hetzner domain example.com --contact-email owner@example.com
```

The command selects an existing Hetzner contact as domain owner: the only one,
the one matching `--contact-email`, or the one you choose in the terminal. It
never creates contacts; create one in the browser if none exists. It then shows
Hetzner's order summary with owner and price. An interactive terminal asks
before the binding order (default No). Without a terminal, `--confirm-order`
accepts Hetzner's terms and places the order; otherwise the command waits until
you order in the browser. `startup bootstrap --buy-domain` accepts the same
choices as `--domain-contact` and `--confirm-domain-order`, including when
attaching to a shared cluster.

## Deploy and operate

Run from your generated project's root. Local commands retrieve the project's
Vault password from the configured backend (macOS Keychain or 1Password, see
[Manage secrets](#manage-secrets)) when `--vault-password` is omitted.

```bash
startup ansible deploy --working-directory deployment --environment production --service backend
startup ansible kubeconfig --working-directory deployment --environment production
startup ansible backup --working-directory deployment --environment production
startup ansible update-vms --working-directory deployment --environment production
```

### Kubernetes access over SSH

`startup ansible kubeconfig` configures an automatic SSH tunnel by default.
Continue using `kubectl` and `k9s` normally: their kubeconfig credential helper
starts the local tunnel on demand, including after a reboot. SSH must work
without a password prompt, using your SSH agent/config or `--ssh-identity`.
An encrypted key must be unlocked in your agent. The CLI does not save Vault
passwords or decrypted deployment SSH keys for reconnecting.

To migrate an existing context or connect directly to a known master:

```bash
startup cluster connect --context my-startup-production --host 203.0.113.10 --namespace my-startup
kubectl --context my-startup-production get pods
k9s --context my-startup-production
```

Verify the server's SSH host key before connecting. `cluster connect` preserves
other contexts and the selected context; an existing namespace is retained
unless `--namespace` is supplied. Each context has its own loopback port and
owner-readable credential file under `~/.kube/startup`. Keep the CLI installed:
the kubeconfig calls the Python environment that configured it. Re-run connect
after moving or replacing that installation. `--ssh-port` and `--identity-file`
support custom SSH setups; `--kubeconfig` selects a single configuration file.

Kubernetes CA and hostname verification remain enabled through the tunnel.
Clients check the tunnel when credentials refresh (every 20 seconds); a network
interruption can briefly interrupt watches or exec sessions. New client
processes reconnect automatically. An idle tunnel exits after two minutes.
Use `startup ansible kubeconfig --direct` for an explicitly configured direct
or private-network API endpoint instead.

After verifying SSH access for every startup on a cluster, set
`firewall_api_source_ips: []` in the owner's production group vars. Preview and
apply only the pinned firewall role:

```bash
startup ansible firewall --working-directory deployment --dry-run
startup ansible firewall --working-directory deployment --execute
startup ansible firewall --working-directory deployment --inspect
```

This keeps the role's website, SSH and cluster-internal rules while removing
the administrator API allowlist. It does not provision or upgrade servers.
Only the cluster owner can apply the shared firewall. Verify SSH, Kubernetes
and public HTTPS again after applying it. Existing tunnels do not themselves
remove an old public API firewall rule.

For restore, verify the backup artifacts and explicitly confirm with `--yes`.
Without an explicit backup directory, the CLI selects the latest matching
database and media artifacts under `~/Backups/<project>`; use `--backup-dir`,
`--db-file` or `--media-file` to choose the files you intend to restore. Inspect
`startup ansible restore --help` before overwriting existing data.

Back up before cluster upgrades. Use `startup ansible k3s-upgrade` or
`startup ansible cert-manager-upgrade` with the project's working directory and
production environment. Review the selected target version before running them.

## Deploy several startups to one cluster

CLI `v0.1.26` pins the compatible Django template `v0.1.7`. Create one cluster
owner with `startup bootstrap --kind fullstack --provider hetzner --shared-cluster`.
This provisions infrastructure in your account and puts the owner's application
in its own namespace. Verify Actions, DNS and HTTPS before attaching another
startup. Cluster failures and upgrades affect every application using it.

For Vue, select `--template https://github.com/Deploy-your-Startup/vue-django-template.git
--template-version v0.1.6`. Older templates without shared-cluster support are
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

Point the second domain at the same cluster ingress. With `--buy-domain`, the
attached startup instead gets its own Hetzner project used only for DNS: its
token is stored in the startup's Vault, and bootstrap creates the zone and A
records through the shared `hetzner-dns` role before ordering the domain. Later
DNS changes run with `startup ansible dns --working-directory deployment
--environment production`, which provisions nothing. Its Vault, deployment SSH
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

Run from the generated project's root. Each project's Vault password is
stored as `VAULT_PASSWORD_<PROJECT>` (for example `VAULT_PASSWORD_EXAMPLE_APP`)
in the computer's password backend; omit `-p` to retrieve it.

- **macOS Keychain** (default): bootstrap stores the password in Keychain and
  waits until you have also saved it in your password manager under the same
  name. On another Mac, the first command that needs the password asks for that
  copy, checks it against the project's Vault and stores it in that Mac's
  Keychain.
- **1Password**: bootstrap stores the password as a Password item in a
  1Password vault, so every computer signed in to that account can deploy. It
  needs the [1Password CLI](https://www.1password.dev/cli/) with the desktop app
  integration turned on (Settings → Developer → Integrate with 1Password CLI);
  each read is approved in the 1Password app.

Bootstrap offers 1Password once when `op` is signed in. Change the choice for
this computer at any time; `STARTUP_VAULT_BACKEND=keychain|1password` overrides
it for a single run:

```bash
startup config set vault-backend 1password --op-vault Startups
startup config show
startup secrets copy-password -r . --from keychain
```

The setting lives in `~/.config/startup/config.toml` and holds no secrets.
`copy-password` checks the password against the project's Vault before it
writes it and leaves the source entry in place. Preview secret changes first:

```bash
startup secrets list-vaults --repo deployment
startup secrets update -r deployment --field-random backend_db_password --dry-run
```

Review the encrypted preview in `dry-run-output/`, then repeat without
`--dry-run` to apply it. Coordinate changes with the affected service: updating
a Vault field alone does not change a running database's password.

For a supplied inline secret, keep the value out of shell history and process
arguments: omit `--value`, or pass `-` as the value of `--field-set`. In a
terminal the CLI asks for the value twice at a hidden prompt; when stdin is
piped, it reads the value from stdin instead:

```bash
vault_file="$PWD/deployment/group_vars/production.yml"
startup secrets update -r "$vault_file" --field-set api_key - --create-in "$vault_file" --dry-run
```

Review the preview, then repeat without `--dry-run` to apply it. `--create-in`
adds a field without an existing Vault block; otherwise a missing field is an
error. Use an absolute path because a relative `--create-in` is resolved against
the directory selected by `-r`. Repeat `--field-set <name> -` to be prompted for
several fields in one run; piped stdin feeds only one field per invocation.

To set or add a single field in one file, `update-inline-field` prompts the same
way; `--create` appends the field when the file has no Vault block for it:

```bash
startup secrets update-inline-field -f deployment/group_vars/production.yml \
  --field api_key --create
```

`get-field` prints a decrypted value. Advanced operations such as
`rotate-password` require care: encryption-password rotation does not update
the password backend or the GitHub Actions secret automatically. Inspect the relevant
`startup secrets <command> --help` before proceeding. Prefer a hidden prompt or stdin
over a value in the arguments, such as `--field-set <name> <value>`.

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

Copier keeps earlier divergences as customizations, so both modes also compare
the agent harness files the template owns (template ADRs 0001-0099, testing
policy, commit conventions and their link check) with a fresh render at the
target version. A warning lists files that differ, are missing, or use a
template ADR number without coming from the template. Templates can declare
their own list under `harness` in `startup-template.yml`. The warning does not
change files or the exit status; align the files or record why they diverge.

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

### Templates with shared KPI tracking

Templates declaring `kpis: true` in `startup-template.yml` request shared
`oauth2-proxy` and `kpis` roles before project setup. Bootstrap syncs an older
owner deployment copy when those roles are missing, records its immutable role
revision and checksum, creates a stable public website UUID, and encrypts three
independent credentials: `kpis_db_password`, `kpis_app_secret` and
`kpis_admin_password`. Resuming a configured project preserves the UUID and
credentials. Templates without this capability retain their existing behavior.

Read the admin password with `startup secrets get-field -f deployment/group_vars/production.yml --field kpis_admin_password`. Use the
project's `kpis_enabled` setting to stop frontend tracking, without deleting
stored analytics. Auth0 setup supports the shared `oauth2_proxy_enabled` toggle
and retains `oauth_enabled` for older templates with a local proxy role.

## Service lint contract

Every service exposes `./make.sh format`, which writes fixes, and
`./make.sh lint`, which only checks; CI calls only `lint`. Run
`startup lint-contract --project-dir ./my-startup` to check the committed files
without running any tools or changing anything. It fails when:

- a `make.sh` lacks `set -e`, or implements `lint` without `format` (or the reverse);
- a script or mise task calls `uvx` instead of tools pinned in `pyproject.toml`;
- a Python service has no `.python-version`, ty is not pinned exactly
  (`ty==<version>`), or a Django service lacks `django-stubs`;
- `mise.toml` manages `uv` or `python`, or the project ships Git hooks such as
  `.pre-commit-config.yaml`;
- a workflow calling `build-and-deploy-service` has no `pull_request` trigger,
  or a deployed service with `lint` is not gated by `run_lint: true`.

A pass confirms the contract's structure, not that `./make.sh lint` succeeds;
run that, or let CI run it, for the actual checks.

## Public legal information and consent

Use `startup doctor --project-dir ./my-startup` to check the generated public
policy configuration without contacting accounts or checking machine prerequisites.
It fails on missing operator details, pending review and invalid analytics settings;
it does not certify legal compliance or live behaviour.

New compatible templates contain `site-policy.json` under `frontend/src/data/`
(Vue) or `backend/project/` (Django). Complete the operator name, postal address,
contact email and project-specific privacy notice; set `reviewed: true` only
after review. `additionalImprint` holds applicable registration/representation
information. `analytics` accepts `off` or `consent`, and `revision` is a positive
integer increased when consent purposes change. Django currently supports `off`
only. These are public values; never place secrets in this file.

Bootstrap accepts `--site-policy /absolute/path/to/operator.json` to install an
already reviewed configuration in a compatible template before the first commit.
It preserves a matching configuration on resume and rejects conflicting operator
data. Without this option the generated pages remain explicit drafts and optional
analytics stays off. Older template releases do not support this option; select a
reviewed compatible revision explicitly until a compatible release is published.

Vue uses locally bundled CookieConsent (`vanilla-cookieconsent`, MIT), with no
account or hosted consent service. Analytics needs both valid runtime Umami
configuration and a reviewed policy with `analytics: consent`. Accept, reject and
withdraw are available; withdrawing reloads the page to discard tracker listeners.
Shared-role sync alone cannot add these application pages or consent controls.


### Your startup Factory

The guided installer creates a local Factory before your first startup. Its
versioned workspace includes an empty startup registry, public contribution
repositories and optional Sprowi automation. CLI-only installation remains
available without onboarding.

```bash
startup factory onboard
```

Choose a workspace folder (default `~/Projects/startup-factory`). When another
folder already uses that name, onboarding suggests the next free one, such as
`~/Projects/startup-factory-2`, and asks again instead of stopping. Setup installs
missing workspace tools using Homebrew when available, installs the declared
mise runtimes and clones public tooling. Without Homebrew it prints the missing
prerequisites; install them and rerun with the same folder. Existing Factory files
and applications are preserved. Cloud setup starts only after bootstrap's plan
and approval. Sprowi remains optional and requires separate engine credentials.

To create only the workspace:

```bash
startup factory init --project-dir my-factory --version v0.1.4 --setup
cd my-factory
startup bootstrap
```

Factory v0.1.4 is pinned by the CLI. `--template` and `--version` select a reviewed
alternative; new/empty destinations and matching existing Factories are accepted.
A retry never applies template updates. Initialize Git and commit generated files
including `.copier-answers.yml` before `startup template update`.

Inside a Factory, bootstrap defaults to `projects/startups`. It registers the
actual Git remote in `projects.yaml` even if a later step fails. Existing entries
and comments survive, and conflicting repository mappings stop setup. Only name
and repository are recorded; verify Actions, DNS, HTTPS and cluster access before
adding live deployment metadata. For an already cloned startup, use
`startup factory register projects/startups/example-app`.
