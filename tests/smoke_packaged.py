#!/usr/bin/env python3
"""检查正式制品；PATH 不提供 Python，不读取真实用户配置。"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install
import runtime


def smoke_shell_hooks(executable, environment, directory):
    """用实际冻结程序执行客户端将使用的 hook 命令。"""
    with mock.patch.object(runtime.sys, 'frozen', True, create=True), \
            mock.patch.object(runtime.sys, 'executable', str(executable)):
        settings = {client: install.hook_settings(client, directory / 'unused-entry.py')
                    for client in ('codex', 'claude')}
    codex = settings['codex']
    if os.name == 'nt':
        powershell = shutil.which('powershell') or shutil.which('pwsh')
        if not powershell:
            raise AssertionError('PowerShell required to verify commandWindows')
        command = [powershell, '-NoProfile', '-NonInteractive', '-Command', codex['commandWindows']]
    else:
        command = ['/bin/sh', '-c', codex['command']]
    claude = settings['claude']
    commands = [('codex', command), ('claude', [claude['command'], *claude['args']])]
    event = json.dumps({'hook_event_name': 'UserPromptSubmit',
                        'prompt': 'ordinary packaged smoke task', 'cwd': str(directory)})
    for client, command in commands:
        result = subprocess.run(command, input=event, capture_output=True, encoding='utf-8',
                                env=environment, cwd=directory, timeout=30, check=True)
        if json.loads(result.stdout) != {}:
            raise AssertionError(f'{client} hook changed a non-trigger event: {result.stdout!r}')
        blocked = json.dumps({'hook_event_name':'UserPromptSubmit',
                              'prompt':'$yaoyin 此处不应填写正文 λ'}, ensure_ascii=False)
        result = subprocess.run(command, input=blocked, capture_output=True, encoding='utf-8',
                                env=environment, cwd=directory, timeout=30, check=True)
        response = json.loads(result.stdout)
        if response.get('decision') != 'block' or '药引隐私入口' not in response.get('reason',''):
            raise AssertionError(f'{client} hook did not preserve UTF-8 output')


def smoke(executable, gui=False):
    executable = Path(executable).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='yaoyin-packaged-smoke-') as temporary:
        home = Path(temporary)
        env = {key: value for key, value in os.environ.items() if key in {
            'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
            'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR',
            'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL',
        }}
        env.update(USERPROFILE=str(home),
                   LOCALAPPDATA=str(home / 'local'), APPDATA=str(home / 'roaming'),
                   XDG_CONFIG_HOME=str(home / 'config'), PATH=str(home))
        version = subprocess.run([str(executable), '--version'], capture_output=True,
                                 env=env, cwd=home, timeout=30, check=True, encoding='utf-8')
        if not re.search(r'\b0\.2\.0\b', version.stdout):
            raise AssertionError(f'Unexpected packaged version: {version.stdout!r}')
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-11-25', 'capabilities': {},
                        'clientInfo': {'name': 'packaged-smoke', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'},
        ]
        result = subprocess.run([str(executable), 'mcp'], env=env, cwd=home,
                                input=''.join(json.dumps(item) + '\n' for item in messages),
                                capture_output=True, encoding='utf-8', timeout=30, check=True)
        # 任何启动、GUI 或调试输出都会破坏真实 stdio 协议。
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        if len(responses) != 3 or [item.get('id') for item in responses] != [1, 2, 3]:
            raise AssertionError(f'Unexpected MCP responses: {responses!r}')
        if any('error' in item or item.get('jsonrpc') != '2.0' for item in responses):
            raise AssertionError(f'MCP error: {responses!r}')
        if responses[0]['result']['serverInfo']['version'] != '0.2.0':
            raise AssertionError('MCP server version differs from archive version')
        if responses[1]['result'] != {}:
            raise AssertionError('Unexpected ping response')
        if not any(tool['name'] == 'ask_model' for tool in responses[2]['result']['tools']):
            raise AssertionError('Packaged MCP did not expose ask_model')
        smoke_shell_hooks(executable, env, home)
        command = [str(executable), 'self-test'] + (['--gui'] if gui else [])
        subprocess.run(command, env=env, cwd=home, capture_output=True,
                       encoding='utf-8', timeout=90, check=True)
        if list(home.rglob('config.json')):
            raise AssertionError('Smoke test unexpectedly persisted an API configuration')
    print(f'Packaged version, MCP stdio, client shell hooks, isolated self-test and GUI={gui}: OK')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--gui', action='store_true')
    arguments = parser.parse_args()
    smoke(arguments.executable, arguments.gui)
