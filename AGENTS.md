# Agent setup handoff

Role: installation assistant for the local operator. Goal: adapt and verify the native-first combined Hermes Agent + WebUI maintenance workflow, not copy the author's environment or build an unattended updater.

Read README.md, docs/combined-maintenance.md, docs/gateway-contract.md, and docs/integration.md first. docs/setup.md and docs/operations.md govern only the optional older WebUI restart coordinator. Inspect OS, checkouts, versions, lifecycle owners, PID topology, every affected profile/state boundary, and live health before proposing changes. Run the isolated tests first.

Scope includes this kit and operator-approved integration changes. Excluded by default: model routing, credentials, private notes, unrelated services, automatic Git updates, dependency replacement, Linux service implementation, and changes to running workloads.

For an explicitly approved combined maintenance window, use the native updater/manual WebUI update path and operator-controlled recovery in the runbook. No new update daemon, transactional release format, automatic dependency/database rollback, or recurring AI maintenance is authorized by installation. Inventory untracked CLI/desktop/serve/cron/background work before using --other-work-stopped. The attestation is not an automatic discovery result. Disable the legacy coordinator before combined maintenance.

Do not apply patches, install/enable services, restart anything, change architecture, or enable recurring behavior without the local operator's approval of exact paths and services. Do not run activation through the service you will stop. Never treat an example/default as discovery. Preserve dirty work; use disposable worktrees. Secret values remain outside model context; use the operator's existing credential references if their setup requires them. This kit itself needs no credentials.

Stop on missing drain capability, mismatched PID or drain directory, unsupported architecture, dirty ownership ambiguity, incompatible patch, failed tests, unknown health, or inability to recover independently. Do not invent compatibility by fabricating telemetry.

Verification: run all kit tests; run native gateway drain/control and WebUI admission/lifecycle checks in isolated state; exercise both sides busy-to-idle, admission rejection/cancellation, new process generations, gateway code SHA, WebUI process/checkout provenance, deep health, and chat/reconnect. Never mutate live code/dependencies before verifying affected services are stopped. A fixture rollback test is not a real dependency/database restoration test. Preserve sanitized results locally, not in this public repository.

Return: changed paths, exact revisions, tests and their limits, service registration/readback, pending versus activated status, rollback path, and remaining blockers. Report installation as unverified if representative live acceptance could not be completed. No autonomous paid reviewer or provider is needed to use this kit.
