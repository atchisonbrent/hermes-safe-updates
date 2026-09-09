"""Real CLI tests with disposable HTTP/Unix-socket runtime fixtures."""
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/maintenance.py'


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hsu-', dir='/tmp')
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'gateway'
        self.home.mkdir(mode=0o700)
        self.drain = self.root / 'drain'
        self.drain.mkdir(mode=0o700)
        self.identify = {'protocol': 1, 'pid': 4242, 'start_time': 1.0,
                         'hermes_home': str(self.home), 'code_sha': 'a' * 40}
        self.status = {'protocol': 1, 'answering_pid': 4242,
                       'gateway_state': 'running', 'active_agents': 0}
        self.health = {'status': 'ok', 'active_runs': 0,
                       'restart_drain_supported': True, 'server_started_at': 2.0}
        owner = self

        class SocketHandler(socketserver.StreamRequestHandler):
            def handle(self):
                request = json.loads(self.rfile.readline())
                payload = owner.identify if request['verb'] == 'identify' else owner.status
                self.wfile.write(json.dumps({'ok': True, 'result': payload}).encode() + b'\n')

        class HTTPHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = json.dumps(owner.health).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *_):
                pass

        self.socket_server = socketserver.ThreadingUnixStreamServer(str(self.home / 'gateway.sock'), SocketHandler)
        self.http_server = ThreadingHTTPServer(('127.0.0.1', 0), HTTPHandler)
        self.threads = []
        for server in (self.socket_server, self.http_server):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.url = f'http://127.0.0.1:{self.http_server.server_port}/health?deep=1'

    def tearDown(self):
        for server in (self.socket_server, self.http_server):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()
        self.tmp.cleanup()

    def run_cli(self, action, *args):
        return subprocess.run([
            sys.executable, str(SCRIPT), action,
            '--gateway-home', str(self.home), '--webui-health-url', self.url,
            '--webui-drain-dir', str(self.drain), '--webui-pid', '5252',
            *args,
        ], text=True, capture_output=True, timeout=10,
            env={'HOME': str(self.root), 'PATH': os.environ['PATH']})

    def test_begin_and_cancel_own_both_markers(self):
        ticket = '11111111-1111-4111-8111-111111111111'
        result = self.run_cli('begin', '--ticket', ticket)
        self.assertEqual(0, result.returncode, result.stderr)
        gateway = self.home / '.drain_request.json'
        webui = self.drain / '5252.json'
        self.assertEqual('drain', json.loads(gateway.read_text())['action'])
        self.assertEqual(ticket, json.loads(webui.read_text())['maintenance_ticket'])
        self.assertEqual(0o600, gateway.stat().st_mode & 0o777)
        denied = self.run_cli('cancel', '--ticket', '22222222-2222-4222-8222-222222222222')
        self.assertNotEqual(0, denied.returncode)
        self.assertTrue(gateway.exists())
        cancelled = self.run_cli('cancel', '--ticket', ticket)
        self.assertEqual(0, cancelled.returncode, cancelled.stderr)
        self.assertFalse(gateway.exists())
        self.assertFalse(webui.exists())

    def test_wait_requires_both_idle_acknowledgement_and_same_generations(self):
        ticket = '11111111-1111-4111-8111-111111111111'
        before = self.root / 'before.json'
        before.write_text(self.run_cli('inspect').stdout)
        self.assertEqual(0, self.run_cli('begin', '--ticket', ticket).returncode)
        options = ('--ticket', ticket, '--before', str(before), '--timeout', '0.3',
                   '--quiet-seconds', '0.05', '--other-work-stopped')
        # A request on disk without gateway acknowledgement is NOT drained.
        self.assertNotEqual(0, self.run_cli('wait', *options).returncode)
        self.status['gateway_state'] = 'draining'
        self.status['active_agents'] = 1
        self.assertNotEqual(0, self.run_cli('wait', *options).returncode)
        self.status['active_agents'] = 0
        self.health['active_runs'] = 1
        self.assertNotEqual(0, self.run_cli('wait', *options).returncode)
        self.health['active_runs'] = 0
        ready = self.run_cli('wait', *options)
        self.assertEqual(0, ready.returncode, ready.stderr)
        self.assertEqual('drained_observed', json.loads(ready.stdout)['status'])
        self.health['server_started_at'] = 3.0
        self.assertNotEqual(0, self.run_cli('wait', *options).returncode)

    def test_verify_requires_both_new_generations_and_expected_agent_code(self):
        before = self.root / 'before.json'
        before.write_text(self.run_cli('inspect').stdout)
        options = ('--before', str(before), '--expect-agent-sha', 'b' * 40,
                   '--timeout', '0.3', '--quiet-seconds', '0.05')
        self.assertNotEqual(0, self.run_cli('verify', *options).returncode)
        self.identify.update(pid=4243, start_time=3.0, code_sha='b' * 40)
        self.status['answering_pid'] = 4243
        self.assertNotEqual(0, self.run_cli('verify', *options).returncode)
        self.health['server_started_at'] = 4.0
        result = self.run_cli('verify', *options)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('replacement_observed', json.loads(result.stdout)['status'])
        self.identify['code_sha'] = 'c' * 40
        self.assertNotEqual(0, self.run_cli('verify', *options).returncode)
        # Operator-controlled rollback is verified with the old SHA, never fabricated.
        rollback = ('--before', str(before), '--expect-agent-sha', 'a' * 40,
                    '--timeout', '0.3', '--quiet-seconds', '0.05')
        self.identify['code_sha'] = 'a' * 40
        self.assertEqual(0, self.run_cli('verify', *rollback).returncode)

    def test_verify_rejects_corrupt_baseline_identity(self):
        before = self.root / 'before.json'
        body = json.loads(self.run_cli('inspect').stdout)
        body['gateways'][0]['start_time'] = None
        body['webui']['server_started_at'] = None
        before.write_text(json.dumps(body))
        result = self.run_cli('verify', '--before', str(before), '--expect-agent-sha', 'a' * 40,
                              '--timeout', '0.3', '--quiet-seconds', '0.05')
        self.assertNotEqual(0, result.returncode)

    def test_unknown_health_and_socket_failure_never_create_markers(self):
        ticket = '11111111-1111-4111-8111-111111111111'
        for invalid in (None, False, '0', -1):
            with self.subTest(count=invalid):
                self.status['active_agents'] = invalid
                self.assertNotEqual(0, self.run_cli('begin', '--ticket', ticket).returncode)
                self.assertFalse((self.home / '.drain_request.json').exists())
        self.status['active_agents'] = 0
        for invalid in (None, False, '0', -1):
            self.health['active_runs'] = invalid
            self.assertNotEqual(0, self.run_cli('begin', '--ticket', ticket).returncode)
        self.health['active_runs'] = 0
        (self.home / 'gateway.sock').unlink()
        self.assertNotEqual(0, self.run_cli('begin', '--ticket', ticket).returncode)
        self.assertFalse((self.home / '.drain_request.json').exists())

    def test_existing_marker_never_overwritten_and_no_other_marker_created(self):
        marker = self.drain / '5252.json'
        marker.write_text('another operator')
        result = self.run_cli('begin', '--ticket', '11111111-1111-4111-8111-111111111111')
        self.assertNotEqual(0, result.returncode)
        self.assertEqual('another operator', marker.read_text())
        self.assertFalse((self.home / '.drain_request.json').exists())

    def test_untracked_work_requires_explicit_attestation(self):
        result = self.run_cli('wait', '--ticket', '11111111-1111-4111-8111-111111111111',
                              '--before', str(self.root / 'before.json'))
        self.assertEqual(2, result.returncode)
        self.assertIn('--other-work-stopped', result.stderr)

    def test_multiplex_gateway_refused_before_drain(self):
        self.identify['served_profiles'] = ['default', 'other']
        result = self.run_cli('begin', '--ticket', '11111111-1111-4111-8111-111111111111')
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.home / '.drain_request.json').exists())

    def test_all_selected_gateways_must_drain(self):
        other = self.root / 'other'
        other.mkdir(mode=0o700)
        identity = dict(self.identify, pid=4343, hermes_home=str(other))
        status = dict(self.status, answering_pid=4343, gateway_state='draining', active_agents=1)

        class OtherGateway(socketserver.StreamRequestHandler):
            def handle(self):
                request = json.loads(self.rfile.readline())
                data = identity if request['verb'] == 'identify' else status
                self.wfile.write(json.dumps({'ok': True, 'result': data}).encode() + b'\n')

        server = socketserver.ThreadingUnixStreamServer(str(other / 'gateway.sock'), OtherGateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            extra = ('--gateway-home', str(other))
            before = self.root / 'before.json'
            captured = self.run_cli('inspect', *extra)
            self.assertEqual(0, captured.returncode, captured.stderr)
            self.assertEqual(2, len(json.loads(captured.stdout)['gateways']))
            before.write_text(captured.stdout)
            ticket = '11111111-1111-4111-8111-111111111111'
            self.assertEqual(0, self.run_cli('begin', *extra, '--ticket', ticket).returncode)
            self.status['gateway_state'] = 'draining'
            wait = (*extra, '--ticket', ticket, '--before', str(before), '--other-work-stopped',
                    '--timeout', '0.4', '--quiet-seconds', '0.05')
            self.assertNotEqual(0, self.run_cli('wait', *wait).returncode)
            status['active_agents'] = 0
            self.assertEqual(0, self.run_cli('wait', *wait).returncode)
            self.assertEqual(0, self.run_cli('cancel', *extra, '--ticket', ticket).returncode)
            self.assertFalse((other / '.drain_request.json').exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_invalid_code_identity_refused_before_mutation(self):
        for value in (None, '', 'abc123', 'A' * 40, 123):
            self.identify['code_sha'] = value
            result = self.run_cli('begin', '--ticket', '11111111-1111-4111-8111-111111111111')
            self.assertNotEqual(0, result.returncode)
            self.assertFalse((self.home / '.drain_request.json').exists())

    def test_file_instead_of_drain_directory_refused_before_mutation(self):
        self.drain.rmdir()
        self.drain.write_text('not a directory')
        result = self.run_cli('begin', '--ticket', '11111111-1111-4111-8111-111111111111')
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.home / '.drain_request.json').exists())

    def test_cancel_reports_only_exact_selected_targets(self):
        ticket = '11111111-1111-4111-8111-111111111111'
        self.assertEqual(0, self.run_cli('begin', '--ticket', ticket).returncode)
        result = self.run_cli('cancel', '--ticket', ticket)
        body = json.loads(result.stdout)
        self.assertEqual('selected_markers_cleared', body['status'])
        self.assertEqual(sorted([str(self.home / '.drain_request.json'), str(self.drain / '5252.json')]), sorted(body['removed']))
        again = json.loads(self.run_cli('cancel', '--ticket', ticket).stdout)
        self.assertEqual([], again['removed'])
        self.assertEqual(sorted(body['removed']), sorted(again['already_absent']))

    def test_inspect_uses_both_live_runtimes_and_redacts_details(self):
        self.health['runs'] = [{'message': 'private conversation'}]
        self.status['platforms'] = {'private': 'metadata'}
        result = self.run_cli('inspect')
        self.assertEqual(0, result.returncode, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual('a' * 40, body['gateways'][0]['code_sha'])
        self.assertEqual(2.0, body['webui']['server_started_at'])
        self.assertNotIn('private conversation', result.stdout)
        self.assertNotIn('platforms', result.stdout)


if __name__ == '__main__':
    unittest.main()
