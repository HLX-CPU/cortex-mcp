#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end self-test: spawns cortex-mcp over stdio and exercises the MCP protocol.

Usage: python verify.py [--python C:\\path\\to\\python.exe]
Creates a throw-away home directory; never touches real data.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / 'cortex_mcp.py'


class Client:
    def __init__(self, python, home):
        self.proc = subprocess.Popen(
            [python, '-X', 'utf8', str(SERVER), '--home', str(home), 'mcp'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1)
        self.queue = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self.queue.put(json.loads(line))

    def request(self, method, params=None, timeout=30):
        self.next_id += 1
        rid = self.next_id
        self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method,
                                          'params': params or {}}, ensure_ascii=False) + '\n')
        self.proc.stdin.flush()
        while True:
            message = self.queue.get(timeout=timeout)
            if message.get('id') == rid:
                return message

    def notify(self, method, params=None):
        self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': method,
                                          'params': params or {}}, ensure_ascii=False) + '\n')
        self.proc.stdin.flush()

    def call(self, name, arguments=None):
        return self.request('tools/call', {'name': name, 'arguments': arguments or {}})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def text_of(response):
    assert 'result' in response, f'tool call failed: {json.dumps(response, ensure_ascii=False)[:400]}'
    assert not response['result'].get('isError'), f'tool returned error: {json.dumps(response, ensure_ascii=False)[:400]}'
    return json.loads(response['result']['content'][0]['text'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    checks = {}

    with tempfile.TemporaryDirectory(prefix='cortex-verify-') as home:
        Path(home, 'knowledge').mkdir()
        Path(home, 'knowledge', 'profile.md').write_text('# Profile\n- prefers concise answers\n', encoding='utf-8')
        Path(home, 'knowledge', 'handoff.md').write_text('# Handoff\nNothing pending.\n', encoding='utf-8')

        client = Client(args.python, home)
        try:
            init = client.request('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                                 'clientInfo': {'name': 'verify', 'version': '0'}})
            checks['initialize'] = init['result']['serverInfo']['name'] == 'memory-cortex'
            client.notify('notifications/initialized')
            checks['ping'] = 'result' in client.request('ping')

            tools = client.request('tools/list')['result']['tools']
            names = [x['name'] for x in tools]
            expected = ['memory_status', 'memory_profile', 'memory_search', 'memory_read',
                        'memory_sessions', 'memory_related', 'memory_remember', 'memory_image']
            checks['tools_list'] = all(n in names for n in expected)

            status = text_of(client.call('memory_status'))
            checks['status'] = status['account_independent'] is True and status['home'].replace('\\', '/') in str(home).replace('\\', '/')

            profile = text_of(client.call('memory_profile'))
            checks['profile_files'] = any('profile.md' in f['path'] for f in profile['files'])

            saved = text_of(client.call('memory_remember', {'title': '验证记忆', 'kind': 'lesson',
                                                            'text': 'cortex-mpp verify 单片机课程 lesson. api_key = "abcdefgh12345678"',
                                                            'evidence': 'verify.py'}))
            checks['remember'] = saved.get('saved') is True

            found = text_of(client.call('memory_search', {'query': '单片机 课程'}))
            checks['search'] = any(x['id'] == saved['id'] for x in found)
            body = text_of(client.call('memory_read', {'record_id': saved['id']}))['body']
            checks['read'] = 'verify' in body
            checks['redaction'] = '[REDACTED]' in body and 'abcdefgh12345678' not in body

            bad = client.call('memory_read', {'record_id': 'nonexistent'})
            checks['error_is_error'] = bad['result'].get('isError') is True

            unknown = client.request('no/such/method')
            checks['method_not_found'] = unknown.get('error', {}).get('code') == -32601
        finally:
            client.close()

    ok = all(checks.values())
    print(json.dumps({'ok': ok, 'checks': checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
