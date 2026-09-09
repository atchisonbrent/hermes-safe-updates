#!/usr/bin/env python3
"""Coordinate a drain-gated restart of launchd-managed Hermes WebUI."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STATE_FILE = "state.json"
EXPECTED_OPERATION_ERRORS = (
    RuntimeError,
    TypeError,
    ValueError,
    OSError,
    json.JSONDecodeError,
    subprocess.SubprocessError,
)


def parse_args() -> argparse.Namespace:
    home = Path.home()
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-dir", type=Path, default=home / ".hermes/hermes-agent")
    parser.add_argument("--webui-dir", type=Path, default=home / "hermes-webui")
    parser.add_argument("--state-dir", type=Path, default=home / ".hermes/webui/restart-coordinator")
    parser.add_argument("--drain-dir", type=Path)
    parser.add_argument("--enabled-file", type=Path)
    parser.add_argument("--launchctl", type=Path, default=Path("/bin/launchctl"))
    parser.add_argument("--launch-label", default="local.hermes-webui")
    parser.add_argument("--health-url", default="http://127.0.0.1:8787/health?deep=1")
    parser.add_argument("--settle-seconds", type=float, default=120.0)
    parser.add_argument("--idle-seconds", type=float, default=300.0)
    parser.add_argument("--drain-timeout", type=float, default=120.0)
    parser.add_argument("--post-health-timeout", type=float, default=60.0)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--command-timeout", type=float, default=15.0)
    parser.add_argument("--retry-blocked", action="store_true")
    return parser.parse_args()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def git_head(repo: Path, timeout: float) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    ).strip()


def revision_pair(args: argparse.Namespace) -> dict[str, str]:
    return {
        "agent_revision": git_head(args.agent_dir, args.command_timeout),
        "webui_revision": git_head(args.webui_dir, args.command_timeout),
    }


def read_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unreadable coordinator state: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("active"), dict):
        raise TypeError(f"invalid coordinator state: {path}")
    return payload


def fetch_health(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"health returned HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("health payload is not an object")
    return payload


def strict_health(payload: dict) -> tuple[int, float]:
    if payload.get("status") != "ok":
        raise RuntimeError("WebUI health is not ok")
    if payload.get("restart_drain_supported") is not True:
        raise RuntimeError("running WebUI does not advertise restart drain support")
    active = payload.get("active_runs")
    if isinstance(active, bool) or not isinstance(active, int) or active < 0:
        raise RuntimeError("health active_runs is missing or malformed")
    started = payload.get("server_started_at")
    if isinstance(started, bool) or not isinstance(started, (int, float)):
        raise TypeError("health server_started_at is missing or malformed")
    return active, float(started)


def idle_for(payload: dict) -> float:
    value = payload.get("idle_seconds_since_last_run")
    if value is None:
        value = payload.get("uptime_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return 0.0
    return float(value)


def candidate_preflight(args: argparse.Namespace) -> None:
    interpreters = (
        args.agent_dir / "venv/bin/python",
        args.agent_dir / ".venv/bin/python",
    )
    interpreter = next((path for path in interpreters if path.is_file()), Path(sys.executable))
    relative_files = (
        "server.py",
        "bootstrap.py",
        "api/config.py",
        "api/routes.py",
        "api/streaming.py",
        "api/gateway_chat.py",
    )
    files = [args.webui_dir / relative for relative in relative_files if (args.webui_dir / relative).is_file()]
    if not files:
        return
    completed = subprocess.run(
        [str(interpreter), "-m", "py_compile", *map(str, files)],
        text=True,
        capture_output=True,
        check=False,
        timeout=args.command_timeout,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "Python compile check failed")


def launchd_pid(args: argparse.Namespace) -> int:
    target = f"gui/{os.getuid()}/{args.launch_label}"
    completed = subprocess.run(
        [str(args.launchctl), "print", target],
        text=True,
        capture_output=True,
        check=False,
        timeout=args.command_timeout,
    )
    if completed.returncode:
        raise RuntimeError("WebUI launchd job is not available")
    match = re.search(r"(?m)^\s*pid = ([0-9]+)\s*$", completed.stdout)
    if not match or int(match.group(1)) <= 0:
        raise RuntimeError("WebUI launchd job has no running pid")
    return int(match.group(1))


def restart(args: argparse.Namespace, before_started: float) -> None:
    target = f"gui/{os.getuid()}/{args.launch_label}"
    completed = subprocess.run(
        [str(args.launchctl), "kickstart", "-k", target],
        text=True,
        capture_output=True,
        check=False,
        timeout=args.command_timeout,
    )
    if completed.returncode:
        raise RuntimeError(f"launchctl kickstart failed: {completed.stderr.strip()}")
    deadline = time.monotonic() + args.post_health_timeout
    last_error = "new WebUI generation did not become healthy"
    while time.monotonic() < deadline:
        try:
            payload = fetch_health(args.health_url, min(2.0, args.command_timeout))
            _active, started = strict_health(payload)
            if started != before_started:
                return
            last_error = "health still reports the old WebUI generation"
        except EXPECTED_OPERATION_ERRORS as exc:
            last_error = str(exc)
        time.sleep(args.poll_seconds)
    raise RuntimeError(last_error)


def main() -> int:
    args = parse_args()
    enabled_file = args.enabled_file or (args.state_dir / "enabled")
    if not enabled_file.is_file():
        return 0
    enabled_stat = enabled_file.stat()
    if enabled_stat.st_uid != os.getuid() or stat.S_IMODE(enabled_stat.st_mode) != 0o600:
        print("DISABLED: enable sentinel must be owned by this user with mode 0600", file=sys.stderr)
        return 0

    args.state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.state_dir / "coordinator.lock"
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        state_path = args.state_dir / STATE_FILE
        current = revision_pair(args)
        if not state_path.exists():
            atomic_json(state_path, {"active": current})
            return 0

        state = read_state(state_path)
        if state.get("active") == current:
            if "pending" in state:
                atomic_json(state_path, {"active": current})
            return 0

        blocked = state.get("blocked")
        if (
            isinstance(blocked, dict)
            and blocked.get("revision_pair") == current
            and not args.retry_blocked
        ):
            return 0

        pending = state.get("pending")
        if not isinstance(pending, dict) or pending.get("revision_pair") != current:
            atomic_json(
                state_path,
                {
                    "active": state["active"],
                    "pending": {"revision_pair": current, "since": time.time()},
                },
            )
            return 0

        if time.time() - float(pending.get("since", time.time())) < args.settle_seconds:
            return 0
        if (args.agent_dir / ".git/index.lock").exists() or (args.webui_dir / ".git/index.lock").exists():
            return 0

        try:
            candidate_preflight(args)
        except EXPECTED_OPERATION_ERRORS as exc:
            print(f"PENDING: candidate preflight failed: {exc}", file=sys.stderr)
            return 0

        try:
            health = fetch_health(args.health_url, min(2.0, args.command_timeout))
            active_runs, before_started = strict_health(health)
        except EXPECTED_OPERATION_ERRORS as exc:
            print(f"PENDING: {exc}", file=sys.stderr)
            return 0
        if active_runs != 0 or idle_for(health) < args.idle_seconds:
            return 0

        pid = launchd_pid(args)
        drain_dir = args.drain_dir or (args.state_dir / "restart-drain")
        marker = drain_dir / f"{pid}.json"
        atomic_json(
            marker,
            {
                "pid": pid,
                "revision_pair": current,
                "requested_at": time.time(),
            },
        )

        try:
            deadline = time.monotonic() + args.drain_timeout
            while True:
                drained = fetch_health(args.health_url, min(2.0, args.command_timeout))
                active_runs, observed_started = strict_health(drained)
                if observed_started != before_started:
                    raise RuntimeError("WebUI generation changed before coordinated restart")
                if active_runs == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("WebUI did not drain active runs before deadline")
                time.sleep(args.poll_seconds)
            restart(args, before_started)
        except EXPECTED_OPERATION_ERRORS as exc:
            marker.unlink(missing_ok=True)
            atomic_json(
                state_path,
                {
                    "active": state["active"],
                    "blocked": {
                        "revision_pair": current,
                        "at": time.time(),
                        "reason": str(exc),
                    },
                },
            )
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        marker.unlink(missing_ok=True)
        atomic_json(state_path, {"active": current})
        print(
            "OK: restarted Hermes WebUI for Agent/WebUI revisions "
            f"{current['agent_revision'][:12]}/{current['webui_revision'][:12]}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
