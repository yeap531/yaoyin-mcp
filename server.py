#!/usr/bin/env python3
"""本地 stdio MCP；仅将显式消息发送到用户配置的固定 API。"""
import json
import os
import http.client
from pathlib import Path
import re
import signal
import socket
import ssl
import threading
import time
import sys
import runtime
import storage
from urllib.parse import urlsplit


CONFIG = runtime.config_path()
MAX_INPUT = 4 * 1024 * 1024
REQUEST_TIMEOUT = 120
VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
TOOL = {
    'name': 'ask_model',
    'description': '调用用户配置的模型 API，按 messages 原文生成回答。仅发送显式工具参数；不自动读取项目、会话历史或官方凭据，不运行命令。结果返回当前原生会话。',
    'inputSchema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'messages': {'type': 'array', 'minItems': 1, 'items': {
                'type': 'object', 'additionalProperties': False,
                'properties': {'role': {'type': 'string', 'enum': ['user', 'assistant']},
                               'content': {'type': 'string'}},
                'required': ['role', 'content']}},
            'max_output_tokens': {'type': 'integer', 'minimum': 64,
                                  'maximum': 8192, 'default': 4096}},
        'required': ['messages']},
    'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                    'idempotentHint': False, 'openWorldHint': True},
}


class ApiError(Exception):
    pass


class ProxiedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('请求已到期')
        self.timeout = remaining
        http.client.HTTPConnection.connect(self)
        hostname = self._tunnel_host or self.host
        # 先保存 SSL socket 再握手，计时器才能取消正在进行的 TLS 操作。
        secure = self._context.wrap_socket(self.sock, server_hostname=hostname,
                                           do_handshake_on_connect=False)
        self.sock = secure
        self.active_socket = secure
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('请求已到期')
        secure.settimeout(remaining)
        secure.do_handshake()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('请求已到期')
        secure.settimeout(remaining)


def read_config():
    try:
        storage.check_private(CONFIG)
    except (OSError, RuntimeError, ValueError):
        raise ApiError('私有配置权限或路径无效') from None
    return validate_config(json.loads(CONFIG.read_text(encoding='utf-8')))


def validate_config(config):
    if not isinstance(config, dict) or set(config) != {'endpoint', 'model', 'api_key', 'proxy'}:
        raise ApiError('配置必须包含 endpoint、model、api_key、proxy')
    if not all(isinstance(value, str) for value in config.values()):
        raise ApiError('配置值必须是字符串')
    try:
        endpoint = urlsplit(config['endpoint'])
        proxy = urlsplit(config['proxy'])
        endpoint_port, proxy_port = endpoint.port, proxy.port
    except ValueError:
        raise ApiError('endpoint 或 proxy 地址格式无效')
    if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username is not None
            or endpoint.password is not None or endpoint_port not in (None, 443)
            or endpoint.query or endpoint.fragment or not endpoint.path.endswith('/chat/completions')):
        raise ApiError('endpoint 必须为 HTTPS chat/completions 地址，不含凭据、查询参数或片段')
    if (proxy.scheme != 'http' or proxy.hostname != '127.0.0.1' or not proxy_port
            or proxy.username is not None or proxy.password is not None
            or proxy.path or proxy.query or proxy.fragment):
        raise ApiError('proxy 必须是 http://127.0.0.1:端口')
    if not config['model'] or len(config['model']) > 200:
        raise ApiError('model 必须为非空模型 ID')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', config['api_key']):
        raise ApiError('密钥格式无效')
    return config


def validate(arguments, model):
    if not isinstance(arguments, dict) or set(arguments) - {'messages', 'max_output_tokens'}:
        raise ValueError('仅接受 messages 和 max_output_tokens')
    messages = arguments.get('messages')
    if not isinstance(messages, list) or not messages:
        raise ValueError('messages 必须是非空数组')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'} or
                message['role'] not in ('user', 'assistant') or
                not isinstance(message['content'], str)):
            raise ValueError('每条消息仅包含 role 和原文 content，role 为 user 或 assistant')
    limit = arguments.get('max_output_tokens', 4096)
    if type(limit) is not int or not 64 <= limit <= 8192:
        raise ValueError('max_output_tokens 必须是 64 至 8192 的整数')
    body = json.dumps({'model': model, 'messages': messages,
                       'max_tokens': limit, 'stream': False}, ensure_ascii=False).encode()
    if len(body) > MAX_INPUT:
        raise ValueError('工具输入超过 4 MB，未发送且未截断')
    return body


