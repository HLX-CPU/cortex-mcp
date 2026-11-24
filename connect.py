#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Register cortex-mcp into local AI clients.

Supported: codex, claude-code, claude-desktop, zcode, kimi, generic-json.
Every edit is additive and idempotent; other settings in the same file are
preserved byte-for-byte (TOML) or key-for-key (JSON). No credentials are read.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib  # Python 3.11+
    HAVE_TOMLLIB = True
except ImportError:  # pragma: no cover
    HAVE_TOMLLIB = False

SERVER_PY = Path(__file__).resolve().parent / 'cortex_mcp.py'
CLIENTS = ['codex', 'claude-code', 'claude-desktop', 'zcode', 'kimi', 'generic-json']
TZ = dt.timezone(dt.timedelta(hours=8))


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def server_command(python, home, name):
    """The exact argv every client will run."""
    return [python, '-X', 'utf8', str(SERVER_PY), '--home', str(home), 'mcp']


# ------------------------------------------------------------------ JSON files

def json_update(path: Path, keys, value=None, remove=False, create=False, dry=False):
    """Set or delete keys[0].keys[1]... in a JSON file, atomically, other keys preserved."""
    path = Path(path)
    if not path.exists():
        if not (create or remove):
            return {'path': str(path), 'status': 'missing', 'ok': False}
        data = {}
    else:
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except ValueError as e:
            return {'path': str(path), 'status': 'unparseable', 'error': str(e), 'ok': False}
        if not isinstance(data, dict):
            return {'path': str(path), 'status': 'not-a-json-object', 'ok': False}
    node = data
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            if remove:
                return {'path': str(path), 'status': 'not-installed', 'ok': True}
            child = {}
            node[key] = child
        node = child
    last = keys[-1]
    if remove:
        if last not in node:
            return {'path': str(path), 'status': 'not-installed', 'ok': True}
        del node[last]
        action = 'removed'
    else:
        if node.get(last) == value:
            return {'path': str(path), 'status': 'already-installed', 'ok': True}
        action = 'updated' if last in node else 'added'
        node[last] = value
    if dry:
        return {'path': str(path), 'status': action + '-dry-run', 'ok': True}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + '.bak-cortex-mcp'))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {'path': str(path), 'status': action, 'ok': True}


# ------------------------------------------------------------------ TOML files (codex)

def toml_entry(python, home, name):
    return {'command': python, 'args': server_command(python, home, name)[1:]}


def codex_install(config_path: Path, python, home, name, remove=False, dry=False):
    config_path = Path(config_path)
    marker = f'# CORTEX_MCP_{name.replace("-", "_").upper()}'
    if not config_path.exists():
        return {'path': str(config_path), 'status': 'missing', 'ok': False}
    text = config_path.read_text(encoding='utf-8-sig')
    if HAVE_TOMLLIB:
        data = tomllib.loads(text)
    else:
        data = None
    if remove:
        lines = text.splitlines(keepends=True)
        out, i, removed = [], 0, False
        while i < len(lines):
            if lines[i].strip() == marker:
                removed = True
                i += 1
                if i < len(lines) and lines[i].lstrip().startswith('['):
                    i += 1  # our own table header
                while i < len(lines) and not lines[i].lstrip().startswith('['):
                    i += 1
                continue
            out.append(lines[i]); i += 1
        if not removed:
            return {'path': str(config_path), 'status': 'not-installed', 'ok': True}
        if dry:
            return {'path': str(config_path), 'status': 'removed-dry-run', 'ok': True}
        shutil.copy2(config_path, config_path.with_name(config_path.name + '.bak-cortex-mcp'))
        config_path.write_text(''.join(out), encoding='utf-8')
        return {'path': str(config_path), 'status': 'removed', 'ok': True}
    entry = toml_entry(python, home, name)
    if data is not None:
        existing = data.get('mcp_servers', {}).get(name)
        if existing is not None:
            same = existing.get('command') == entry['command'] and existing.get('args') == entry['args']
            return {'path': str(config_path), 'status': 'already-installed' if same else 'conflict',
                    'existing': {k: existing.get(k) for k in ('command', 'args')}, 'ok': bool(same)}
    elif f'[mcp_servers."{name}"]' in text or f'[mcp_servers.{name}]' in text:
        return {'path': str(config_path), 'status': 'conflict-verify-skipped', 'ok': False}
    section = '\n'.join([
        marker,
        f'[mcp_servers."{name}"]',
        'command = ' + json.dumps(entry['command'], ensure_ascii=False),
        'args = ' + json.dumps(entry['args'], ensure_ascii=False),
        'startup_timeout_sec = 30',
        'tool_timeout_sec = 600',
        'enabled = true', ''])
    if dry:
        return {'path': str(config_path), 'status': 'added-dry-run', 'ok': True}
    before = config_path.read_bytes()
    with config_path.open('ab') as f:
        f.write(b'\n\n' + section.encode('utf-8')); f.flush(); os.fsync(f.fileno())
    after = config_path.read_bytes()
    if not after.startswith(before):
        return {'path': str(config_path), 'status': 'concurrent-modification', 'ok': False}
    if HAVE_TOMLLIB:
        try:
            tomllib.loads(config_path.read_text(encoding='utf-8-sig'))
        except ValueError as e:
            config_path.write_bytes(before)  # roll back our append
            return {'path': str(config_path), 'status': 'toml-parse-failed', 'error': str(e), 'ok': False}
    return {'path': str(config_path), 'status': 'added', 'ok': True}


