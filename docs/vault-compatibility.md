# Ansible Vault compatibility

Changes to Vault handling, including replacing Ansible Python imports with
`ansible-vault` subprocesses, must preserve the existing inline-field behavior.
Before refactoring, read the current commands and tests and capture any additional
user requirements as integration scenarios. Do not assume a subprocess migration
preserves behavior without exercising it.

- Drive the real `startup secrets` CLI against temporary YAML files and the real
  installed `ansible-vault` executable. Do not mock encryption, decryption, rekeying,
  our Vault code or subprocess execution. Use synthetic test secrets only.
- Cover targeted inline replacement, adjacent and nested fields, varying input
  indentation and the established two-space indentation of rewritten blocks.
  Assert that unrelated fields, comments and surrounding content survive.
- Cover multiline secrets, Unicode, leading/trailing whitespace and final
  newlines. Verify the exact decrypted value with real Ansible; do not use
  `.strip()` in assertions to hide changes to secret contents.
- Cover multiple fields and files, missing-field errors, explicit `--create-in`,
  full-file Vault operations and password rotation for both inline and full-file
  encryption. Verify that rotated data opens with the new password and fails
  with the old one.
- Cover stdin input, password-provider integration, dry runs and wrong-password
  failures. An unavailable OS Keychain may use a local boundary stand-in, but
  Ansible must remain real. Assert dry runs leave files unchanged and failures
  do not corrupt them. Check that secrets never enter child-process arguments
  or diagnostic output; only an explicitly requested secret-read result may
  contain plaintext on stdout.
- Run the template integration matrix for Vault changes: render the real
  Django/FastAPI and Vue + Django/FastAPI templates at pinned public revisions,
  then exercise single-field, mixed multi-file and all-field updates, dry runs,
  and bootstrap's batch secret setup plus strict rotation for Hetzner and BYOS.
  Use disposable rendered projects and synthetic credentials; never provision
  cloud infrastructure for these tests. `mise run test-vault` runs this matrix
  together with the other Vault regression tests.
- Treat these as required checks, not claims that all cases already work. Record
  uncovered behavior or failures explicitly before claiming compatibility.
