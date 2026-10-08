---
name: deploy-your-startup
description: Use when the user wants to install, set up or operate the Deploy Your Startup `startup` CLI — create a new startup or landing page (`startup bootstrap`), check prerequisites (`startup doctor`), deploy or provision servers, manage Ansible Vault secrets, sync shared deployment workflows or update an application template. Also use when a repository contains `deployment/group_vars`, `.copier-answers.yml` or workflows that call `deploy-your-startup`.
---

# Deploy Your Startup

The `startup` CLI takes an idea from the first commit to a live application in
the user's own accounts: a GitHub repository, servers on Hetzner (or an
existing server), encrypted deployment configuration and GitHub Actions that
deploy on every push to `main`. Code, servers, secrets and deployments stay
with the user. Your job is to drive the CLI for them — never to replace it with
hand-written scaffolding, raw `ansible-vault` or ad-hoc `ansible-playbook` runs.

## 1. Make sure the CLI is ready

1. Check `command -v startup`. If it is missing, offer the official installer
   and run it only after the user agrees:

   ```bash
   curl -fsSL https://deploy-your-startup.com/install.sh | bash
   ```

   The installer brings its own Python and puts `startup` into
   `~/.local/bin`. Early access supports macOS only. If Git is missing the user
   first runs `xcode-select --install`.
2. Run `startup doctor`. It is read-only. Fix each `MISSING` line with the
   command it prints (for example `gh auth login --git-protocol https --scopes
   repo,workflow,read:packages,write:packages`). Commands that need the user's
   browser or password — GitHub login, Hetzner sign-up — are theirs to
   complete; tell them exactly what to run and wait.
3. When in doubt about an option, read `startup <command> --help` instead of
   guessing. The help output is the source of truth for the installed version.

## 2. Create a new startup

Ask **one** bundled question with only what is missing:

- **Kind** — `pitch` (landing page, waitlist, coming-soon page on Cloudflare
  Pages; few inputs) or `fullstack` (Django/FastAPI, Postgres and HTTPS on
  k3s; more inputs). Prefer `pitch` for marketing pages and `fullstack` for
  anything with a backend. Ask this first if it is unclear.
- **Project name** in kebab-case and the **base domain**.
- For `fullstack`: **provider** `hetzner` (default) or `byos` with the existing
  server's host; whether the domain is **already owned** (default) or should
  be bought.

Optional, ask only if the user brings it up: extra domains, a Sentry DSN, the
Vue template, Auth0 login (`--auth0-tenant`; otherwise pass `--without-auth`).

Before running anything, state the cost: servers and domains are billed by the
user's providers. Then prefer the unattended form — every question has an
option, and `--yes` turns missing answers into errors naming the option:

```bash
startup bootstrap --yes \
  --kind fullstack --provider hetzner --domain-owned \
  --project-name my-startup --base-domain example.com \
  --without-auth
```

```bash
startup bootstrap --yes --kind pitch \
  --project-name my-startup --base-domain example.com
```

Built-in full-stack and pitch templates use the tested releases pinned by the CLI.
The launch plan shows the selected version. Use `--template-version <tag-or-commit>`
to choose another version; custom `--template` sources default to HEAD.

Use `--output-dir <dir>` when the user keeps projects in a specific folder.
Run bootstrap outside a sandbox: the Hetzner and Cloudflare steps can open a
real browser for the user to sign in. If no token is passed, that browser path
is used. Pass tokens via `--hetzner-token-stdin`, never as an argument.

Bootstrap is resumable — every step checks whether its outcome already exists.
After an interruption, run the same command again rather than cleaning up by
hand.

## 3. Verify the launch

Hetzner full-stack bootstrap now verifies deployment by default (up to 900
seconds). `--no-verify` requests background setup; `--verify-timeout` changes the
wait. If verification fails, keep the configured project and rerun the read-only
check after correcting DNS or the failed workflow:

```bash
startup verify --working-directory <project> --timeout 900
```

The command prints the required A record and checks Actions, DNS, TLS and
application health. BYOS/custom infrastructure needs `--server-ip <IPv4>`;
`--health-path` selects a custom JSON health endpoint. A nonzero exit means the
site is not verified live.

Bootstrap requests a deployment; it does not prove the site is live. Before
reporting success:

1. Follow the printed GitHub Actions link, or run
   `gh run list -R <owner>/<project> --limit 3` and
   `gh run view -R <owner>/<project> <run-id> --log-failed` on failure.
2. Make sure the domain's DNS points at the server when bootstrap says so.
3. Check the site over HTTPS, e.g. `curl -sSI https://example.com`.

Report what you actually verified. Local checks alone do not establish a
successful production deployment.

## 4. Operate an existing project

Run these from the project's root directory. `startup ansible` commands take
`--working-directory deployment --environment production` (abbreviated as
`…` below); `main` deploys to production. Omit `--vault-password` so the
password comes from the Keychain.

