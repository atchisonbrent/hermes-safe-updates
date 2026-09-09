# Update and recovery runbook

## Prepare, then activate

1. Identify exact Agent/WebUI revisions and their compatibility. Inspect official, version-matched [Hermes documentation](https://hermes-agent.nousresearch.com/docs/) and the installed updater's help before choosing update commands. A branch name is not a tested release identity.
2. Check clean checkout ownership and record the known-good revisions. No concurrent writers. Disable the coordinator before preparing changes: remove its enable sentinel, then ensure any already-running invocation has finished. Removing the sentinel does not cancel an invocation already past that check.
3. Take the application's supported backup, including configuration and state. Use SQLite backup APIs or a supported backup command for live databases, not a blind copy. Keep credentials out of receipts and this repository.
4. Stage and test candidate code/dependencies outside the serving tree and live state. Pure static assets may not need a restart; verify actual served bytes and cache/API compatibility first. This coordinator does not classify asset-only changes.
5. For mutation of live code/dependencies or any updater that restarts services itself, arrange an explicit maintenance window and drain all affected services **before mutation**. This coordinator's later idle check does not make live dependency replacement safe.
6. Perform the approved update from an external terminal/operator that will survive the restart. A background child of the WebUI is not automatically independent of its service lifecycle. Keep durable logs outside replaced trees and isolate stdio.
7. When disk is ready, re-enable the existing coordinator without resetting `state.json`. It detects the changed pair, waits for settling and idle, then attempts the WebUI restart. If your updater already restarted it, verify independently; the coordinator can otherwise cause an extra restart because it compares stored revisions rather than loaded revision metadata.
8. Verify new process generation, deep health, exact intended revisions, and a representative real chat/reconnect. Report **pending** until activation is observed; do not equate published source with a replaced runtime.

Updating a separately running gateway requires its own work/health checks and restart plan. There is no full coupled updater in this kit.

## Read status

Inspect the private `state.json` and configured stdout/stderr logs locally:

- `active`: recorded revision pair; on first initialization this is an assumption you must establish independently.
- `pending`: changed pair waiting for settling, preflight, health, or idle.
- `blocked`: restart/drain attempt failed; `reason` explains why. Repeated polls will not retry that same pair.

Exit zero can mean disabled, busy lock, pending, unchanged, or successful. It is **not** a completion receipt by itself. Only an `OK` message plus independent live verification supports completion.

## Recover

Disable the enable sentinel and wait for the current coordinator invocation to exit before manual intervention. Preserve failure evidence. Check the native service logs and identify whether failure was preflight, drain, restart, or health.

If a new code revision cannot start, restore the recorded known-good code and compatible dependencies through your normal supported update/recovery process, then restart through the existing service manager and prove health. Do not restore a database just because code startup failed; schema compatibility and state restoration are separate decisions.

Investigate blocked state before running a one-shot invocation with `--retry-blocked` and **the exact same explicit arguments** as the installed job. Do not add that flag to the recurring plist: it removes the no-retry-loop protection. A changed revision pair can be considered again normally.

For a leftover drain marker, verify the exact PID, server generation, and absence of a running coordinator before removing that one marker. Never delete all markers while another update might own them.

## Disable / uninstall

Remove your coordinator's enable sentinel, wait for any active invocation to finish, then unload only its job:

```sh
launchctl bootout "gui/$(id -u)/local.hermes-safe-updates"
```

Verify the coordinator label is absent. Keep the existing WebUI service running. Remove the installed coordinator plist and clone only after preserving desired logs/state and confirming they are not shared with another service. The WebUI patch is inert without its PID marker and need not be urgently reversed. Reverse it only in a reviewed compatible checkout with no unrelated edits.

## Tests and evidence

`python3 -m unittest discover -s tests -v` exercises the real coordinator CLI against temporary repositories, loopback HTTP, and a fake launchctl. It covers disabled initialization, revision detection, busy work, drain capability, malformed count, drain polling, generation failure/blocking, sentinel permissions, and syntax-preflight failure. It does not register a real job or exercise a real model turn. See setup acceptance checks for the remaining host-specific proof.
