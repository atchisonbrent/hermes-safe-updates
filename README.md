# Hermes safe updates

Wait for work to finish before restarting Hermes WebUI.

A small, self-contained reference kit extracted from a working personal deployment: the restart coordinator, regression tests, WebUI admission-control patch, and setup/recovery guide. No private configuration, credentials, paid model, or private repository is required.

**This is a restart coordinator, not an automatic updater.** You choose and test updates; it notices changed Agent/WebUI Git revisions, waits for idle, drains WebUI work, restarts through launchd, and checks for a healthy new server generation.

## Start here

```sh
git clone https://github.com/atchisonbrent/hermes-safe-updates.git
cd hermes-safe-updates
python3 -m unittest discover -s tests -v
python3 scripts/webui-restart-coordinator.py --help
```

These commands do not install a service or restart Hermes. Tests use temporary Git repositories, a loopback HTTP server, and a fake service manager. Python 3.10+ and Git are required; the coordinator tests use only the standard library.

- **[Setup](docs/setup.md)** — compatibility checks, first activation, service configuration, and verification.
- **[Update and recovery runbook](docs/operations.md)** — prepare an update, interpret pending/blocked status, recover without a restart loop.
- **[Integration contract](docs/integration.md)** — what the WebUI must implement; patch baseline and limits.
- **[Agent handoff](AGENTS.md)** — boundaries for an agent setting this up.

## How it works

1. Detect a changed Agent/WebUI revision pair and allow it to settle.
2. Compile selected WebUI Python files and require healthy, well-formed telemetry.
3. Wait for zero active runs and the configured quiet window.
4. Create a process-specific drain marker. Compatible WebUI code refuses new work.
5. Recheck active runs, restart the existing launchd job, and require a changed server-start identity plus healthy deep health.
6. Record success, or block the failed revision pair for operator investigation.

An open tab is not a running turn. A closed stream is not proof that the worker stopped. That is why this uses worker-lifecycle tracking rather than browser activity.

## Supported boundary

- **macOS, per-user launchd, one WebUI process**, whose launchd PID is the Python WebUI process itself (an `exec` launcher is fine).
- **Standalone `nesquena/hermes-webui` architecture**, with the documented active-run and drain interfaces. Not a claim of compatibility with every version, fork, or the built-in Hermes dashboard.
- Linux users can run the isolated coordinator tests, but must implement and test a systemd adapter before deployment. No Linux service adapter is supplied.
- The repository contains all *kit* source and guidance. Hermes Agent, Hermes WebUI, their dependencies, and a configured service remain prerequisites. The included patch is not a vendored WebUI distribution or a universal patch for arbitrary releases.

## What it does not guarantee

No automatic Git updates, dependency transactions, code/database rollback, zero downtime, or protection for unrelated CLI/gateway/cron work. It restarts **WebUI only**, even when it detects Agent code changes. Updating dependencies or code in place can affect running processes *before* a restart; use a separately approved maintenance window for those changes.

The marker protocol is a pragmatic cross-process gate, not a formally atomic distributed handoff. See [the race and verification limits](docs/integration.md#limits). New requests during maintenance receive a retryable error rather than being durably queued.

MIT licensed. WebUI patch context retains its separate [copyright notice](patches/LICENSE). This is a community project, not an official Nous Research updater.
