#!/usr/bin/env python3
"""Explicit maintenance checks for a local Hermes gateway + standalone WebUI.

No updates, service-manager calls, dependency installs, or automatic recovery.
Gateway observation uses the native v1 control socket; no state-file fallback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
import socket
import stat
import sys
import time
import urllib.parse
import urllib.request
import uuid

LIMIT = 512 * 1024


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('missing or malformed active-work count')
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('missing or malformed process generation')
    return value


def query(home, verb):
    path = home / 'gateway.sock'
    if not path.exists():
        path = Path((home / 'gateway.sock.path').read_text().strip())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(str(path))
        sock.sendall(json.dumps({'verb': verb, 'id': 1, 'protocol': 1}).encode() + b'\n')
        with sock.makefile('rb') as stream:
            raw = stream.readline(LIMIT + 1)
        if len(raw) > LIMIT or not raw.endswith(b'\n'):
            raise ValueError('invalid control response length/framing')
    envelope = json.loads(raw)
    if not isinstance(envelope, dict) or envelope.get('ok') is not True:
        raise ValueError('gateway control request refused')
    result = envelope.get('result')
    if not isinstance(result, dict) or type(result.get('protocol')) is not int or result['protocol'] != 1:
        raise ValueError('unsupported gateway control protocol')
    return result


def snapshot(args):
    gateways = []
    for home in args.gateway_home:
        identity = query(home, 'identify')
        status = query(home, 'status')
        if identity.get('served_profiles'):
            raise ValueError('multiplexed gateway requires a separate maintenance design')
        pid = count(identity.get('pid'))
        if not pid or status.get('answering_pid') != pid:
            raise ValueError('gateway changed between identity and status probes')
        if identity.get('hermes_home') != str(home):
            raise ValueError('gateway answered for another home')
        if not isinstance(identity.get('code_sha'), str) or not re.fullmatch('[0-9a-f]{40}', identity['code_sha']):
            raise ValueError('gateway lacks exact code identity')
        state = status.get('gateway_state')
        if state not in ('running', 'draining'):
            raise ValueError('gateway is neither running nor externally draining')
        gateways.append({'home': str(home), 'pid': pid,
                         'start_time': number(identity.get('start_time')),
                         'code_sha': identity.get('code_sha'),
                         'state': state, 'active_agents': count(status.get('active_agents'))})
    # No proxies, credentials, or redirects: this is a local runtime probe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(args.webui_health_url, timeout=2) as response:
        data = response.read(LIMIT + 1)
        if response.status != 200 or len(data) > LIMIT:
            raise ValueError('WebUI health failed')
    health = json.loads(data)
    if not isinstance(health, dict) or health.get('status') != 'ok' or health.get('restart_drain_supported') is not True:
        raise ValueError('WebUI is unhealthy or lacks restart drain support')
    return {'gateways': gateways, 'webui': {
        'health_url': args.webui_health_url, 'pid': args.webui_pid,
        'server_started_at': number(health.get('server_started_at')),
        'active_runs': count(health.get('active_runs'))}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def markers(args):
    return [home / '.drain_request.json' for home in args.gateway_home] + [args.webui_drain_dir / f'{args.webui_pid}.json']


def owned_marker(path, ticket):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('marker is not a regular file owned by this user')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('maintenance_ticket') != ticket:
        raise ValueError('marker belongs to another maintenance operation')
    return data


def change_drain(args):
    paths = markers(args)
    for path in paths:
        info = path.parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('marker parent must be owned by user and not group/world writable')
    if args.action == 'cancel':
        removed, absent = [], []
        # Validate every marker before removing any. No blind stale-marker cleanup.
        for path in paths:
            if path.exists() or path.is_symlink():
                owned_marker(path, args.ticket)
        for path in paths:
            if path.exists():
                owned_marker(path, args.ticket)
                path.unlink()
                removed.append(str(path))
            else:
                absent.append(str(path))
        return {'status': 'selected_markers_cleared', 'ticket': args.ticket,
                'removed': removed, 'already_absent': absent,
                'note': 'Only these explicit targets were checked; no discovery of omitted markers. Confirm original targets before starting services.'}
    snapshot(args)  # Refuse unknown runtimes before any mutation.
    if any(p.exists() or p.is_symlink() for p in paths):
        raise ValueError('drain marker already exists; inspect its owner')
    payload = {'action': 'drain', 'requested_at': datetime.now(timezone.utc).isoformat(),
               'principal': 'hermes-safe-updates', 'epoch': '',
               'suppress_notification': False, 'maintenance_ticket': args.ticket}
    for path in paths:
        # Native presence-based markers fail toward draining while written.
        # A partial failure deliberately leaves owned markers for explicit cancel.
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
    return {'status': 'drain_requested', 'ticket': args.ticket,
            'note': 'Not proof of idle. Run wait; markers remain until explicit cancel.'}


def generations(body):
    if not isinstance(body, dict) or not isinstance(body.get('gateways'), list) or not body['gateways']:
        raise ValueError('invalid baseline gateway list')
    for g in body['gateways']:
        if not isinstance(g, dict) or not count(g.get('pid')):
            raise ValueError('invalid baseline gateway PID')
        number(g.get('start_time'))
    if not count(body['webui'].get('pid')):
        raise ValueError('invalid baseline WebUI PID')
    number(body['webui'].get('server_started_at'))
    return ([(g['home'], g['pid'], g['start_time']) for g in body['gateways']],
            body['webui']['health_url'], body['webui']['pid'], body['webui']['server_started_at'])


def baseline(args):
    before = json.loads(args.before.read_text())
    generations(before)
    if ([g['home'] for g in before['gateways']] != [str(p) for p in args.gateway_home]
            or before['webui']['health_url'] != args.webui_health_url):
        raise ValueError('baseline does not match selected runtimes')
    return before


def verify_replacement(args):
    before = baseline(args)
    deadline = time.monotonic() + args.timeout
    stable = None
    since = None
    while time.monotonic() < deadline:
        try:
            current = snapshot(args)
            ready = all(g['state'] == 'running' and g['code_sha'] == args.expect_agent_sha
                        and g['start_time'] != old['start_time']
                        for g, old in zip(current['gateways'], before['gateways']))
            ready = ready and current['webui']['server_started_at'] != before['webui']['server_started_at']
            identity = generations(current)
            now = time.monotonic()
            if ready:
                if identity != stable:
                    since, stable = now, identity
                if since is not None and now - since >= args.quiet_seconds:
                    return {'status': 'replacement_observed', 'snapshot': current,
                            'note': 'Gateway code verified; WebUI loaded revision and representative chat still require operator checks.'}
            else:
                stable, since = None, None
        except (OSError, ValueError, TypeError):
            stable, since = None, None
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise ValueError('replacement verification timed out; operator recovery required')


def refresh_owned_marker(path, ticket):
    # Renew the native one-hour lease without recreating a cancelled marker.
    # Keep the same inode: cancel can unlink it and the next probe will fail.
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('marker is not a regular file owned by this user')
        data = json.load(stream)
        if not isinstance(data, dict) or data.get('maintenance_ticket') != ticket:
            raise ValueError('marker belongs to another maintenance operation')
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data['requested_at'])).total_seconds()
        if age < 0:
            raise ValueError('drain marker is future-dated')
        if age >= 60:
            data['requested_at'] = datetime.now(timezone.utc).isoformat()
            stream.seek(0)
            json.dump(data, stream)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())


def wait_for_drain(args):
    before = baseline(args)
    quiet_since = None
    while True:
        for path in markers(args):
            refresh_owned_marker(path, args.ticket)
        try:
            current = snapshot(args)
        except OSError:
            # Transport failures do not spend an idle-wait budget. Owned
            # leases continue renewing; no unknown sample can prove idle.
            quiet_since = None
            time.sleep(1)
            continue
        if generations(current) != generations(before):
            raise ValueError('runtime generation changed while waiting')
        idle = (all(g['state'] == 'draining' and g['active_agents'] == 0 for g in current['gateways'])
                and current['webui']['active_runs'] == 0)
        now = time.monotonic()
        quiet_since = (quiet_since if quiet_since is not None else now) if idle else None
        if quiet_since is not None and now - quiet_since >= args.quiet_seconds:
            return {'status': 'drained_observed', 'snapshot': current,
                    'note': 'Not a global lock. Keep ingress closed and stop services before mutation.'}
        time.sleep(0.1)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'begin', 'cancel', 'wait', 'verify'])
    parser.add_argument('--expect-agent-sha', help='Exact full lowercase Git SHA expected from every gateway')
    parser.add_argument('--before', type=Path, help='Saved inspect JSON from before maintenance')
    parser.add_argument('--timeout', type=float, default=300, help='Replacement verification only; idle waits never expire')
    parser.add_argument('--quiet-seconds', type=float, default=3)
    parser.add_argument('--other-work-stopped', action='store_true',
                        help='Attest all CLI, other profiles/backends and standalone schedulers are stopped or separately protected')
    parser.add_argument('--ticket', help='Operator-generated UUID identifying ownership of drain markers')
    parser.add_argument('--gateway-home', type=Path, action='append', required=True)
    parser.add_argument('--webui-health-url', required=True)
    parser.add_argument('--webui-drain-dir', type=Path, required=True)
    parser.add_argument('--webui-pid', type=int, required=True,
                        help='Operator-verified WebUI Python PID, not a supervisor wrapper')
    args = parser.parse_args()
    args.gateway_home = sorted({p.expanduser().resolve(strict=True) for p in args.gateway_home})
    args.webui_drain_dir = args.webui_drain_dir.expanduser().resolve(strict=True)
    url = urllib.parse.urlsplit(args.webui_health_url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1')
            or url.username or url.password or url.fragment
            or url.path != '/health' or url.query != 'deep=1'):
        parser.error('health URL must be direct loopback http://.../health?deep=1 without credentials')
    if args.webui_pid <= 0:
        parser.error('WebUI PID must be positive')
    if args.action in ('begin', 'cancel', 'wait'):
        if not args.ticket or str(uuid.UUID(args.ticket)) != args.ticket:
            parser.error('begin/cancel require a canonical UUID --ticket')
    if args.action == 'wait' and (not args.before or not args.other_work_stopped):
        parser.error('wait requires --before and --other-work-stopped attestation')
    if args.action == 'verify' and (not args.before or not re.fullmatch('[0-9a-f]{40}', args.expect_agent_sha or '')):
        parser.error('verify requires --before and exact 40-character --expect-agent-sha')
    if not math.isfinite(args.quiet_seconds) or args.quiet_seconds <= 0:
        parser.error('quiet-seconds must be finite and positive')
    if args.action == 'verify' and not (math.isfinite(args.timeout)
            and args.quiet_seconds < args.timeout <= 1800):
        parser.error('verification requires quiet-seconds < timeout <= 1800')
    return args


def main():
    args = parse_args()
    if args.action in ('begin', 'cancel'):
        result = change_drain(args)
    elif args.action == 'wait':
        result = wait_for_drain(args)
    elif args.action == 'verify':
        result = verify_replacement(args)
    else:
        result = snapshot(args)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Deliberately do not echo raw service responses or exception URLs.
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