def call_api(arguments, timeout=120):
    config = read_config()
    body = validate(arguments, config['model'])
    api_key = config['api_key']
    if api_key.encode() in body:
        raise ApiError('工具输入包含服务密钥，已阻止发送')
    endpoint = urlsplit(config['endpoint'])
    proxy = urlsplit(config['proxy'])
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    # 只连接明确指定的回环代理；http.client 不读取环境代理或登录凭据。
    connection = ProxiedHTTPSConnection(proxy.hostname, proxy.port,
                                         timeout=timeout, context=context)
    connection.deadline = time.monotonic() + timeout
    connection.set_tunnel(endpoint.hostname, 443)
    expired = threading.Event()
    def abort():
        expired.set()
        active = connection.sock or getattr(connection, 'active_socket', None)
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()
    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    response = None
    try:
        connection.request('POST', endpoint.path, body=body, headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
        response = connection.getresponse()
        if response.status != 200:
            raise ApiError('API 返回 HTTP ' + str(response.status) + '；未重定向或回退')
        payload = response.read(16 * 1024 * 1024 + 1)
        if expired.is_set():
            raise ApiError('API 请求超时，未切换到其他目标')
        if len(payload) > 16 * 1024 * 1024:
            raise ApiError('API 返回内容超过限制')
        try:
            result = json.loads(payload)
            answer = result['choices'][0]['message']['content']
        except (ValueError, KeyError, IndexError, TypeError):
            raise ApiError('API 返回格式无效')
        if not isinstance(answer, str) or not answer.strip():
            raise ApiError('API 未返回文本结果')
        if api_key in answer:
            raise ApiError('API 结果包含服务密钥，已阻止返回')
        return {'content': [{'type': 'text', 'text': answer}],
                'structuredContent': {'model': config['model'], 'answer': answer}}
    except (OSError, http.client.HTTPException):
        raise ApiError('API 连接失败或超时；未直连、重定向或回退') from None
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        connection.close()


def dispatch(request):
    method = request.get('method')
    params = request.get('params') or {}
    if method == 'initialize':
        version = params.get('protocolVersion')
        return {'protocolVersion': version if version in VERSIONS else VERSIONS[-1],
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'yaoyin-mcp', 'version': runtime.VERSION}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [TOOL]}
    if method == 'tools/call':
        if params.get('name') != TOOL['name']:
            raise ValueError('未知工具')
        try:
            return call_api(params.get('arguments', {}), timeout=REQUEST_TIMEOUT)
        except (ValueError, ApiError) as exc:
            return {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
    raise ValueError('不支持的方法')


def stop(signum, frame):
    raise SystemExit(0)


def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        line = sys.stdin.buffer.readline(MAX_INPUT + 65536)
        if not line:
            return
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
                raise ValueError('无效 JSON-RPC 请求')
            if 'id' not in request:
                continue
            request_id = request['id']
            result = dispatch(request)
            response = {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except ValueError:
            response = {'jsonrpc': '2.0', 'id': request_id,
                        'error': {'code': -32602, 'message': '无效请求或工具参数'}}
        except Exception:
            # 错误不含输入正文、上游响应、环境变量或凭据。
            response = {'jsonrpc': '2.0', 'id': request_id,
                        'error': {'code': -32603, 'message': '本地药引 MCP 调用失败'}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='药引 stdio MCP')
    parser.add_argument('--handoff', action='store_true', help='本地入口使用，API 请求上限 300 秒')
    if parser.parse_args().handoff:
        REQUEST_TIMEOUT = 300
    main()