# ------------------------------------------------------------------ client registry

def claude_code_install(python, home, name, remove=False, dry=False):
    if dry:
        return {'path': str(Path.home() / '.claude.json'), 'status': 'dry-run', 'ok': True,
                'note': 'would run: claude mcp add -s user (file fallback: ~/.cla.json)'}
    exe = shutil.which('claude') or shutil.which('claude.cmd') or shutil.which('claude.exe')
    if exe:
        cmd = [exe, 'mcp'] + (['remove', name, '-s', 'user'] if remove else
                              ['add', '-s', 'user', name, '--'] + server_command(python, home, name))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
            if proc.returncode == 0 or ('already' in (proc.stdout or '').lower() and not remove):
                return {'via': 'claude-cli', 'status': 'done', 'output': (proc.stdout or '').strip()[:500], 'ok': True}
        except (OSError, subprocess.TimeoutExpired):
            pass
    # File fallback: user scope lives in ~/.cla.json -> mcpServers.
    path = Path.home() / '.claude.json'
    return dict(json_update(path, ['mcpServers', name], remove=remove,
                            value={'command': python, 'args': server_command(python, home, name)[1:]},
                            create=True), via='file-fallback')


def install_client(client, opts):
    name, python, home = opts.name, opts.python, opts.home
    args = dict(python=python, home=home, name=name, remove=opts.remove, dry=opts.dry_run)
    if client == 'codex':
        homes = [Path(x) for x in opts.codex_home]
        if os.environ.get('CODEX_HOME'):
            homes.insert(0, Path(os.environ['CODEX_HOME']))
        homes.append(Path.home() / '.codex')
        seen, results = set(), []
        for h in homes:
            if str(h) in seen:
                continue
            seen.add(str(h))
            results.append(codex_install(h / 'config.toml', **args))
        return results
    if client == 'claude-code':
        return [claude_code_install(**args)]
    if client == 'claude-desktop':
        if sys.platform == 'win32':
            path = Path(os.environ.get('APPDATA', '')) / 'Claude' / 'claude_desktop_config.json'
        elif sys.platform == 'darwin':
            path = Path.home() / 'Library' / 'Application Support' / 'Claude' / 'claude_desktop_config.json'
        else:
            path = Path.home() / '.config' / 'Claude' / 'claude_desktop_config.json'
        return [dict(json_update(path, ['mcpServers', name], remove=opts.remove,
                                 value={'command': python, 'args': server_command(python, home, name)[1:]},
                                 create=opts.create_dirs or path.parent.exists(), dry=opts.dry_run))]
    if client == 'zcode':
        path = Path.home() / '.zcode' / 'cli' / 'config.json'
        if not path.parent.exists() and not (opts.create_dirs or opts.dry_run) and not opts.remove:
            return [{'path': str(path), 'status': 'client-not-found', 'ok': False}]
        return [dict(json_update(path, ['mcp', 'servers', name], remove=opts.remove,
                                 value={'type': 'stdio', 'command': python,
                                        'args': server_command(python, home, name)[1:],
                                        'enabled': True, 'timeoutMs': 60000},
                                 create=True, dry=opts.dry_run))]
    if client == 'kimi':
        root = Path(os.environ.get('KIMI_CODE_HOME') or Path.home() / '.kimi-code')
        path = root / 'mcp.json'
        if not root.exists() and not (opts.create_dirs or opts.dry_run) and not opts.remove:
            return [{'path': str(path), 'status': 'client-not-found', 'ok': False}]
        return [dict(json_update(path, ['mcpServers', name], remove=opts.remove,
                                 value={'command': python, 'args': server_command(python, home, name)[1:],
                                        'enabled': True, 'startupTimeoutMs': 30000, 'toolTimeoutMs': 600000},
                                 create=True, dry=opts.dry_run))]
    if client == 'generic-json':
        if not opts.path:
            return [{'status': 'generic-json-requires---path', 'ok': False}]
        return [dict(json_update(opts.path, [opts.key, name], remove=opts.remove,
                                 value={'command': python, 'args': server_command(python, home, name)[1:]},
                                 create=True, dry=opts.dry_run))]
    return [{'status': 'unknown-client', 'ok': False}]


