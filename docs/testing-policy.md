# Testing policy and completion evidence

Integration tests are the default. Every behavior change needs a test of the
real application or tool; changed user flows also need E2E coverage. Use explicit
GIVEN / WHEN / THEN, independent fixtures and observable outcomes. Exercise real
persistence, scripts and public interfaces. Do not mock our own code; use local
stand-ins only for external boundaries that cannot run locally.
GIVEN / WHEN / THEN tests are the specification: do not write separate spec
documents. Specs that are not executed drift from the code; tests are checked
on every change. Record why a behaviour was chosen in an ADR or the change
description.

Unit tests are reserved for complex algorithms with meaningful edge cases or
invariants. Explain why isolated coverage is useful. They supplement integration
and E2E tests and never replace the required application scenarios. Routine
helpers, views, models and service workflows stay covered through integration
tests. Do not add unit tests merely to increase a coverage percentage.

## E2E coverage

Maintain a list of critical user flows and the integration/E2E scenarios that
cover them. For each affected flow cover success, invalid input and denied access
where applicable. A bug fix starts with a scenario reproducing the failure.
Browser checks should use the real running application and semantic locators;
tool E2E checks exercise the real command and its observable result.

A page-loading smoke test covers that specific scenario. It does not establish
that registration, writes, authorization, uploads or external integrations work.
Record uncovered flows in project documentation or issues and extend coverage as
those flows are changed. Keep optional provider checks explicit and record the
credentials or environment they require without storing secrets.

## CI and release evidence

A green pipeline means the enabled checks passed. It does not prove complete
coverage of every feature. Report executed, failed and skipped checks separately;
a skipped provider scenario is unverified, not a pass. State remaining gaps when
reporting completion. Local checks alone do not prove a production deployment.

Preserve E2E as a deployment gate where the project deploys an application.
Every enabled shard must succeed before publishing or deployment. Use isolated
data for each shard, retain reports and failure logs, and clean up disposable
resources. Improve speed through caching and independent parallel checks rather
than removing required scenarios or hiding failures. Use the project's existing
make.sh and mise commands; shared workflows consume the same service contracts.

The portable documentation contract can be checked without application services:

```bash
python3 tests/test_agent_links_integration.py
```

Application integration and E2E checks remain the project's own commands.
