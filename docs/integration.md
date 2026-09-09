# WebUI integration contract

## Included patch

`patches/webui-restart-drain.patch` is the five-file source diff from the MIT-licensed WebUI change `0f972f79442352eb7cb3a088915dbec58821f880`, with its tests. It applies to exact parent **`e585030012d1e795be4d1cc4f2bf86f2222edc1e`** in [the WebUI fork](https://github.com/atchisonbrent/hermes-webui). Patch bytes are included; obtaining the patch requires no remote/private repository. The baseline is for reproducibility, not a recommendation to downgrade to an old release.

It depends on an existing active-run registry, worker lifecycle cleanup, stream ownership helpers, and health telemetry. Those are part of the target WebUI, not supplied by this narrow patch. Do not assume an arbitrary upstream revision has them. Do not fake health fields to satisfy the coordinator.

In a clean disposable WebUI worktree at a compatible revision:

```sh
git apply --check /absolute/path/to/hermes-safe-updates/patches/webui-restart-drain.patch
# Review the diff and get approval before applying:
git apply /absolute/path/to/hermes-safe-updates/patches/webui-restart-drain.patch
```

If the patch is already present, do not apply it again. If the check fails, stop: inspect the APIs and port the small integration rather than using fuzzy/forced application. Use the WebUI repository's documented isolated test runner for `tests/test_restart_drain_admission.py`, its active-run lifecycle tests, and the relevant chat/regression suite. Loading WebUI tests may import application state; never point them at a real user's profile.

The patch includes tests for process-scoped markers, malformed markers refusing admission, route-level rejection before session mutation, local/gateway worker rejection, and the health capability field.

## Required behavior

- `/health?deep=1` returns HTTP 200 only when the required deep checks pass, and exposes `status`, `active_runs`, `server_started_at`, `restart_drain_supported`, and idle/uptime telemetry.
- Active runs count actual workers through outer cleanup, including tool execution and work whose browser disconnected. It is not the count of SSE connections.
- The WebUI looks for `<drain-dir>/<its-Python-PID>.json`. Existence is the gate; malformed contents still block new work.
- Chat admission checks the marker before session mutation. Worker registration checks again under its active-run lock to reject a worker that reached admission earlier.
- Rejected route requests return HTTP 503 with `status: restart_draining` and `retryable: true`. Worker races emit an `apperror` event of type `restart_draining` and release ownership. Clients must permit retry; the kit does not queue requests or automatically replay them.
- The coordinator and WebUI share the same private local filesystem directory and OS user. The launchd PID must be the WebUI PID. A supervising shell without `exec`, containers, multiworker servers, and remote filesystems are outside this adapter's contract.

## Limits

The marker is created by an external process; it does **not** take the WebUI's in-process registry lock. Thus this is not a proof of an atomic zero-loss handoff: a worker could pass the marker check immediately before marker creation, pause before registry insertion, and be missed by the coordinator's health sample. Closing that last race requires an application-owned drain/ack operation synchronized with registration, which this kit does not implement. For a strict no-interruption requirement, close ingress and arrange an explicit maintenance window rather than treating this protocol as sufficient.

Other important boundaries:

- A new start timestamp plus deep health proves a responding replacement, not exact imported revision identity or a successful real chat. Verify those separately.
- Revision settling is not a deployment lock. The coordinator checks Git index locks but does not hold a transaction lock across both repositories. Do not run concurrent update writers. Disable coordination around preparation; re-enable only after both checkouts/dependencies are ready.
- Dirty/uncommitted changes are not revision changes. Only Git HEAD pairs trigger this coordinator.
- Preflight is compilation of selected files, not a full dependency/import/candidate startup test.
- The initial state records disk revisions without proving runtime parity.
- A coordinator crash may leave a PID marker. PID reuse can make an old marker relevant; investigate and remove an exact stale marker only after proving no active coordinator owns it.
- No automatic code or state restoration. Separate gateway/CLI/cron work is not covered by WebUI health or restarted by this script.

These limitations are intentional publication disclosures, not claims that the recipient's deployment has passed acceptance testing.