def detect(client):
    found = []
    if client == 'codex':
        candidates = ([Path(os.environ['CODEX_HOME'])] if os.environ.get('CODEX_HOME') else []) + [Path.home() / '.codex']
        found = [str(p / 'config.toml') for p in candidates if (p / 'config.toml').exists()]
    elif client == 'claude-code':
        if shutil.which('claude') or shutil.which('claude.cmd'):
            found.append('claude on PATH')
        if (Path.home() / '.claude.json').exists():
            found.append(str(Path.home() / '.claude.json'))
    elif client == 'claude-desktop':
        if sys.platform == 'win32':
            p = Path(os.environ.get('APPDATA', '')) / 'Claude' / 'claude_desktop_config.json'
        elif sys.platform == 'darwin':
            p = Path.home() / 'Library' / 'Application Support' / 'Claude' / 'claude_desktop_config.json'
        else:
            p = Path.home() / '.config' / 'Claude' / 'claude_desktop_config.json'
        if p.parent.exists():
            found.append(str(p))
    elif client == 'zcode':
        if (Path.home() / '.zcode' / 'cli').exists():
            found.append(str(Path.home() / '.zcode' / 'cli' / 'config.json'))
    elif client == 'kimi':
        root = Path(os.environ.get('KIMI_CODE_HOME') or Path.home() / '.kimi-code')
        if root.exists():
            found.append(str(root / 'mcp.json'))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(prog='connect', description='Register cortex-mcp into AI clients.')
    parser.add_argument('--client', action='append', choices=CLIENTS)
    parser.add_argument('--all-found', action='store_true', help='install into every detected client')
    parser.add_argument('--list', action='store_true', help='only list detected clients')
    parser.add_argument('--name', default='memory-cortex')
    parser.add_argument('--home', default=None, help='cortex home (default $CORTEX_HOME or ~/.cortex-mcp)')
    parser.add_argument('--python', default=None, help='interpreter to run the server (default: this one)')
    parser.add_argument('--codex-home', action='append', default=[], help='extra CODEX_HOME, repeatable')
    parser.add_argument('--path', help='config file for generic-json')
    parser.add_argument('--key', default='mcpServers', help='top-level key for generic-json')
    parser.add_argument('--remove', action='store_true', help='uninstall instead of install')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--create-dirs', action='store_true', help='create client config dirs even if client is absent')
    opts = parser.parse_args(argv)

    home = Path(opts.home or os.environ.get('CORTEX_HOME') or Path.home() / '.cortex-mcp').resolve()
    opts.python = opts.python or sys.executable
    opts.home = str(home)

    if opts.list or not opts.client:
        report = {c: detect(c) for c in CLIENTS}
        print(json.dumps({'detected': report, 'server_command': server_command(opts.python, str(home), opts.name),
                          'note': 'pass --client <name> ... or --all-found to install'}, ensure_ascii=False, indent=2))
        return 0
    results = {}
    for client in opts.client:
        results[client] = install_client(client, opts)
    report = {'at': now(), 'name': opts.name, 'home': str(home), 'python': opts.python,
              'server': str(SERVER_PY), 'remove': opts.remove, 'results': results}
    (home / 'reports').mkdir(parents=True, exist_ok=True)
    out = home / 'reports' / ('connect-' + now().replace(':', '').replace('-', '') + '.json')
    if not opts.dry_run:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = all(item.get('ok') for items in results.values() for item in items)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
