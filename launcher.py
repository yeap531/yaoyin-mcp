#!/usr/bin/env python3
"""药引下载版入口；MCP 与 hook 始终保留标准输入输出管道。"""
import argparse
import json
import subprocess
import sys

import runtime


def self_test(gui=False):
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'yaoyin-self-test', 'version': runtime.VERSION}}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'},
    ]
    result = subprocess.run(runtime.command('mcp'),
        input=''.join(json.dumps(item) + '\n' for item in messages).encode(),
        capture_output=True, timeout=30, env=runtime.child_env())
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    if result.returncode or len(responses) != 2 or responses[1].get('result') != {}:
        raise RuntimeError('本地 MCP 自检失败')
    if gui:
        import gui as local_gui
        local_gui.smoke_test()
    print(json.dumps({'version': runtime.VERSION, 'mcp': 'ok', 'gui': 'ok' if gui else 'not-tested'}))


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='药引 MCP：本地输入与原生会话交接')
    parser.add_argument('--version', action='version', version=runtime.VERSION)
    parser.add_argument('mode', nargs='?', default='setup',
                        choices=['setup', 'mcp', 'hook', 'install', 'self-test'])
    args, rest = parser.parse_known_args()
    if args.mode == 'mcp':
        import server
        p = argparse.ArgumentParser()
        p.add_argument('--handoff', action='store_true')
        options = p.parse_args(rest)
        server.REQUEST_TIMEOUT = 300 if options.handoff else 120
        server.main()
    elif args.mode == 'hook':
        import entry_hook
        entry_hook.main(rest)
    elif args.mode == 'install':
        import install
        install.main(rest)
    elif args.mode == 'self-test':
        p = argparse.ArgumentParser()
        p.add_argument('--gui', action='store_true')
        self_test(p.parse_args(rest).gui)
    else:
        if rest:
            parser.error('setup 不接受额外参数')
        import gui
        gui.setup()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('药引运行失败，请检查本地配置、客户端版本及桌面环境。', file=sys.stderr)
        sys.exit(1)
