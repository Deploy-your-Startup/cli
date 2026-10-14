# Optional startup onboarding

The installer finishes CLI/GitHub setup before offering to create a project.
Project creation asks for a name, template, parent folder, compatible deployment
target, GitHub account confirmation and final approval, in that order. An SSH
key or cloud account cannot block installation completion.

## Before

```mermaid
flowchart TD
    A[Install CLI and runtime] --> B[Require GitHub CLI; sign in]
    B --> C[Full-stack doctor: SSH key and Chrome]
    C -->|Missing| C
    C -->|Pass| D[Optional agent skill]
    D --> E[Bootstrap with fullstack and Hetzner fixed]
    E --> F[Project name, domain and folder]
    F --> G[Confirm plan; configure accounts and project]
    G --> H[GitHub repository, secrets and first push]
    H --> I[Request Hetzner deployment; verify]
```

## After

```mermaid
flowchart TD
    A[macOS and Git available] --> B[Install CLI and runtime]
    B --> C[Reuse GitHub CLI or install official verified binary]
    C --> D{Existing GitHub login or environment token?}
    D -->|Valid| F[Configure Git HTTPS credentials; basic doctor]
    D -->|No| E[Native gh auth login]
    D -->|Invalid environment token| X[CLI installed; correct token and retry]
    E -->|Success| F
    E -->|Cancelled or failed| Y[CLI installed; GitHub not connected]
    F -->|Missing Git or GitHub setup| R[Explain correction and retry]
    R --> F
    F -->|Pass| G[Setup complete; optional agent skill]
    G --> H{Create a startup now?}
    H -->|Later| Z[Exit successfully; later run startup bootstrap]
    H -->|Yes| J[Startup name]
    L[Later: startup bootstrap] --> J
    J --> K{Choose template}
    K --> FOLDER[Choose parent folder; show project path]
    FOLDER --> M{Compatible deployment target}
    M -->|Django or Vue: existing server| P[Owned domain; user manages DNS and server access]
    M -->|Django or Vue: Hetzner| O[Domain: owned or register]
    M -->|Astro: Cloudflare Pages| Q[Domain]
    O --> ACCOUNT{Confirm signed-in GitHub account}
    P --> ACCOUNT
    Q --> ACCOUNT
    ACCOUNT -->|Yes| S[Review launch plan]
    ACCOUNT -->|No or not connected| LOGIN[Native gh auth login; confirm new account]
    LOGIN -->|Success| ACCOUNT
    LOGIN -->|Cancelled or failed| STOP[Exit; no project resources created]
    ACCOUNT -->|Environment token needs changing| TOKEN[Explain token override; exit without project resources]
    S --> T{Create project?}
    T -->|No| U[Exit successfully; no resources created]
    T -->|Yes| V[Check chosen target requirements and server public key]
    V --> W[Configure provider; render project and agent instructions]
    W --> AA[GitHub repository, secrets, first push and deployment configuration]
    AA --> AB[Show configured project and target-specific deployment status]
```

## Behavior and compatibility

- Plain `startup doctor` checks Git, Git identity, GitHub CLI/auth/scopes and uv.
  Add `--provider hetzner` or `--provider byos` for deployment prerequisites.
  Chrome is checked for the Hetzner browser path, not BYOS/basic setup.
- `startup bootstrap` asks for name, template, parent folder, deployment target,
  GitHub account confirmation and final approval. Built-in
  templates use pinned releases: Django/FastAPI v0.1.6, Vue v0.1.5, Astro v0.1.1.
  Existing explicit flags remain supported; `--kind fullstack` bypasses the
  picker and keeps the Django/FastAPI default. `--yes` stays unattended.
- GitHub confirmation shows the signed-in username and proposed repository.
  Declining runs native `gh auth login`, configures Git credentials, then asks
  again using the newly authenticated identity. Missing authentication also
  starts native login. Environment tokens are reused, never silently removed;
  changing their account requires updating/unsetting `GH_TOKEN`/`GITHUB_TOKEN`.
  `--github-username` preserves an explicit owner/organization without another
  account prompt; `--yes` remains unattended. No project/provider resources are
  created before final approval. Login may update the local credential store.
- Server keys are separate from GitHub OAuth/tokens and generated CI keys.
  `--ssh-public-key <path.pub>` supports custom paths. New projects select and
  validate the operator public key before any provider resources are created.
  Invalid/private key files are rejected without printing their contents.
- BYOS asks for an owned domain. Ordinary BYOS rejects `--buy-domain` rather
  than silently ignoring it. The existing shared-cluster domain-order path is
  preserved.
- Installation failure, incomplete GitHub setup and project failure are distinct.
  Existing projects are preserved. Rerun bootstrap using the same name, parent
  folder, stack and provider; this change does not add a new resume database.
- Deployment behavior remains target-specific: Hetzner keeps its Actions/DNS/TLS
  health verification; BYOS prints the existing server setup/deploy commands;
  Astro uses its existing Cloudflare flow. A configured project is not reported
  as a verified live application.

## Rollout across repositories

1. Merge and publish a tested CLI release containing this change.
2. Advance the website installer's fixed CLI pin to that published release using
   the existing release updater.
3. Merge the companion website/installer change only after that pin advances.
   The installer checks for the new doctor interface and refuses to run the old
   full-stack prerequisite loop with an incompatible CLI.
4. Verify the live installer and first-time onboarding on macOS. Existing CLI
   installations update only when their owners rerun the installer.

No unpublished CLI tag is invented in either change. Browser OAuth, Keychain,
macOS binary execution and real cloud deployment still require separate live
verification; local boundary tests do not establish those outcomes.
