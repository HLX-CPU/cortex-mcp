#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Private localhost dashboard: bounded read-only APIs plus fixed sync/backup jobs."""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # work even under python -I

import cortex_mcp as cm

IDENTITY = 'cortex-mcp-v1'
JOB = {'running': False, 'type': None, 'result': None, 'error': None}
JOB_LOCK = threading.Lock()


def port():
    return int(cm.cfg('dashboard_port'))


def run_job(kind):
    with JOB_LOCK:
        if JOB['running']:
            return dict(JOB)
        JOB.update(running=True, type=kind, result=None, error=None)

    def work():
        try:
            if kind == 'sync':
                result = cm.run_sync()
            else:
                result = cm.backup()
            with JOB_LOCK:
                JOB.update(running=False, result=result)
        except Exception as e:  # noqa: BLE001
            with JOB_LOCK:
                JOB.update(running=False, error=cm.redact(str(e)))
        log = cm.home() / 'reports' / 'dashboard-jobs.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'at': cm.now(), **JOB}, ensure_ascii=False) + '\n')
    threading.Thread(target=work, daemon=True).start()
    return dict(JOB)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def valid_host(self):
        return self.headers.get('Host', '') in [f'127.0.0.1:{port()}', f'localhost:{port()}']

    def send(self, value, code=200, content_type='application/json; charset=utf-8'):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.valid_host():
            self.send({'error': 'localhost only.'}, 403)
            return
        origin = self.headers.get('Origin')
        if origin and origin not in [f'http://127.0.0.1:{port()}', f'http://localhost:{port()}']:
            self.send({'error': 'This origin may not read the memory store.'}, 403)
            return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        get = lambda name, default=None: query.get(name, [default])[0]
        try:
            if parsed.path == '/':
                self.send((Path(__file__).parent / 'dashboard.html').read_bytes(),
                          content_type='text/html; charset=utf-8')
            elif parsed.path == '/api/health':
                self.send({'identity': IDENTITY})
            elif parsed.path == '/api/status':
                self.send(cm.status())
            elif parsed.path == '/api/search':
                self.send(cm.search(get('q', ''), limit=20, topic=get('topic')))
            elif parsed.path == '/api/profile':
                self.send(cm.profile(full=get('full') == '1'))
            elif parsed.path == '/api/read':
                self.send(cm.read_record(get('id', ''), offset=int(get('offset', 0))))
            elif parsed.path == '/api/sessions':
                self.send(cm.sessions(limit=100, topic=get('topic')))
            elif parsed.path == '/api/job':
                self.send(dict(JOB))
            else:
                self.send({'error': 'not found.'}, 404)
        except Exception as e:  # noqa: BLE001
            self.send({'error': cm.redact(str(e))}, 400)

    def do_POST(self):
        allowed = [f'http://127.0.0.1:{port()}', f'http://localhost:{port()}']
        if not self.valid_host() or self.headers.get('Origin') not in allowed or \
                self.headers.get('Content-Type') != 'application/json':
            self.send({'error': 'Use the buttons on the local dashboard page.'}, 403)
            return
        length = int(self.headers.get('Content-Length', 0))
        if length > 2048:
            self.send({'error': 'request too large.'}, 413)
            return
        self.rfile.read(length)
        if self.path not in ['/api/sync', '/api/backup']:
            self.send({'error': 'unsupported.'}, 404)
            return
        self.send(run_job('sync' if self.path == '/api/sync' else 'backup'), 202)


def serve(open_browser=True):
    url = f'http://127.0.0.1:{port()}'
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
            if json.loads(response.read()).get('identity') == IDENTITY:
                if open_browser:
                    webbrowser.open(url)
                return
    except Exception:  # noqa: BLE001
        pass
    server = ThreadingHTTPServer(('127.0.0.1', port()), Handler)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='cortex-mcp local dashboard')
    parser.add_argument('--home', default=None)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    cm.configure(args.home)
    serve(open_browser=not args.no_browser)
