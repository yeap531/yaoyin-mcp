#!/usr/bin/env python3
"""使用客户端原生 CLI 注册药引 MCP，不设置模型供应商或登录凭据。"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import hashlib
import runtime


def hook_settings(client, entry):
    command = runtime.command('hook', '--client', client)
    if client == 'claude':
        return {'type': 'command', 'command': command[0], 'args': command[1:],
                'timeout': 460, 'onFailure': 'block'}
    result = {'type': 'command', 'command': shlex.join(command), 'timeout': 460,
              'statusMessage': '药引本地输入与定时交接', 'additionalContextLimit': 0}
    if os.name == 'nt':
        invoke = '& ' + ' '.join("'" + item.replace("'", "''") + "'" for item in command)
        result['commandWindows'] = ('& { $OutputEncoding = [Console]::OutputEncoding = '
            '[System.Text.UTF8Encoding]::new($false); ' + invoke + ' }')
    return result


def install_hook(client, entry):
    if client == 'claude':
        version = subprocess.run([*runtime.cli_command('claude'), '--version'], check=True, capture_output=True,
                                 text=True).stdout.split()[0]
        if tuple(map(int, version.split('.'))) < (2, 1, 295):
            raise RuntimeError('Claude Code 至少需要 2.1.295，旧版无法在 hook 失败时阻止提交')
        path = Path.home() / '.claude/settings.json'
    else:
        features = subprocess.run([*runtime.cli_command('codex'), 'features', 'list'], check=True,
                                  capture_output=True, text=True).stdout
        if not re.search(r'(?m)^hooks\s+\S+\s+true\s*$', features):
            raise RuntimeError('当前 Codex 未启用原生 hooks，未更改客户端功能开关')
        path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'hooks.json'
    before = path.read_text(encoding='utf-8') if path.exists() else None
    config = json.loads(before) if before is not None else {}
    if config.get('disableAllHooks'):
        raise RuntimeError('客户端已禁用全部 hooks，保留原设置')
    wanted = hook_settings(client, entry)
    groups = config.setdefault('hooks', {}).setdefault('UserPromptSubmit', [])
    own = [hook for group in groups for hook in group.get('hooks', [])
           if 'entry_hook.py' in json.dumps(hook) or '药引本地输入与定时交接' in json.dumps(hook, ensure_ascii=False) or (hook.get('args', [])[-3:] == ['hook', '--client', client])]
    if own and own != [wanted]:
        raise RuntimeError('已有不同的药引入口 hook，未覆盖')
    if not own:
        groups.append({'hooks': [wanted]})
        path.parent.mkdir(parents=True, exist_ok=True)
        if (path.read_text(encoding='utf-8') if path.exists() else None) != before:
            raise RuntimeError('hook 配置同时发生变化，未写入，请重试')
        destination = path.resolve()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=destination.parent, prefix='.yaoyin-hook-', delete=False) as output:
                temporary = Path(output.name)
                output.write(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(client + '：药引本地输入 hook 已安装，只接受不带任务的启动命令')


def skill_locations(client):
    if client == 'claude':
        return [Path.home() / '.claude/skills/yaoyin']
    config_root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    return [config_root / 'skills/yaoyin', Path.home() / '.agents/skills/yaoyin']


def plain_skill_path(path):
    """写入前拒绝文件及其祖先目录中的符号链接。"""
    return not path.is_symlink() and all(
        not parent.is_symlink() and (not parent.exists() or parent.is_dir())
        for parent in path.parents)


def owned_skill(target):
    marker = target / '.yaoyin-files.json'
    if target.is_symlink() or marker.is_symlink() or not marker.is_file():
        return False
    try:
        files = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(files, dict) or not files or any(Path(name).is_absolute() or '..' in Path(name).parts for name in files):
            return False
        return all(plain_skill_path(target / name) and
                   (target / name).is_file() and
                   hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
                   for name, digest in files.items())
    except (OSError, ValueError, TypeError):
        return False


def check_skill_locations(clients, skill):
    for client in clients:
        for target in skill_locations(client):
            if target.is_symlink() and target.resolve() == skill:
                continue
            owned = owned_skill(target)
            if not owned and (target.exists() or target.is_symlink()):
                raise RuntimeError('已有不同或经修改的同名 skill，未覆盖：' + str(target))
            previous = json.loads((target / '.yaoyin-files.json').read_text(encoding='utf-8')) if owned else {}
            for source in skill.rglob('*'):
                if not source.is_file():
                    continue
                name = source.relative_to(skill).as_posix()
                destination = target / name
                if (not plain_skill_path(destination) or
                        (destination.exists() and (name not in previous or not destination.is_file()))):
                    raise RuntimeError('skill 目标含链接或用户新增的同名文件，未覆盖：' + str(destination))


def install_skill(client, skill):
    check_skill_locations([client], skill)
    for target in skill_locations(client):
        if target.is_symlink():
            continue
        if os.name != 'nt' and not getattr(sys, 'frozen', False) and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(skill, target_is_directory=True)
            continue
        target.mkdir(parents=True, exist_ok=True)
        files = {}
        for source in skill.rglob('*'):
            if not source.is_file():
                continue
            name = source.relative_to(skill).as_posix()
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            files[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        (target / '.yaoyin-files.json').write_text(json.dumps(files), encoding='utf-8')
    print(client + '：药引工作流 skill 已安装')


def set_codex_timeout(path):
    before = path.read_text(encoding='utf-8')
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
        if path.read_text(encoding='utf-8') != before:
            raise RuntimeError('Codex 配置同时发生变化，未写入超时设置，请重试')
        path.write_text(after, encoding='utf-8')


def install(client, executable, server):
    if client == 'claude':
        # 仅检查用户级同名条目；不更改已有的不同服务。
        config_path = Path.home() / '.claude.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        old = config.get('mcpServers', {}).get('yaoyin')
        command = runtime.command('mcp')
        wanted = {'type': 'stdio', 'command': command[0],
                  'args': command[1:], 'timeout': 130000}
        if old and old != wanted:
            raise RuntimeError('Claude Code 已有不同的 yaoyin 配置，未覆盖')
        if not old:
            subprocess.run([*executable, 'mcp', 'add-json', '--scope', 'user',
                            'yaoyin', json.dumps(wanted)], check=True, capture_output=True, text=True)
    else:
        result = subprocess.run([*executable, 'mcp', 'list', '--json'], check=True,
                                capture_output=True, text=True)
        old = next((item for item in json.loads(result.stdout) if item['name'] == 'yaoyin'), None)
        if old:
            if old.get('enabled') is False:
                raise RuntimeError('Codex 的 yaoyin MCP 已被禁用，保留原设置；请先自行启用')
            transport = old.get('transport', {})
            if (transport.get('command') != runtime.command('mcp')[0] or
                    transport.get('args') != runtime.command('mcp')[1:] or transport.get('env')):
                raise RuntimeError('Codex 已有不同的 yaoyin 配置，未覆盖')
        else:
            subprocess.run([*executable, 'mcp', 'add', 'yaoyin', '--',
                            *runtime.command('mcp')], check=True, capture_output=True, text=True)
        config_root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        set_codex_timeout(config_root / 'config.toml')
    print(client + '：药引 MCP 已注册')


def main(argv=None):
    parser = argparse.ArgumentParser(description='为 Codex / Claude Code 注册药引 MCP')
    parser.add_argument('--client', choices=['codex', 'claude', 'both'], default='both')
    parser.add_argument('--skills-only', action='store_true', help='仅安装工作流 skill，保留已有 MCP 配置')
    parser.add_argument('--entry-only', action='store_true', help='安装入口 hook 与 skill，保留已有 MCP 配置')
    args = parser.parse_args(argv)
    if args.skills_only and args.entry_only:
        parser.error('--skills-only 与 --entry-only 不能同时使用')
    server = runtime.resources() / 'server.py'
    skill = server.parent / 'skills/yaoyin'
    clients = ['codex', 'claude'] if args.client == 'both' else [args.client]
    check_skill_locations(clients, skill)
    executables = {client: runtime.cli_command(client) for client in clients} if not args.skills_only else {}
    if any(not executable for executable in executables.values()):
        parser.error('请先安装所选客户端的官方 CLI，并使其位于 PATH 中')
    for client in clients:
        if not args.skills_only and not args.entry_only:
            install(client, executables[client], server)
        if not args.skills_only:
            install_hook(client, server.with_name('entry_hook.py'))
        install_skill(client, skill)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('注册失败：' + type(exc).__name__ +
              ('：' + str(exc) if isinstance(exc, RuntimeError) else ''), file=sys.stderr)
        sys.exit(1)