| Task | Command |
|---|---|
| Deploy all or one service | `startup ansible deploy … [--service <tag>]` |
| Provision or change servers | `startup ansible infrastructure …` |
| Fetch the cluster kubeconfig | `startup ansible kubeconfig …` |
| Check playbook syntax offline | `startup ansible validate --playbook <file> --inventory <file>` |
| Back up / restore | `startup ansible backup …` / `startup ansible restore …` |
| Update server packages | `startup ansible update-vms … [--reboot]` |
| Preview Ubuntu LTS upgrade | `startup ansible os-upgrade … --target-version 26.04 --dry-run` |
| Upgrade k3s / cert-manager | `startup ansible k3s-upgrade …` / `startup ansible cert-manager-upgrade …` |
| Update shared deployment workflows and roles | `startup sync --dry-run`, then `startup sync` |
| Update application template files | `startup template update --dry-run`, then `startup template update` |
| Record a template baseline for an older project | `startup template adopt --version <tag>` |

Keep the two update paths apart: `startup sync` updates the shared deployment
repository in the user's GitHub account; `startup template update` changes the
application files of one project. After a template update, review the diff,
resolve conflicts, run the project's checks and commit together with
`.copier-answers.yml`.

`os-upgrade` previews by default. It is separate from package updates.
Execution uses `--execute --backup-confirmed --health-url https://app.example.com/health`
and requires a reviewed consecutive LTS path offered by Ubuntu. Verify database,
media and cluster backups plus a VM recovery route first. A release containing
the OS upgrade playbook must be synced and the project role pin updated. Nodes
upgrade one at a time; single-node clusters have downtime. Local simulated
integration checks do not establish compatibility: qualify a disposable real
deployment before production. After a controller disconnect, rerun the same
command; node-side failures require operator recovery, not automatic retries.

`os-upgrade --execute`, `infrastructure`, `restore`, `k3s-upgrade` and `cert-manager-upgrade` can
replace servers, delete data or interrupt the site. Explain what will change
and get an explicit yes before running them. Never add
`--allow-worker-teardown` unless the user asked for workers to be removed.

## Shared clusters

For mutually trusted startups in the same owner's accounts, bootstrap one
Hetzner owner with `--shared-cluster`. Verify its first deployment, then run
`startup cluster export --working-directory deployment --output <new-file>`.
Bootstrap additional applications with `--cluster <file> --kind fullstack`,
unique project names and domains. Point their DNS at the same ingress; attachment
creates no servers. Each project has its own namespace, database, media and Vault.

Shared-cluster bootstrap requires a compatible template revision declaring
shared_cluster support; select it with --template-version. Older templates are
rejected before account or server setup.

Run infrastructure, update-vms, os-upgrade, k3s-upgrade and cert-manager-upgrade from the
cluster owner, and account for every attached application before these operations.
Attached projects are application-only. Deployment SSH keys remain administrative:
this mode is for trusted projects, not independent customers. Verify network
policies, quotas and per-startup backups. Local data volumes do not fail over
between nodes. Do not silently convert existing production projects or change
their namespace. Use `--deployment-ref <reviewed-ref>` only with a template that
supports `deploy_ref` when reviewing an unpublished shared workflow branch.

## 5. Secrets

Secrets live as inline Ansible Vault fields in
`deployment/group_vars/<environment>.yml`. The vault password comes from the
macOS Keychain when `-p` is omitted — always omit it.

- Read a value: `startup secrets get-field -f <file> --field <name>`. Do not
  print secrets into the conversation unless the user asked to see that value.
- Generate a random value:
  `startup secrets update -r <file> --field-random <name> --dry-run`, then
  without `--dry-run`.
- Set a value from the shell without exposing it in process arguments, using
  absolute paths (a relative `--create-in` resolves against the `-r` file's
  folder):

  ```bash
  file="$(realpath deployment/group_vars/production.yml)"
  printf '%s' "$SECRET" | startup secrets update -r "$file" \
    --field-stdin <name> --create-in "$file"
  ```

- When the user types a secret themselves, give them a hidden prompt rather
  than asking them to paste it into the chat:

  ```zsh
  read -rs "?Secret: " s && printf '%s' "$s" | startup secrets update -r "$PWD/deployment/group_vars/production.yml" --field-stdin <name> --create-in "$PWD/deployment/group_vars/production.yml"; unset s
  ```

- Replace an encrypted whole-file secret through stdin:
  `startup secrets update -r deployment --file-stdin <filename> --dry-run`,
  then repeat without `--dry-run`. Never put its value in process arguments.

- Rotating the vault password (`startup secrets rotate-password`) is
  destructive for anyone holding the old password; confirm first.

Never log, commit or echo tokens, vault passwords or decrypted secrets, and
never pass them as command-line arguments.

## 6. Guardrails

- Use the CLI for scaffolding, Vault and Ansible. Do not hand-edit
  `deployment/.shared-roles`; it is a git checkout that the CLI refreshes.
- Use `--dry-run` first wherever a command offers it.
- Ask before anything that costs money, deletes data or changes production.
- Respect the user's existing customizations: do not regenerate a project to
  "fix" it; update it with `startup template update`.
- If a command fails, read its message — the CLI names the missing option,
  prerequisite or next step — and fix that before retrying.
