# First-deploy beta test

Run this with a tester's own accounts on macOS. Do not use maintainer credentials.
The tester needs Git, Chrome, GitHub CLI, a Hetzner account and a domain they own.
Cloud resources incur provider charges.

Follow the published [quickstart](https://deploy-your-startup.com/get-started).
Record the CLI/template tags, macOS version, CPU architecture and the step where
each problem occurs. Keep credentials and decrypted secrets out of reports.

- Install through the website command, then open a new terminal.
- Log into GitHub and resolve every `startup doctor` prerequisite.
- Bootstrap a project with the default Django/FastAPI template.
- Verify the private application and shared deployment repositories belong to
  the tester. Check the infrastructure and backend Actions runs succeeded.
- Configure DNS and verify the application over HTTPS.
- Change a visible application detail, push to `main` and verify it goes live.
- Run the installer again; confirm the CLI runs and project files are unchanged.
- Remove the CLI with the published uninstaller; confirm the project and
  credentials remain. Reinstall and confirm the existing project is usable.
- Record feedback through the repository's bug report form.

Deleting the CLI does not delete servers. The tester must authorize cloud cleanup
separately and verify that test resources no longer incur charges.

Do not mark this test complete based on local installer tests or mocked account
flows. It passes only after the tester's Actions and live application are verified.
