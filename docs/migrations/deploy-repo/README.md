# Temporary migration: CLI v0.1.43 → v0.1.47, Factory v0.1.25 → v0.1.26

Remove this directory after both early-access users and the owner's projects have
completed the migration. This is an operator-assisted migration, not a new
permanent onboarding flow. Older versions need inspection before using these steps.

## Maintainer delivery gate

Before using this guide:

- Ship the website installer pin to published CLI v0.1.47 and verify the actual
  `https://deploy-your-startup.com/install.sh` content. Installation alone does
  not migrate an existing Factory or application.
- CLI v0.1.47 also pins Factory v0.1.26 for new workspaces. Existing users
  explicitly update their own Factory below; reinstalling the CLI does not
  apply template changes to existing workspaces.
- Review the local rehearsal report. It verifies local files, not a production
  deployment or GitHub permissions. Test the first real application before
  proceeding with the remaining applications.

## 1. Inventory and preserve local work

In the Factory root, inspect the installed CLI package version, `.copier-answers.yml`,
`projects.yaml`, Git status and remotes. List all registered application folders
and any personal deployment copies. Preserve dirty working trees; do not stash,
reset or overwrite the owner's work automatically. Start each update from a clean,
committed checkout. Use a separate branch for the migration.

If Copier metadata is absent, stop and establish the actual baseline with the
owner; do not guess or adopt the target version as the old baseline.

## 2. Update the CLI

After the delivery gate above, download and inspect the website installer, then
run it without `--onboard` (do not create another Factory):

```bash
curl -fsSL https://deploy-your-startup.com/install.sh -o /tmp/startup-install.sh
# Inspect /tmp/startup-install.sh; it must select v0.1.47 or the reviewed successor.
bash /tmp/startup-install.sh
startup --version
```

CLI v0.1.43 does not support `--version`; inspect its installer environment
metadata when determining the old version. The upgraded CLI supports it.
Check `command -v startup` if the old version is still selected. Do not uninstall
other tools or edit shell configuration blindly.

## 3. Update this Factory

From its clean Git root:

```bash
startup template update --version v0.1.26 --dry-run
startup template update --version v0.1.26
```

Review and resolve conflicts. `projects.yaml` is deliberately preserved by Copier:
update its public tooling entry named `deploy-template` (or an older equivalent)
to `name: deploy` and `repo: https://github.com/Deploy-your-Startup/deploy.git`.
Keep startup registrations, evidence configuration and other owner fields.
Do not replace a deliberately customized deployment fork without review.

Preserve the old local tooling checkout and all its changes. If `projects/tooling/deploy`
already exists, inspect its origin and history before using it; do not overwrite it.
Move a clean superseded tooling checkout outside `projects/tooling` if registry
validation would otherwise report it as unregistered. Do not delete it.

```bash
./scripts/init.sh
mise run registry:check
FACTORY_TEST_PYTHON="$HOME/.local/share/startup/tools/deploy-your-startup-cli/bin/python" \
STARTUP_TEST_EXECUTABLE="$HOME/.local/share/startup/commands/startup" \
mise run test
```

Factory v0.1.26's tests need the installed CLI runtime explicitly; the environment
variables above keep them on the installer's isolated Python instead of an
unrelated system Python. If using a custom CLI installation, select its matching
Python and executable instead.

Setup can clone public tools, install runtimes and agent skills, and request
local access. Review new Copier answers and all changes, commit and push through
the repository's normal review workflow. Never include private configuration in
a public PR. Template updates do not migrate applications.

## 4. Migrate one application first

In that application's clean Git root, inspect custom changes to the old personal
`<owner>/deploy-your-startup` repository and confirm that the central roles and
workflows cover them. GitHub organizations restricting Actions must allow
`Deploy-your-Startup/deploy`. Do not silently discard intentional forks.

```bash
startup migrate deploy --dry-run
startup migrate deploy
```

The preview names the target release and commit; record both. The migration
selects the central repository's current version tag, not the old copy's pin.
The command currently has no `--version` option: review the applied release
against the preview, especially if a release was published in between.

Review `.github/workflows/`, role reference/checksum and any source marker.
Shared build jobs need the permissions requested by their nested workflows;
check existing explicit permission blocks as well. Application Copier answers,
Vault files, infrastructure configuration, domains and namespaces must remain
unchanged. Do not update the application template as part of this migration.

Run the application's prescribed lint and full local tests, including E2E where
available. Commit the reviewed changes. Pushing a production branch may deploy:
obtain the owner's approval for the concrete diff before rolling out. Follow
Actions and verify the application's HTTPS and health after deployment.

```bash
startup migrate deploy --dry-run
startup update --check
```

The first command should now report nothing to migrate. The second should
identify the central release. Repeat for every registered application that
still calls the personal copy; list already migrated or unsupported projects.

## 5. Finish

Keep personal deployment repositories available for rollback. Once all projects
are verified, offer archiving them with separate owner approval; do not delete
repositories or local checkouts. Preserve the Factory repository itself.

Record per user: Factory commit, CLI version, each application commit, target
Deploy release/commit, local checks, production result and remaining work.

- [ ] User 1: Factory and all applications verified
- [ ] User 2: Factory and all applications verified
- [ ] Owner: all applications verified
- [ ] Remove this temporary directory after completion

Removing this guide does not require removing the migration command: decide that
separately, once no supported installations depend on it.
