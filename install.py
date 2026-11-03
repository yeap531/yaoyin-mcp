#!/usr/bin/env python3
"""使用客户端原生 CLI 注册药引 MCP，不设置模型供应商或登录凭据。"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def skill_locations(client):
    if client == 'claude':
        return [Path.home() / '.claude/skills/yaoyin']
    config_root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    return [config_root / 'skills/yaoyin', Path.home() / '.agents/skills/yaoyin']


def check_skill_locations(clients, skill):
    for client in clients:
        for target in skill_locations(client):
            if target.is_symlink() and target.resolve() == skill:
                continue
            if target.exists() or target.is_symlink():
                raise RuntimeError('已有不同的同名 skill，未覆盖：' + str(target))


def install_skill(client, skill):
    for target in skill_locations(client):
        if not target.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(skill, target_is_directory=True)
    print(client + '：药引工作流 skill 已安装')


def set_codex_timeout(path):
    before = path.read_text()
    header = re.search(r'(?m)^\[mcp_servers\.yaoyin\][ \t]*$', before)
    if not header:
        raise RuntimeError('未找到 Codex 的 yaoyin 配置段，请检查客户端版本')
    next_section = re.search(r'(?m)^\[', before[header.end():])
    end = header.end() + next_section.start() if next_section else len(before)
    section = before[header.end():end]
    setting = r'(?m)^tool_timeout_sec\s*=.*$'
    if re.search(setting, section):
        section = re.sub(setting, 'tool_timeout_sec = 130', section, count=1)
    else:
        section = '\ntool_timeout_sec = 130' + section
    after = before[:header.end()] + section + before[end:]
    if before != after:
        if path.read_text() != before:
            raise RuntimeError('Codex 配置同时发生变化，未写入超时设置，请重试')
        path.write_text(after)


def install(client, executable, server):
    if client == 'claude':
        # 仅检查用户级同名条目；不更改已有的不同服务。
        config_path = Path.home() / '.claude.json'
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        old = config.get('mcpServers', {}).get('yaoyin')
        wanted = {'type': 'stdio', 'command': sys.executable,
                  'args': [str(server)], 'timeout': 130000}
        if old and old != wanted:
            raise RuntimeError('Claude Code 已有不同的 yaoyin 配置，未覆盖')
        if not old:
            subprocess.run([executable, 'mcp', 'add-json', '--scope', 'user',
                            'yaoyin', json.dumps(wanted)], check=True)
    else:
        result = subprocess.run([executable, 'mcp', 'list', '--json'], check=True,
                                capture_output=True, text=True)
        old = next((item for item in json.loads(result.stdout) if item['name'] == 'yaoyin'), None)
        if old:
            if old.get('enabled') is False:
                raise RuntimeError('Codex 的 yaoyin MCP 已被禁用，保留原设置；请先自行启用')
            transport = old.get('transport', {})
            if (transport.get('command') != sys.executable or
                    transport.get('args') != [str(server)] or transport.get('env')):
                raise RuntimeError('Codex 已有不同的 yaoyin 配置，未覆盖')
        else:
            subprocess.run([executable, 'mcp', 'add', 'yaoyin', '--',
                            sys.executable, str(server)], check=True)
        config_root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        set_codex_timeout(config_root / 'config.toml')
    print(client + '：药引 MCP 已注册')


def main():
    parser = argparse.ArgumentParser(description='为 Codex / Claude Code 注册药引 MCP')
    parser.add_argument('--client', choices=['codex', 'claude', 'both'], default='both')
    parser.add_argument('--skills-only', action='store_true', help='仅安装工作流 skill，保留已有 MCP 配置')
    args = parser.parse_args()
    server = Path(__file__).resolve().with_name('server.py')
    skill = server.parent / 'skills/yaoyin'
    clients = ['codex', 'claude'] if args.client == 'both' else [args.client]
    check_skill_locations(clients, skill)
    executables = {client: shutil.which(client) for client in clients} if not args.skills_only else {}
    if any(not executable for executable in executables.values()):
        parser.error('请先安装所选客户端的官方 CLI，并使其位于 PATH 中')
    for client in clients:
        if not args.skills_only:
            install(client, executables[client], server)
        install_skill(client, skill)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('注册失败：' + type(exc).__name__ +
              ('：' + str(exc) if isinstance(exc, RuntimeError) else ''), file=sys.stderr)
        sys.exit(1)
