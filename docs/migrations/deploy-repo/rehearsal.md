# Local migration rehearsal — 2026-11-17

The disposable `factory-2` workspace was created by an isolated installation of
published CLI v0.1.43 using its default Factory v0.1.25. The test application was
rendered from that CLI's published Django template v0.1.10 with synthetic public
settings. No cloud resources, remote repositories or production data were created.

The candidate CLI (based on v0.1.46, released as v0.1.47) then:

1. Previewed and applied Factory v0.1.26. The owner registry and a custom file
   remained byte-for-byte unchanged. Copier recorded v0.1.26 without conflicts.
2. Explicitly replaced the preserved `deploy-template` tooling entry with the
   central `deploy` source, retaining the application registration.
3. Ran real setup: public tooling checkouts, local agent skills and runtimes.
4. Previewed and migrated the old application to central Deploy v1.0.0,
   commit `64ec3a5f13de5328f02272cccaa3bcffde768fcf`.
5. Verified that application Copier answers and deployment group-variable files
   remained unchanged, workflow references moved, role pins were written and
   shared-build permissions were present.
6. Committed locally and repeated migration: nothing to migrate, clean Git status.
   `startup update --check` reported central v1.0.0 up to date.

The Factory registry check needs the sample application's declared origin;
this was configured locally, without creating that remote repository.
Factory tests require an explicit installed CLI/Python in this older Factory
release. An isolated HOME must use the real GitHub CLI binary rather than a
personal account-selecting wrapper. The CLI tests require the sibling Deploy
checkout (or `STARTUP_TEST_DEPLOY`) and valid RFC 5280 test certificates under
Python 3.14. These environmental problems were identified during full checks.

This proves the local migration and repeatability. It does not prove production
rollout, organization Actions permissions or compatibility with private custom
roles. The first user application must still be rolled out and verified before
the remaining applications.
