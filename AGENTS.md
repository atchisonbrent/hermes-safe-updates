# Agent setup handoff

Role: installation assistant for the local operator. Goal: adapt and verify this restart kit for their Hermes/WebUI installation, not copy the author's environment.

Read README.md, docs/setup.md, docs/integration.md, and docs/operations.md first. Inspect the target OS, checkouts, versions, lifecycle owner, PID topology, profile/state boundaries, and live health before proposing changes. Run the isolated tests first.

Scope includes this kit and operator-approved integration changes. Excluded by default: model routing, credentials, private notes, unrelated services, automatic Git updates, dependency replacement, Linux service implementation, and changes to running workloads.

Do not apply patches, install/enable services, restart anything, change architecture, or enable recurring behavior without the local operator's approval of exact paths and services. Do not run activation through the service you will stop. Never treat an example/default as discovery. Preserve dirty work; use disposable worktrees. Secret values remain outside model context; use the operator's existing credential references if their setup requires them. This kit itself needs no credentials.

Stop on missing drain capability, mismatched PID or drain directory, unsupported architecture, dirty ownership ambiguity, incompatible patch, failed tests, unknown health, or inability to recover independently. Do not invent compatibility by fabricating telemetry.

Verification: run coordinator tests; run WebUI admission/lifecycle tests in isolated state; exercise a disposable service and then an approved real busy-to-idle turn, admission rejection, replacement generation, deep health, exact revisions, and chat/reconnect. Preserve sanitized results locally, not in this public repository.

Return: changed paths, exact revisions, tests and their limits, service registration/readback, pending versus activated status, rollback path, and remaining blockers. Report installation as unverified if representative live acceptance could not be completed. No autonomous paid reviewer or provider is needed to use this kit.
