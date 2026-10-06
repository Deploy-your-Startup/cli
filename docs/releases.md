# CLI releases

Merging to `main` runs CI; it does not publish an installer update. The public
installer installs a fixed Git tag with uv. GitHub releases describe those tags.
The CLI is not published to PyPI.

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

   `setuptools-scm` derives the package version from Git tags.
4. Verify the remote tag resolves to the intended commit and the public source
   archive downloads. Install that tag in an isolated tool directory and verify
   the installed package version, `startup --help` and the affected user flow. For terminal
   animations, use an interactive terminal; redirected output stays static.
5. The website's Deploy workflow checks the latest stable CLI release every ten
   minutes, updates `frontend/public/install.sh`, tests and commits the new pin,
   then dispatches a fresh website deploy. GitHub may delay scheduled runs.
   Dispatch Deploy manually to check sooner. Drafts, prereleases and older
   versions are skipped; the application template tag stays independent.
   See the website repository's `docs/cli-installer-updates.md` for details.
   For a manual update or rollback, change the installer pin in
   `philipp-lein/deploy-your-startup-website`, run `mise run test` and
   `mise run build`, then push the tested change to `main`.
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

## Withdraw a release

Do not move or reuse a published tag. Mark a faulty GitHub release as a
prerelease, explain the withdrawal, restore the last working installer pin and
deploy it. Publish the fix under a new version. A release label alone does not
change what the installer downloads.
