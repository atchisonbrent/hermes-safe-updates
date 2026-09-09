from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "webui-restart-coordinator.py"


class WebUIRestartCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="webui-restart-coordinator-")
        self.root = Path(self.tmp.name)
        self.agent = self.root / "agent"
        self.webui = self.root / "webui"
        self.state = self.root / "state"
        self.agent.mkdir()
        self.webui.mkdir()
        (self.webui / "server.py").write_text("# Disposable WebUI syntax fixture\n")
        self.state.mkdir()
        self.launch_calls = self.root / "launch-calls"
        self.kickstarted = self.root / "kickstarted"
        self.launchctl = self.root / "launchctl"
        self.launchctl.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {self.launch_calls}\n"
            "if [ \"$1\" = print ]; then printf '\\tpid = 4242\\n'; fi\n"
            f"if [ \"$1\" = kickstart ]; then : > {self.kickstarted}; fi\n",
            encoding="utf-8",
        )
        self.launchctl.chmod(0o700)
        self.health = {
            "status": "ok",
            "active_runs": 0,
            "idle_seconds_since_last_run": 600,
            "server_started_at": 10.0,
            "restart_drain_supported": True,
        }
        self.advance_generation_on_kickstart = True
        self.drain_active_sequence: list[int] = []
        owner = self

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                payload = dict(owner.health)
                marker = owner.state / "restart-drain" / "4242.json"
                if marker.exists() and owner.drain_active_sequence:
                    payload["active_runs"] = owner.drain_active_sequence.pop(0)
                if owner.kickstarted.exists() and owner.advance_generation_on_kickstart:
                    payload["server_started_at"] = 20.0
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *_args) -> None:
                return

        self.health_server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        self.health_thread = threading.Thread(target=self.health_server.serve_forever, daemon=True)
        self.health_thread.start()
        self.health_url = f"http://127.0.0.1:{self.health_server.server_port}/health"

    def init_repo(self, path: Path, content: str) -> str:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
        (path / "revision.txt").write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "revision.txt"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-qm", content], cwd=path, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()

    def enable(self) -> Path:
        enabled = self.state / "enabled"
        enabled.write_text("enabled\n", encoding="utf-8")
        enabled.chmod(0o600)
        return enabled

    def advance_repo(self, path: Path, content: str) -> str:
        (path / "revision.txt").write_text(content, encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", content], cwd=path, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()

    def prepare_pending(self) -> tuple[str, str]:
        self.init_repo(self.agent, "agent-one")
        webui_head = self.init_repo(self.webui, "webui-one")
        self.enable()
        self.assertEqual(0, self.run_coordinator().returncode)
        agent_head = self.advance_repo(self.agent, "agent-two")
        self.assertEqual(0, self.run_coordinator("--settle-seconds", "0").returncode)
        return agent_head, webui_head

    def tearDown(self) -> None:
        self.health_server.shutdown()
        self.health_server.server_close()
        self.health_thread.join(timeout=2)
        self.tmp.cleanup()

    def run_coordinator(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--agent-dir",
                str(self.agent),
                "--webui-dir",
                str(self.webui),
                "--state-dir",
                str(self.state),
                "--drain-dir",
                str(self.state / "restart-drain"),
                "--launchctl",
                str(self.launchctl),
                "--health-url",
                self.health_url,
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            umask=0o022,
        )

    def test_missing_explicit_drain_dir_is_rejected(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--state-dir", str(self.state)],
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--drain-dir", result.stderr)
        self.assertFalse(self.launch_calls.exists())

    def test_created_state_directory_is_private_under_permissive_umask(self) -> None:
        self.init_repo(self.agent, "agent-one")
        self.init_repo(self.webui, "webui-one")
        enabled = self.enable()
        private_state = self.root / "new-parent" / "state"
        result = self.run_coordinator("--state-dir", str(private_state),
                                      "--enabled-file", str(enabled))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(0o700, private_state.stat().st_mode & 0o777)
        self.assertEqual(0o700, private_state.parent.stat().st_mode & 0o777)

    def test_missing_repository_reports_error_without_traceback(self) -> None:
        self.enable()
        result = self.run_coordinator()
        self.assertEqual(1, result.returncode)
        self.assertIn("ERROR:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.launch_calls.exists())

    def test_disabled_without_enable_sentinel_is_side_effect_free(self) -> None:
        result = self.run_coordinator()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.launch_calls.exists())
        self.assertEqual([], list(self.state.iterdir()))

    def test_first_enabled_run_records_revision_pair_without_restart(self) -> None:
        agent_head = self.init_repo(self.agent, "agent-one")
        webui_head = self.init_repo(self.webui, "webui-one")
        self.enable()

        result = self.run_coordinator()

        self.assertEqual(0, result.returncode, result.stderr)
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(agent_head, state["active"]["agent_revision"])
        self.assertEqual(webui_head, state["active"]["webui_revision"])
        self.assertFalse(self.launch_calls.exists())
        self.assertEqual(0o600, os.stat(self.state / "state.json").st_mode & 0o777)

    def test_changed_pair_restarts_only_after_idle_and_proves_new_generation(self) -> None:
        agent_head, webui_head = self.prepare_pending()
        self.assertFalse(self.launch_calls.exists())

        self.health["active_runs"] = 1
        blocked = self.run_coordinator("--settle-seconds", "0", "--idle-seconds", "0")
        self.assertEqual(0, blocked.returncode, blocked.stderr)
        self.assertFalse(self.launch_calls.exists())

        self.health["active_runs"] = 0
        restarted = self.run_coordinator(
            "--settle-seconds",
            "0",
            "--idle-seconds",
            "0",
            "--post-health-timeout",
            "3",
        )
        self.assertEqual(0, restarted.returncode, restarted.stderr)
        calls = self.launch_calls.read_text(encoding="utf-8")
        self.assertIn("print gui/", calls)
        self.assertIn("kickstart -k gui/", calls)
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(agent_head, state["active"]["agent_revision"])
        self.assertEqual(webui_head, state["active"]["webui_revision"])
        self.assertFalse((self.state / "restart-drain" / "4242.json").exists())

    def test_runtime_without_drain_capability_stays_pending(self) -> None:
        self.prepare_pending()
        self.health["restart_drain_supported"] = False

        result = self.run_coordinator("--settle-seconds", "0", "--idle-seconds", "0")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("does not advertise restart drain support", result.stderr)
        self.assertFalse(self.launch_calls.exists())
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertIn("pending", state)

    def test_malformed_active_run_count_fails_closed(self) -> None:
        self.prepare_pending()
        self.health["active_runs"] = "0"

        result = self.run_coordinator("--settle-seconds", "0", "--idle-seconds", "0")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("active_runs is missing or malformed", result.stderr)
        self.assertFalse(self.launch_calls.exists())

    def test_run_admitted_before_marker_is_drained_before_restart(self) -> None:
        self.prepare_pending()
        self.drain_active_sequence = [1, 1, 0]

        result = self.run_coordinator(
            "--settle-seconds",
            "0",
            "--idle-seconds",
            "0",
            "--drain-timeout",
            "3",
            "--post-health-timeout",
            "3",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.drain_active_sequence)
        self.assertTrue(self.kickstarted.exists())

    def test_failed_new_generation_blocks_revision_without_retry_loop(self) -> None:
        self.prepare_pending()
        self.advance_generation_on_kickstart = False

        failed = self.run_coordinator(
            "--settle-seconds",
            "0",
            "--idle-seconds",
            "0",
            "--post-health-timeout",
            "0.2",
            "--poll-seconds",
            "0.02",
        )
        self.assertEqual(1, failed.returncode)
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertIn("blocked", state)
        self.launch_calls.unlink()
        self.kickstarted.unlink()

        again = self.run_coordinator("--settle-seconds", "0", "--idle-seconds", "0")
        third = self.run_coordinator("--settle-seconds", "0", "--idle-seconds", "0")

        self.assertEqual(0, again.returncode, again.stderr)
        self.assertEqual(0, third.returncode, third.stderr)
        self.assertFalse(self.launch_calls.exists())

    def test_enable_sentinel_with_group_permissions_does_not_enable(self) -> None:
        enabled = self.enable()
        enabled.chmod(0o640)

        result = self.run_coordinator()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.state / "state.json").exists())
        self.assertFalse(self.launch_calls.exists())

    def test_missing_candidate_source_never_enters_drain(self) -> None:
        self.prepare_pending()
        (self.webui / "server.py").unlink()
        result = self.run_coordinator("--settle-seconds", "0", "--idle-seconds", "0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no expected WebUI source files", result.stderr)
        self.assertFalse(self.launch_calls.exists())

    def test_invalid_candidate_source_never_enters_drain(self) -> None:
        self.prepare_pending()
        (self.webui / "server.py").write_text("def broken(:\n", encoding="utf-8")

        result = self.run_coordinator("--settle-seconds", "0", "--idle-seconds", "0")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("candidate preflight failed", result.stderr)
        self.assertFalse(self.launch_calls.exists())
        self.assertFalse((self.state / "restart-drain" / "4242.json").exists())


if __name__ == "__main__":
    unittest.main()
