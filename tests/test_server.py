"""使用私有临时配置与假 HTTPS 连接验证安全边界，不访问外网。"""
import contextlib
import io
import json
import os
from pathlib import Path
import ssl
import socket
import threading
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import storage


class ServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='yaoyin-api-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve() / 'private'
        self.config_path = self.directory / 'config.json'
        self.config = {'endpoint':'https://models.example.test/v1/chat/completions',
                       'model':'test-model','api_key':'sk-unit-test-only',
                       'proxy':'http://127.0.0.1:7890'}
        storage.write_config(self.config, path=self.config_path)
        patcher = mock.patch.object(server, 'CONFIG', self.config_path)
        patcher.start(); self.addCleanup(patcher.stop)
        self.arguments = {'messages':[{'role':'user','content':'任务原文\n中文 "引号"  '},
                                     {'role':'assistant','content':'已有成果'},
                                     {'role':'user','content':'继续'}]}

    @contextlib.contextmanager
    def connection(self, answer='原始回答', status=200, error=None):
        response = mock.Mock(status=status)
        response.read.return_value = json.dumps({'choices':[{'message':{'content':answer}}]}).encode()
        connection = mock.Mock(sock=None)
        connection.getresponse.return_value = response
        if error is not None: connection.request.side_effect = error
        with mock.patch.object(server, 'ProxiedHTTPSConnection', return_value=connection) as factory:
            yield connection, response, factory

    def test_original_messages_fixed_proxy_and_tls(self):
        with mock.patch.dict(os.environ, {'NO_PROXY':'*','HTTPS_PROXY':'http://untrusted.invalid:1'}):
            with self.connection() as (connection, _, factory):
                result = server.call_api(self.arguments)
        self.assertEqual(factory.call_args.args, ('127.0.0.1',7890))
        context = factory.call_args.kwargs['context']
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        connection.set_tunnel.assert_called_once_with('models.example.test',443)
        self.assertEqual(connection.request.call_args.args,('POST','/v1/chat/completions'))
        kwargs = connection.request.call_args.kwargs
        body = json.loads(kwargs['body'])
        self.assertEqual(body['messages'],self.arguments['messages'])
        self.assertNotIn('system',body)
        self.assertEqual(body['model'],self.config['model'])
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer '+self.config['api_key'])
        self.assertEqual(result['structuredContent']['answer'],'原始回答')
        self.assertFalse(list(self.directory.glob('.yaoyin-request-*')))
        connection.close.assert_called()

    def test_cannot_override_target_or_authentication(self):
        with self.connection() as (connection,_,_):
            for name in ('endpoint','model','proxy','api_key'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    server.call_api(dict(self.arguments,**{name:'override'}))
            connection.request.assert_not_called()

    def test_unsafe_config_is_rejected(self):
        invalid={'endpoint':['http://models.example.test/v1/chat/completions',
            'https://user:password@models.example.test/v1/chat/completions',
            'https://models.example.test:444/v1/chat/completions',
            'https://models.example.test/v1/chat/completions?redirect=1',
            'https://models.example.test/v1/messages'],
            'proxy':['http://localhost:7890','http://192.0.2.1:7890',
                'http://127.0.0.1:0','http://user:password@127.0.0.1:7890',
                'http://127.0.0.1:7890/path']}
        for key,values in invalid.items():
            for value in values:
                with self.subTest(key=key,value=value),self.assertRaises(server.ApiError):
                    server.validate_config(dict(self.config,**{key:value}))

    def test_config_security_failure_prevents_connection(self):
        with mock.patch.object(storage,'check_private',side_effect=RuntimeError('unsafe')):
            with self.connection() as (connection,_,_):
                with self.assertRaises(server.ApiError):server.call_api(self.arguments)
                connection.request.assert_not_called()

    def test_upstream_failures_never_retry_follow_or_echo_error_body(self):
        for status,error in [(302,None),(401,None),(200,OSError('contains upstream secret'))]:
            with self.subTest(status=status,error=error):
                with self.connection(answer=self.config['api_key'],status=status,error=error) as (connection,response,_):
                    result=server.dispatch({'method':'tools/call','params':{
                        'name':'ask_model','arguments':self.arguments}})
                self.assertTrue(result['isError'])
                self.assertNotIn(self.config['api_key'],json.dumps(result))
                self.assertNotIn('upstream secret',json.dumps(result))
                self.assertEqual(connection.request.call_count,1)
                response.read.assert_not_called()

    def test_key_blocked_in_input_and_result(self):
        with self.connection() as (connection,_,_):
            with self.assertRaises(server.ApiError):
                server.call_api({'messages':[{'role':'user','content':self.config['api_key']}]})
            connection.request.assert_not_called()
        with self.connection(answer=self.config['api_key']):
            with self.assertRaises(server.ApiError):server.call_api(self.arguments)

    def test_absolute_deadline_and_cleanup(self):
        with self.connection() as (connection,_,_):
            connection.request.side_effect=lambda *a,**kw:time.sleep(.06)
            with self.assertRaises(server.ApiError):server.call_api(self.arguments,timeout=.02)
            self.assertGreaterEqual(connection.close.call_count,2)

    def test_slow_connect_then_tls_shares_one_deadline(self):
        client, peer = socket.socketpair()
        self.addCleanup(peer.close)
        def delayed_proxy():
            peer.recv(4096)
            time.sleep(.08)
            peer.sendall(b'HTTP/1.0 200 Connection established\r\n\r\n')
            time.sleep(.2)
        worker = threading.Thread(target=delayed_proxy, daemon=True)
        worker.start()
        def create(*args, **kwargs):
            client.settimeout(.12)
            return client
        context = ssl.create_default_context()
        started = time.monotonic()
        with mock.patch.object(server.ssl, 'create_default_context', return_value=context), \
                mock.patch.object(socket, 'create_connection', side_effect=create), \
                mock.patch.object(socket.socket, 'setsockopt'):
            with self.assertRaises(server.ApiError):
                server.call_api(self.arguments, timeout=.12)
        elapsed = time.monotonic() - started
        self.assertGreater(elapsed, .1)
        self.assertLess(elapsed, .18)
        worker.join(timeout=.4)

    def test_protocol_continues_after_invalid_request(self):
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
                  {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'ask_model','arguments':{'messages':[]}}},
                  {'jsonrpc':'2.0','id':3,'method':'ping'}]
        incoming=io.BytesIO(b'{broken}\n'+b''.join(json.dumps(r).encode()+b'\n' for r in requests))
        output=io.StringIO()
        with mock.patch.object(server.sys,'stdin',mock.Mock(buffer=incoming)), \
                mock.patch.object(server.sys,'stdout',output),mock.patch.object(server.signal,'signal'):
            server.main()
        responses=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn('error',responses[0])
        self.assertEqual(responses[1]['result']['serverInfo']['version'],'0.2.0')
        self.assertTrue(responses[2]['result']['isError'])
        self.assertEqual(responses[3]['result'],{})

    def test_connection_close_response_remains_cancellable(self):
        class PlainSocket(socket.socket):
            def do_handshake(self): pass
            def setsockopt(self, level, option, value):
                if level != socket.IPPROTO_TCP:
                    super().setsockopt(level, option, value)
        raw, peer = socket.socketpair()
        client = PlainSocket(family=raw.family, type=raw.type,
                             proto=raw.proto, fileno=raw.detach())
        self.addCleanup(peer.close)
        context = mock.Mock()
        context.wrap_socket.side_effect = lambda sock, **kwargs: sock
        payload = json.dumps({'choices':[{'message':{'content':'answer'}}]}).encode()
        def receive_headers():
            data = b''
            while b'\r\n\r\n' not in data:
                data += peer.recv(4096)
        def slow_response():
            try:
                receive_headers()
                peer.sendall(b'HTTP/1.0 200 Connection established\r\n\r\n')
                receive_headers()
                peer.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: ' +
                             str(len(payload)).encode() + b'\r\n\r\n')
                for index in range(0,len(payload),6):
                    time.sleep(.025)
                    peer.sendall(payload[index:index+6])
            except OSError:
                pass
        worker = threading.Thread(target=slow_response,daemon=True)
        worker.start()
        started = time.monotonic()
        with mock.patch.object(server.ssl,'create_default_context',return_value=context), \
                mock.patch.object(socket,'create_connection',return_value=client):
            with self.assertRaises(server.ApiError):
                server.call_api(self.arguments,timeout=.1)
        elapsed = time.monotonic()-started
        self.assertGreater(elapsed,.08)
        self.assertLess(elapsed,.16)
        worker.join(timeout=.4)


if __name__=='__main__':unittest.main()
