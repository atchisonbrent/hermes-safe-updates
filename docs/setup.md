# Setup on macOS

Do not enable the coordinator until the running WebUI supports draining. Setup itself may require a manually approved restart to load that support.

## 1. Establish compatibility

Record the operating system, actual Hermes/WebUI checkout paths and revisions, service label, listener PID, health URL, and profile/state home. Do not copy another person's service labels or paths.

Read [integration.md](integration.md). If the required interfaces are absent, assess the bundled patch in a disposable worktree; do not blindly apply it to production. Run the target WebUI's tests in isolated state. For a newer or different architecture, stop and adapt the integration first.

Verify the live deep-health endpoint without dumping session metadata into a shared transcript. It must return HTTP 200, `status: "ok"`, integer nonnegative `active_runs`, `restart_drain_supported: true`, numeric `server_started_at`, and a valid idle duration (`idle_seconds_since_last_run`, or uptime before the first run).

Use `launchctl print gui/$(id -u)/YOUR_WEBUI_LABEL` and a listener/process inspection to verify the reported PID is **the WebUI Python PID**, not a parent wrapper. Both sides must use exactly the same drain directory; the coordinator requires explicit `--drain-dir` rather than guessing. The WebUI reads `HERMES_WEBUI_RESTART_DRAIN_DIR`; configure that variable in its existing service environment if overriding its default. Keep this directory private to your OS user.

## 2. Keep the coordinator independent

Keep this clone outside the Agent/WebUI checkouts and virtual environments that updates replace. Use an absolute Python 3.10+ interpreter outside those environments. The coordinator itself has no third-party Python dependencies.

Syntax preflight uses the Agent checkout's `venv/bin/python`, then `.venv/bin/python`, then the coordinator interpreter. Verify that selected interpreter matches WebUI's supported Python version; compilation does not establish runtime/dependency compatibility.

Review `templates/local.hermes-safe-updates.plist`. Copy it to a staging file, replace every `__PLACEHOLDER__` with the actual absolute path/value, and XML-escape values (`&` becomes `&amp;`). Do not load the template itself. In particular:

- `__PYTHON__`: stable interpreter path.
- `__KIT__`: absolute path to this clone.
- `__HOME__`: OS user home, not profile home.
- `__AGENT__`, `__WEBUI__`: actual Git checkouts.
- `__STATE__`: new private coordinator state directory, separate from existing coordinators.
- `__DRAIN__`: exact directory used by the running WebUI. The coordinator creates it if absent, so a typo is not rejected; verify both sides and the admission-rejection acceptance test rather than trusting directory existence.
- `__WEBUI_LABEL__`: verified existing service label.
- `__HEALTH_URL__`: direct loopback deep-health URL; do not embed credentials.

The template uses 120 seconds of revision settling and 30 seconds idle. Review these as policy, not universal tuning. Keep all timeouts positive. Do not enable two coordinators for one WebUI.

Create the state and drain directories with mode 0700, owned by the service user. Do not grant other users write access. Check that the rendered plist has no placeholders, validate it with `plutil -lint /absolute/path/to/rendered.plist`, and inspect `ProgramArguments` and log paths. A valid plist alone does not prove the paths exist or identify the correct service.

## 3. Initialize deliberately

**The first enabled invocation records disk revisions as its baseline without checking or restarting the server.** Before doing this, independently confirm the running WebUI has loaded those exact revisions. If unsure, arrange a manual idle maintenance restart and verify first. Initializing after changing disk code can otherwise hide a needed restart.

With shell variables set to the reviewed absolute values (no example is an instruction to use defaults), enable explicitly:

```sh
# STATE, DRAIN, PYTHON, KIT, AGENT, WEBUI, WEBUI_LABEL, HEALTH_URL
# must already be set to your verified values.
: "${STATE:?}" "${DRAIN:?}" "${PYTHON:?}" "${KIT:?}" "${AGENT:?}" \
  "${WEBUI:?}" "${WEBUI_LABEL:?}" "${HEALTH_URL:?}"
mkdir -p "$STATE" "$DRAIN"
chmod 700 "$STATE" "$DRAIN"
(umask 077; touch "$STATE/enabled")
chmod 600 "$STATE/enabled"
"$PYTHON" "$KIT/scripts/webui-restart-coordinator.py" \
  --agent-dir "$AGENT" --webui-dir "$WEBUI" \
  --state-dir "$STATE" --drain-dir "$DRAIN" \
  --launch-label "$WEBUI_LABEL" --health-url "$HEALTH_URL" \
  --idle-seconds 30
```

Confirm `state.json` contains the expected baseline pair, with mode 0600. Do not delete/reset existing coordinator state to make an unexplained failure disappear.

Only after explicit operator approval, install the reviewed rendered plist at `~/Library/LaunchAgents/local.hermes-safe-updates.plist`, then:

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.hermes-safe-updates.plist"
launchctl print "gui/$(id -u)/local.hermes-safe-updates"
```

The job runs at load and every ten seconds; it is not another WebUI supervisor. There is no `KeepAlive` and no separate WebUI worker.

## 4. Acceptance before unattended use

Use a disposable service and isolated state first; never run failure-injection tests against production.

- Unchanged revisions: no restart.
- Changed revision during a real test turn: remains pending; closing the browser must not make an active worker disappear from the count.
- Completed work and quiet window: maintenance admission closes and the service restarts.
- A request during drain: receives the documented retryable response and creates no new active worker.
- Malformed/unavailable health: no restart.
- Healthy endpoint with unchanged generation after attempted restart: blocked, not successful.
- Successful restart: new generation, deep health, then a real representative chat and reload/resume.

The bundled suite exercises the coordinator's real CLI with fake services. It does not prove your service registration, runtime compatibility, or chat recovery. Record these live acceptance results before claiming your installation verified.

For uninstall and failed updates, see [operations.md](operations.md).
