# Hermes safe updates

**Maintain Hermes Agent and standalone Hermes WebUI together, without cutting off running work.**

This is a native-first maintenance kit: a combined operator/agent runbook, drain-and-verification helpers, and an optional WebUI restart coordinator. It uses Hermes's existing gateway control/drain mechanisms; it does not replace the native updater or build another service manager.

## Start here

```sh
git clone https://github.com/atchisonbrent/hermes-safe-updates.git
cd hermes-safe-updates
python3 -m unittest discover -s tests -v
python3 scripts/maintenance.py --help
```

Those commands do not drain, update, or restart anything. Python 3.10+ and Git are required; kit tests use only the standard library.

**[Combined Hermes + WebUI workflow →](docs/combined-maintenance.md)**

1. Inventory all affected profiles/processes and record a compatible revision pair.
2. Close admission to new work; request gateway and WebUI drains.
3. Wait for gateway acknowledgement and zero work on both sides.
4. Stop affected services, back up state, and use the native/manual update paths.
5. Start both sides, verify new generations and gateway code, then check WebUI revision and a real conversation.
6. If either side fails, keep maintenance closed and perform the documented operator-controlled recovery.

## What is included

| Component | Purpose |
|---|---|
| [`maintenance.py`](scripts/maintenance.py) | Inspect live gateways/WebUI; request/cancel owned drains; wait for both; verify replacements |
| [Combined runbook](docs/combined-maintenance.md) | Native update commands, inventory, backups, service ordering, failure recovery, acceptance gates |
| [Gateway contract](docs/gateway-contract.md) | Required native interfaces, compatibility and counting limits |
| [WebUI integration patch](patches/webui-restart-drain.patch) | Narrow admission-control integration and regression tests |
| [Optional restart coordinator](docs/setup.md) | Existing macOS WebUI-only revision watcher; disable during combined maintenance |
| [Agent handoff](AGENTS.md) | Scope, approval boundaries, and verification instructions for another agent |

## Scope and limits

- Reference deployment: **macOS, local user-owned runtimes, standalone `nesquena/hermes-webui`, and compatible Hermes gateway v1 control sockets**. Windows/named pipes, containers, remote runtimes, and multiplexed gateways are not covered by the combined helper. Linux service adaptation is not supplied.
- Repeat `--gateway-home` for each separately running gateway using the updated Agent checkout. Missing native interfaces are a stop condition, not permission to substitute stale state files or fake health fields.
- The helper does not discover every CLI, desktop backend, detached task, or scheduler. The operator must inventory and stop/protect untracked work before attesting it is safe.
- **No automatic updates or rollback.** The operator chooses and runs updates, dependency installation, service lifecycle commands, and recovery. Native updater behavior can affect every profile; review its plan first.
- **Not zero downtime or a global admission lock.** The WebUI marker race remains documented. Keep ingress closed and stop services before changing code/dependencies.
- A healthy replacement is not a successful conversation. The helper verifies gateway code identity and both process generations, but the WebUI's loaded revision and real chat/reconnect require separate checks.

The optional old coordinator still restarts **WebUI only**. It is not the combined workflow and must not compete with it.

MIT licensed. The WebUI patch preserves its [upstream copyright/license](patches/LICENSE). No private configuration, credentials, paid model, or private repository is needed. This is a community kit, not an official Nous Research updater.
