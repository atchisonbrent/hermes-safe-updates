# Combined Hermes Agent + WebUI maintenance

One maintenance window, separate runtime checks. The helper never runs Git, installs dependencies, stops services, or restores data. The local operator approves and performs those steps. Do not execute this from a Hermes/WebUI process you intend to stop.

## 1. Inventory and prepare

Read the installed versions' documentation and [Hermes updating guide](https://hermes-agent.nousresearch.com/docs/getting-started/updating). Inspect:

```sh
hermes update --help
hermes update --plan
hermes backup --help
```

`--plan` is the native fleet inventory, not a claim that everything it finds is covered by this helper. Identify every profile/gateway, standalone cron scheduler, CLI/TUI session, detached terminal/delegate/kanban task, Desktop/serve/dashboard backend, and external automation sharing the Agent checkout or state. Let their work finish and prevent new work, or explicitly stop them with approval. If ownership or activity cannot be established, **do not update**. Do not treat a quiet browser as idle.

Record exact Agent/WebUI checkout paths, branches, clean status, known-good SHAs, interpreter/dependency environments, backup destination, native service labels/plists, listener PIDs, and how to stop/start/recover each service. No competing update writers or coordinators. Disable the optional WebUI revision coordinator and wait for its current invocation to finish. Leave it disabled throughout this combined operation.

Choose a tested compatible Agent/WebUI revision pair. Stage and test it outside serving checkouts and real state. Native `hermes update --branch` follows a branch, **not an immutable SHA**: resolve it before update and reject unexpected movement afterward. If an exact pinned update is required, use the documented manual update path instead of pretending the branch flag pins it.

The native updater can snapshot/restart other profiles and rebuild Desktop. Freeze automatic backend respawns and account for its complete plan. This guide targets Git installs; image/package-managed installs need their own supported maintenance workflow.

Check [gateway compatibility](gateway-contract.md) and [WebUI compatibility](integration.md). Verify the supplied WebUI Python PID actually owns the health listener and that both sides use the same drain directory. The helper does not infer PID ownership from a URL. Every gateway must serve just its selected home; multiplex mode requires separate adaptation.

## 2. Set explicit local targets

Example shell setup (Bash or Zsh). Replace paths and values based on discovery, not on the author's deployment. Keep receipts private and outside all checkouts/environments being replaced.

```sh
# Fill these yourself before running:
# KIT, PYTHON, AGENT, WEBUI, GATEWAY_HOME, DRAIN_DIR,
# WEBUI_PID, HEALTH_URL, RECEIPTS
: "${KIT:?}" "${PYTHON:?}" "${AGENT:?}" "${WEBUI:?}" \
  "${GATEWAY_HOME:?}" "${DRAIN_DIR:?}" "${WEBUI_PID:?}" \
  "${HEALTH_URL:?}" "${RECEIPTS:?}"
umask 077
mkdir -p "$RECEIPTS"
chmod 700 "$RECEIPTS"
TICKET=$("$PYTHON" -c 'import uuid; print(uuid.uuid4())')
# Save TICKET and the old WebUI PID privately for cancellation/recovery.
COMMON=(--gateway-home "$GATEWAY_HOME"
        --webui-health-url "$HEALTH_URL"
        --webui-drain-dir "$DRAIN_DIR" --webui-pid "$WEBUI_PID")
# Add --gateway-home /absolute/other/profile for EVERY other affected gateway.
"$PYTHON" "$KIT/scripts/maintenance.py" inspect "${COMMON[@]}" > "$RECEIPTS/before.json"
```

Check that the command succeeded and the receipt has all expected gateways. Do not overwrite `before.json` later to make verification pass. The health URL must be literal loopback HTTP `/health?deep=1`, no embedded credentials, redirects, or proxies.

## 3. Close admission, request drain, wait

Arrange a maintenance window: pause external producers and block *new* traffic through your existing ingress controls without killing established turns. On an exclusively local installation, get every user/automation to stop submitting. If you cannot prevent new work, stop here. Do not invent a second proxy as part of setup.

With operator approval:

