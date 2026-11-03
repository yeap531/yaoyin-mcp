"""仅使用临时配置和假 curl 进程，不读取用户配置或访问网络。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


def load_server():
    source = Path(__file__).resolve().parents[1] / 'server.py'
    spec = importlib.util.spec_from_file_location('yaoyin_test_server', source)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


server = load_server()


class ServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='yaoyin-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.directory.chmod(0o700)
        self.config_path = self.directory / 'config.json'
        self.config = {
            'endpoint': 'https://models.example.test/v1/chat/completions',
            'model': 'private-test-model',
            'api_key': 'sk-unit-test-only',
            'proxy': 'http://127.0.0.1:7890',
        }
        self.write_config()
        patcher = mock.patch.object(server, 'CONFIG', self.config_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.arguments = {'messages': [
            {'role': 'user', 'content': '第一行\n原样保留 "引号" 与空格  '},
            {'role': 'assistant', 'content': '已有回答'},
            {'role': 'user', 'content': '继续检查'},
        ]}

    def write_config(self, **updates):
        values = dict(self.config, **updates)
        self.config_path.write_text(json.dumps(values), encoding='utf-8')
        self.config_path.chmod(0o600)

    @contextlib.contextmanager
    def fake_curl(self, *, answer='模型的原始回答', status=200, returncode=0,
                  wait_timeout=False, communicate_timeout=False):
        process = mock.Mock()
        process.pid = 123456789
        process.returncode = returncode
        response = json.dumps({'choices': [{'message': {'content': answer}}]}).encode()
        process.communicate.return_value = (
            response + b'\n' + str(status).encode(), self.config['api_key'].encode())
        if communicate_timeout:
            process.communicate.side_effect = subprocess.TimeoutExpired('fake curl', 125)
        if wait_timeout:
            process.wait.side_effect = subprocess.TimeoutExpired('fake curl', 0.05)
        observed = {}

        def start(command, **kwargs):
            observed['command'] = list(command)
            observed['options'] = kwargs
            request = Path(command[command.index('--data-binary') + 1][1:])
            observed['request'] = request
            observed['body'] = json.loads(request.read_bytes())
            observed['request_mode'] = stat.S_IMODE(request.stat().st_mode)
            return process

        with mock.patch.object(server.subprocess, 'Popen', side_effect=start) as popen, \
                mock.patch.object(server.os, 'killpg'):
            yield process, popen, observed

    def assert_no_requests(self):
        self.assertEqual(list(self.directory.glob('.yaoyin-request-*')), [])

    def test_original_messages_and_configured_target(self):
        with self.fake_curl() as (_, popen, observed):
            result = server.call_api(self.arguments)
        self.assertEqual(observed['body']['messages'], self.arguments['messages'])
        self.assertNotIn('system', observed['body'])
        self.assertEqual(observed['body']['model'], self.config['model'])
        self.assertEqual(observed['command'][-1], self.config['endpoint'])
        self.assertEqual(result['content'][0]['text'], '模型的原始回答')
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(observed['request_mode'], 0o600)
        self.assert_no_requests()

    def test_tool_cannot_override_target_or_authentication(self):
        with mock.patch.object(server.subprocess, 'Popen') as popen:
            for name in ('endpoint', 'model', 'proxy', 'api_key'):
                with self.subTest(parameter=name), self.assertRaises(ValueError):
                    server.call_api(dict(self.arguments, **{name: 'untrusted-override'}))
            popen.assert_not_called()
        self.assert_no_requests()

    def test_unsafe_endpoints_and_proxies_are_rejected(self):
        invalid = {
            'endpoint': [
                'http://models.example.test/v1/chat/completions',
                'https://user:password@models.example.test/v1/chat/completions',
                'https://models.example.test:444/v1/chat/completions',
                'https://models.example.test/v1/chat/completions?other=target',
                'https://models.example.test/v1/chat/completions#fragment',
                'https://models.example.test/v1/messages',
            ],
            'proxy': [
                'https://127.0.0.1:7890', 'http://localhost:7890',
                'http://192.0.2.1:7890', 'http://127.0.0.1:0',
                'http://user:password@127.0.0.1:7890',
                'http://127.0.0.1:7890/path', 'http://127.0.0.1:7890?extra=1',
            ],
        }
        with mock.patch.object(server.subprocess, 'Popen') as popen:
            for name, values in invalid.items():
                for value in values:
                    with self.subTest(field=name, value=value):
                        self.write_config(**{name: value})
                        with self.assertRaises(server.ApiError):
                            server.call_api(self.arguments)
            popen.assert_not_called()

    def test_private_config_permissions_and_ownership(self):
        self.config_path.chmod(0o644)
        with self.assertRaises(server.ApiError):
            server.read_config()
        self.config_path.chmod(0o600)
        self.directory.chmod(0o755)
        with self.assertRaises(server.ApiError):
            server.read_config()
        self.directory.chmod(0o700)
        other_uid = os.getuid() + 1
        with mock.patch.object(server.os, 'getuid', return_value=other_uid):
            with self.assertRaises(server.ApiError):
                server.read_config()
        alias = self.directory / 'config-link.json'
        alias.symlink_to(self.config_path)
        with mock.patch.object(server, 'CONFIG', alias):
            with self.assertRaises(server.ApiError):
                server.read_config()

    def test_curl_keeps_key_out_of_argv_and_environment(self):
        with self.fake_curl() as (process, _, observed):
            server.call_api(self.arguments)
        command, options = observed['command'], observed['options']
        self.assertNotIn(self.config['api_key'], json.dumps([command, options['env']]))
        self.assertIn(self.config['api_key'].encode(), process.communicate.call_args.args[0])
        self.assertEqual(command[1], '--disable')
        self.assertEqual(command[command.index('--config') + 1], '-')
        self.assertEqual(command[command.index('--proxy') + 1], self.config['proxy'])
        self.assertEqual(command[command.index('--noproxy') + 1], '')
        self.assertEqual(command[command.index('--proto') + 1], '=https')
        self.assertEqual(command[command.index('--max-redirs') + 1], '0')
        self.assertFalse(set(command) & {'-k', '--insecure', '-L', '--location',
                                         '--location-trusted'})
        self.assertFalse(set(options['env']) & {'NO_PROXY', 'no_proxy',
                                               'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'})

    def test_upstream_failures_do_not_retry_or_disclose_response(self):
        for status, returncode, timeout in ((302, 0, False), (200, 7, False),
                                           (200, 0, True)):
            with self.subTest(status=status, returncode=returncode, timeout=timeout):
                with self.fake_curl(answer=self.config['api_key'], status=status,
                                    returncode=returncode, communicate_timeout=timeout) as (_, popen, _):
                    result = server.dispatch({'method': 'tools/call', 'params': {
                        'name': 'ask_model', 'arguments': self.arguments}})
                self.assertTrue(result['isError'])
                self.assertNotIn(self.config['api_key'], json.dumps(result))
                self.assertEqual(popen.call_count, 1)
                self.assert_no_requests()

    def test_key_is_blocked_in_input_and_answer(self):
        with mock.patch.object(server.subprocess, 'Popen') as popen:
            with self.assertRaises(server.ApiError):
                server.call_api({'messages': [{'role': 'user', 'content': self.config['api_key']}]})
            popen.assert_not_called()
        with self.fake_curl(answer=self.config['api_key']):
            result = server.dispatch({'method': 'tools/call', 'params': {
                'name': 'ask_model', 'arguments': self.arguments}})
        self.assertTrue(result['isError'])
        self.assertNotIn(self.config['api_key'], json.dumps(result))
        self.assert_no_requests()

    def test_invalid_config_url_does_not_disclose_secret(self):
        self.write_config(endpoint='https://models.example.test:' +
                          self.config['api_key'] + '/v1/chat/completions')
        with mock.patch.object(server.subprocess, 'Popen') as popen:
            result = server.dispatch({'method': 'tools/call', 'params': {
                'name': 'ask_model', 'arguments': self.arguments}})
        self.assertTrue(result['isError'])
        self.assertNotIn(self.config['api_key'], json.dumps(result))
        popen.assert_not_called()

    def test_wait_timeout_cannot_leave_request_or_replace_answer(self):
        # 保留曾经复现的回归：进程回收超时也必须清理请求正文。
        with self.fake_curl(wait_timeout=True) as (_, _, observed):
            result = server.call_api(self.arguments)
        self.assertEqual(result['content'][0]['text'], '模型的原始回答')
        self.assertFalse(observed['request'].exists())
        self.assert_no_requests()

    def test_stdio_continues_after_protocol_and_tool_errors(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-06-18'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'ask_model', 'arguments': {'messages': []}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
             'params': {'name': 'ask_model', 'arguments': self.arguments}},
            {'jsonrpc': '2.0', 'id': 5, 'method': 'ping'},
        ]
        incoming = io.BytesIO(b'{broken json}\n' + b''.join(
            json.dumps(request).encode() + b'\n' for request in requests))
        stdin = mock.Mock(buffer=incoming)
        stdout = io.StringIO()
        with self.fake_curl() as (_, popen, _), \
                mock.patch.object(server.sys, 'stdin', stdin), \
                mock.patch.object(server.sys, 'stdout', stdout), \
                mock.patch.object(server.signal, 'signal'), \
                mock.patch.object(server.os, 'umask'):
            server.main()
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(responses), 6)
        self.assertIn('error', responses[0])
        self.assertEqual(responses[1]['result']['serverInfo']['name'], 'yaoyin-mcp')
        self.assertEqual(responses[2]['result']['tools'][0]['name'], 'ask_model')
        self.assertTrue(responses[3]['result']['isError'])
        self.assertEqual(responses[4]['result']['content'][0]['text'], '模型的原始回答')
        self.assertEqual(responses[5], {'jsonrpc': '2.0', 'id': 5, 'result': {}})
        self.assertEqual(popen.call_count, 1)
        self.assert_no_requests()


if __name__ == '__main__':
    unittest.main()
