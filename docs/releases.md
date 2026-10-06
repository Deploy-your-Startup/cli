# CLI releases

Merging to `main` runs CI; it does not publish an installer update. The public
installer installs a fixed Git tag with uv. GitHub releases describe those tags.
PyPI publishing is a separate, optional manual workflow.

## Publish a tested version

1. Use a clean checkout of current `origin/main`. Check the README, AGENTS.md,
   package metadata, license, CLI help and public quickstart together. Update the
   README's installer version for the release and commit the documentation.
2. Run `mise run lint` and `mise run test`. Push the release commit to `main`
   through the repository's normal review process, then wait for that exact
   commit's GitHub CI to pass. Do not release an older local checkout.
3. Choose an unused `vX.Y.Z` tag. Record the verified commit SHA, then create the
   release against that exact SHA:

   ```bash
   gh release create vX.Y.Z --repo Deploy-your-Startup/cli \
     --target <verified-main-commit-sha> \
     --title "vX.Y.Z — <release title>" --notes-file release-notes.md
   ```

   `setuptools-scm` derives the package version from Git tags. Creating this
   release does not run `publish.yml` or upload to PyPI.
4. Verify the remote tag resolves to the intended commit and the public source
   archive downloads. Install that tag in an isolated tool directory and verify
   `startup --version`, `startup --help` and the affected user flow. For terminal
   animations, use an interactive terminal; redirected output stays static.
5. In `philipp-lein/deploy-your-startup-website`, change the CLI tag in
   `frontend/public/install.sh`. Keep the application template tag independent.
   Run `mise run test` and `mise run build`, then push the tested change to
   `main`. Its Deploy workflow tests and deploys the website to Cloudflare Pages.
6. Wait for the website deployment to succeed. Check the live `install.sh`
   contains the new tag and is shell code, then verify the live homepage and
   `/get-started/`. Only then report the installer update as published.

Existing installations do not update automatically. Users rerun:

```bash
curl -fsSL https://deploy-your-startup.com/install.sh | bash
```

To install and start guided onboarding:

```bash
curl -fsSL https://deploy-your-startup.com/install.sh | bash -s -- --onboard
```

## Optional PyPI publishing

`.github/workflows/publish.yml` runs only through `workflow_dispatch`. It builds
a wheel and source distribution, then uploads through the `pypi` environment
using Trusted Publishing. Configure the PyPI project and publisher first. If
publishing a release, dispatch against its verified tag, not moving `main`.
GitHub tags remain the documented install path until PyPI availability is
verified.

## Withdraw a release

Do not move or reuse a published tag. Mark a faulty GitHub release as a
prerelease, explain the withdrawal, restore the last working installer pin and
deploy it. Publish the fix under a new version. A release label alone does not
change what the installer downloads.