```sh
"$PYTHON" "$KIT/scripts/maintenance.py" begin "${COMMON[@]}" --ticket "$TICKET"
"$PYTHON" "$KIT/scripts/maintenance.py" wait "${COMMON[@]}" \
  --ticket "$TICKET" --before "$RECEIPTS/before.json" \
  --other-work-stopped --quiet-seconds 3
```

`--other-work-stopped` is a **human/agent attestation after inventory**, not automated detection. It includes detached background work, not just CLI sessions. Never add it automatically to silence a refusal.

`begin` writes native-compatible gateway markers and the WebUI PID marker. It refuses existing markers, never overwrites another operation, and returns **drain_requested**, not idle. Gateway acknowledgement is asynchronous. `wait` requires every gateway to answer `draining` with `active_agents == 0`, WebUI `active_runs == 0`, owned markers, unchanged generations, and a quiet observation window. A transport failure resets the quiet window and keeps waiting; malformed health, ownership or generation changes abort safely. Nothing is restarted by the helper.

An error leaves markers in place for explicit cancellation. Waiting for idle has no elapsed-time expiry; `--timeout` applies only to replacement verification. Partial marker creation can occur if a write fails; inspect exact paths and cancel this ticket. Never run another writer concurrently. Parent directories must already exist, be owned by the OS user, and not be group/world writable.

**Do not sleep indefinitely after the gate.** The waiter renews its owned native gateway leases while busy, for as long as needed. Once it returns, renewal stops; complete the transition to stopped services promptly. A marker is not a durable maintenance lock.

## 4. Stop, back up, update the pair

Keep ingress closed. After the gate, stop WebUI first, then affected gateway services/backends using their existing lifecycle owner. Verify **each actual listener and process is gone**; a successful stop command alone is insufficient. Do not use `kickstart -k` against busy work. On launchd, save the original plist path before `bootout`, and wait until `launchctl print` reports that exact label absent before later bootstrapping it. Unknown/permission errors do not mean absent.

This is the boundary that protects live Python code/dependency mutation. Do not pull/install into a checkout while its processes are still using it. Stop user-launched CLI/background processes as inventoried, too.

Take a full supported backup of every affected state home while writers are stopped; keep the archive and recovery instructions outside update targets. `hermes backup` is the native migration/backup entrypoint; use its installed `--help` for destination/options. Native `hermes update --backup` is additional protection, not evidence that a large WebUI database or external state directory was included. Cover external WebUI state explicitly and verify backup contents/integrity without displaying secrets.

Choose **one** supported Agent update route and record its output privately:

```sh
# Operator-approved branch update; not an immutable pin:
hermes update --branch "$AGENT_BRANCH" --backup --keep-stash
```

Run it against the intended installation with the correct profile environment, from the external terminal. Confirm the branch/update flags exist in that version. Do not automatically accept stash restore, change fork ownership, or treat exit zero as pair compatibility. Check the native update receipt and exact final Agent SHA against the pre-approved target. If either differs or the updater reports partial completion, keep maintenance closed and investigate.

For the WebUI's supported source-install path, after fetching/reviewing its exact target before maintenance:

```sh
# WEBUI_TARGET must be a previously verified full commit SHA in this checkout.
git -C "$WEBUI" merge --ff-only "$WEBUI_TARGET"
```

Use its version-matched documented dependency installation command with the intended environment; do not assume Agent dependency installation covers WebUI. If fast-forward is impossible, stop and review the merge outside production. Never reset away dirty work or add automatic merge conflict resolution. The helper performs neither command.

## 5. Release the drains and start both sides

Keep ingress/producers closed. While services are stopped, cancel the old ticket using the **original COMMON array and old WebUI PID**:

```sh
"$PYTHON" "$KIT/scripts/maintenance.py" cancel "${COMMON[@]}" --ticket "$TICKET"
```

Cancellation works without a running gateway or WebUI. It validates ownership before deleting markers; missing markers are allowed, foreign/malformed markers stop cleanup. Its `selected_markers_cleared` result lists exact removed and already-absent paths: compare their union with the original target inventory. It cannot discover omitted homes, a changed drain directory, or the old PID when given the new PID. This is not a global ticket-completion claim. Verify the exact original paths are clear. Do not remove unrelated markers or delete an entire drain directory. An interrupted half-written marker needs manual ownership investigation.

