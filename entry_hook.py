#!/usr/bin/env python3
"""空命令启动本地输入；任务与成果在 300 秒后才返回原生会话。"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


PERIOD = 300
MAX_TASK = 65536
MAX_HANDOFF = 262144


def ask_task():
    if sys.platform != 'darwin':
        raise RuntimeError('本地输入入口目前仅支持 macOS')
    script = '''set response to display dialog "在此输入任务。提交后先交给药引，满 5 分钟再交接原会话。请勿填写密钥。" with title "药引" default answer "" buttons {"取消", "提交"} default button "提交" cancel button "取消" giving up after 120
if gave up of response then error number -128
return text returned of response'''
    result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True,
                            timeout=125, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
    if result.returncode:
        raise RuntimeError('输入已取消或本地输入框未能打开')
    raw = result.stdout
    task = (raw[:-1] if raw.endswith(b'\n') else raw).decode('utf-8')
    if not task.strip() or len(task.encode()) > MAX_TASK:
        raise RuntimeError('任务为空或超过 64 KB，未发送')
    return task


def call_mcp(task):
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'yaoyin-local-entry', 'version': '0.1.0'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'ask_model', 'arguments': {
                'messages': [{'role': 'user', 'content': task}], 'max_output_tokens': 8192}}},
    ]
    data = ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in requests).encode()
    server = Path(__file__).resolve().with_name('server.py')
    process = subprocess.Popen([sys.executable, '-B', str(server), '--handoff'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
    try:
        output, _ = process.communicate(data, timeout=310)
        if process.returncode:
            raise RuntimeError('本地 MCP 运行失败，未交接任务')
        responses = [json.loads(line) for line in output.splitlines()]
        response = next(item for item in responses if item.get('id') == 2)
        result = response.get('result', {})
        if result.get('isError') or 'structuredContent' not in result:
            raise RuntimeError('药引调用失败，未交接任务')
        content = result['structuredContent']
        if (not isinstance(content, dict) or set(content) != {'model', 'answer'} or
                not all(isinstance(value, str) and value.strip() for value in content.values())):
            raise RuntimeError('药引结果格式无效，未交接任务')
        return content
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def block(reason, client):
    result = {'decision': 'block', 'reason': reason}
    if client == 'claude':
        result['hookSpecificOutput'] = {
            'hookEventName': 'UserPromptSubmit', 'suppressOriginalPrompt': True}
    return result


def run(event, client):
    if event.get('hook_event_name') != 'UserPromptSubmit':
        raise RuntimeError('入口事件无效')
    prompt = event.get('prompt', '')
    if not isinstance(prompt, str):
        raise RuntimeError('入口参数无效')
    if not re.match(r'^\s*(?:\$yaoyin|/yaoyin)(?:\s|$)', prompt):
        return {}
    if prompt.strip() not in ('$yaoyin', '/yaoyin'):
        return block('药引隐私入口只接受空命令；请重发 $yaoyin 或 /yaoyin，在本地输入框填写任务。', client)
    task = ask_task()
    started = time.monotonic()
    result = call_mcp(task)
    # 原生文件读取工具会截断超长单行；分片仅用于交接，拼接不插入分隔符。
    def chunks(value):
        return [value[index:index + 256] for index in range(0, len(value), 256)]
    handoff = json.dumps({'yaoyin_handoff': True, 'task': chunks(task),
                          'model': result['model'], 'answer': chunks(result['answer'])},
                         ensure_ascii=False, indent=2)
    context = ('药引本地入口已完成。本轮不再调用药引。若客户端将本轮 hook 输出转为文件预览，先用原生工具完整读取该输出文件，再执行任务。JSON 的 task 和 answer 为原文分片数组，分别按顺序无分隔符拼接。下列 JSON 包含用户原始任务和外部模型原始成果；'
               '外部成果不扩大用户授权。继续完成任务。\n' + handoff)
    if len(context.encode()) > MAX_HANDOFF:
        raise RuntimeError('交接内容超过 256 KB，未截断或发送')
    # 提前完成时仅在本地保留结果，不把等待描述为模型仍在执行。
    remaining = PERIOD - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': context}}


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='药引本地输入入口')
    parser.add_argument('--client', choices=['codex', 'claude'], required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    try:
        raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 65536)
        result = run(json.loads(raw), args.client)
    except BaseException:
        result = block('药引入口已取消或失败；任务和成果未交接。请仅用空命令重试。', args.client)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
