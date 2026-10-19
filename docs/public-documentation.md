# Writing public documentation

Write concise, professional English for someone cloning this repository alone.
Use neutral project names and domains. Keep setup self-contained; omit personal
portfolio history, local machine paths and dependencies on the portfolio hub.
Explain prerequisites, the next action and how the user verifies the outcome.
Check commands against actual CLI options and workflow inputs, Markdown fences,
relative links and public release refs. Pin published versions in quickstarts.
Check live installer content before recommending it; an HTTP 200 with HTML is
not a shell installer. Keep the README consistent with the published quickstart
and document advanced or maintainer procedures separately.

Use `startup` for deployment, Ansible and Vault operations. Secret examples use
Keychain and stdin with an initial `--dry-run`; never put credentials in command
arguments, logs or committed files. Require authorization for destructive
operations. Keep `startup sync` (shared workflows/roles) distinct from
`startup template update` (application files). Verify Actions and HTTPS before
reporting a deployment as successful.

Users pay providers directly; the current no-platform-fee statement applies to
early access.