Start the native gateway services first, then WebUI. Stopped gateways may not be restarted by the native updater, so explicitly verify every expected job. Re-identify the new WebUI listener PID and update that one argument for verification (do not reuse the old PID in a future begin).

```sh
VERIFY_COMMON=(--gateway-home "$GATEWAY_HOME"
               --webui-health-url "$HEALTH_URL"
               --webui-drain-dir "$DRAIN_DIR" --webui-pid "$NEW_WEBUI_PID")
# Repeat all the other --gateway-home arguments here, too.
"$PYTHON" "$KIT/scripts/maintenance.py" verify "${VERIFY_COMMON[@]}" \
  --before "$RECEIPTS/before.json" --expect-agent-sha "$EXPECTED_AGENT_SHA" \
  --timeout 60 --quiet-seconds 3 > "$RECEIPTS/after.json"
```

This requires a **new start identity for every selected gateway and WebUI**, gateways back in `running`, exact full Agent code SHA from each live socket, and healthy WebUI responses over a stable observation window. This full-pair workflow intentionally restarts both; use the optional WebUI-only path for narrower approved changes.

`replacement_observed` is not “done.” Also verify:

- Agent and WebUI clean disk SHAs match the tested pair; native fleet plan/receipt shows no missed runtime.
- WebUI is launched from the intended checkout and interpreter; inspect loaded-revision telemetry if that version provides it. Otherwise use launch/process provenance plus the unchanged checkout through startup, and disclose that weaker evidence. The helper has no WebUI loaded-SHA field.
- Platform readiness/deep health, real tool-using conversation through WebUI, reload/reconnect, and one representative gateway surface work as intended. Check no orphan workers or failed service loops.
- Only after those pass, reopen ingress and producers. Restore the optional revision coordinator only as a separately reviewed action: its old stored pair can schedule an unnecessary extra restart after manual maintenance. Leaving it disabled is the default for this workflow.

## Already seeing “Hermes Agent was updated while Hermes WebUI was running”?

This guard prevents new Agent-backed work from mixing cached old Python modules with changed source. A healthy `/health` response does not establish that new chat can start. Do not bypass the guard, hot-reload Python modules, or overwrite revision tracking to make it disappear.

From an external terminal, compare the Agent change history with the WebUI process generation and inspect the existing coordinator's pending/blocked state and live `active_runs`. A pending restart with active work is not a failed restart. Let legitimate work finish; the maintenance conversation itself can be the run keeping WebUI busy. Do not mistake closing a browser tab for finishing that run, or forcibly clear the run registry.

If the optional coordinator is already enabled and owns the queued restart, avoid starting a competing restart mechanism. Once the last run ends and its configured idle/settle gates pass, verify the replacement generation and a real new chat. If it remains blocked, inspect the recorded failure from outside WebUI and use the same lifecycle owner's approved recovery path after quiescence. A queue entry is not proof of recovery. For future updates, follow the combined stop-before-mutation sequence above rather than relying on a revision watcher to repair an already changed live checkout.

## 6. Operator-controlled recovery

Keep ingress closed if either side fails. Preserve updater receipt/logs and before/after identities. Stop any partially started runtimes through their native owner before restoring code or dependencies.

Restore **both recorded known-good revisions and their compatible dependency environments**, following installed-version rollback guidance. Code rollback is not dependency rollback, and neither implies database rollback. If an incompatible schema/config migration occurred, select the matching verified state backup with explicit approval; automatic database restoration can discard new work. Do not restore secrets into this public repository.

Before **every recovery start**, inspect and cancel this operation's drain markers while services are stopped, using the original homes, drain directory, WebUI PID and ticket from section 5. Update failure may occur before normal cancellation. Confirm every original marker path is clear; foreign or malformed markers are a manual blocker, not permission to erase them.

Start both sides, then run `verify` against the original before receipt with the **known-good Agent SHA**, followed by the same WebUI provenance and real-conversation checks. The fixtures test that wrong/mixed code fails verification and a manually restored expected SHA can pass; they do not execute a real Git/venv/database rollback.

If recovery cannot be verified, report **emergency / still in maintenance**, retain external access, and escalate. Do not loop destructive retries, invent a healthy receipt, or reopen ingress because a command exited zero.
