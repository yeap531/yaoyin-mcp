"""隐私入口回归检查；不打开输入框，不读取真实配置，不访问网络。"""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('yaoyin_entry',
    Path(__file__).resolve().parents[1] / 'entry_hook.py')
entry = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True
spec.loader.exec_module(entry)


class EntryTests(unittest.TestCase):
    def test_only_empty_trigger_opens_private_input(self):
        with mock.patch.object(entry, 'ask_task') as ask:
            self.assertEqual(entry.run({'hook_event_name': 'UserPromptSubmit',
                'prompt': '普通任务'}, 'codex'), {})
            for client in ['codex', 'claude']:
                for text in ['$yaoyin 不应放在这里的正文', '/yaoyin\n正文']:
                    result = entry.run({'hook_event_name': 'UserPromptSubmit', 'prompt': text}, client)
                    self.assertEqual(result['decision'], 'block')
                    self.assertNotIn('正文', json.dumps(result, ensure_ascii=False))
            ask.assert_not_called()

    def test_main_releases_complete_task_only_after_timer(self):
        task = '原文\n "引号" $() 末尾空格  '
        result = {'model': 'test-model', 'answer': '原始回答\n继续内容'}
        stream = io.StringIO()
        event = {'hook_event_name': 'UserPromptSubmit', 'prompt': '$yaoyin'}
        def wait(seconds):
            self.assertEqual(seconds, 299)
            self.assertEqual(stream.getvalue(), '')
        with mock.patch.object(entry, 'ask_task', return_value=task), \
                mock.patch.object(entry, 'call_mcp', return_value=result) as call, \
                mock.patch.object(entry.time, 'monotonic', side_effect=[0, 1]), \
                mock.patch.object(entry.time, 'sleep', side_effect=wait), \
                mock.patch.object(entry.sys, 'argv', ['entry_hook.py', '--client', 'codex']), \
                mock.patch.object(entry.sys, 'stdin', mock.Mock(buffer=io.BytesIO(json.dumps(event).encode()))), \
                mock.patch.object(entry.sys, 'stdout', stream), \
                mock.patch.object(entry.signal, 'signal'):
            entry.main()
        call.assert_called_once_with(task)
        context = json.loads(stream.getvalue())['hookSpecificOutput']['additionalContext']
        handoff = json.loads(context.split('\n', 1)[1])
        self.assertEqual(''.join(handoff['task']), task)
        self.assertEqual(''.join(handoff['answer']), result['answer'])
        self.assertTrue(handoff['yaoyin_handoff'])

    def test_failure_and_cancel_never_echo_task_or_answer(self):
        for error in [RuntimeError('secret task and answer'), KeyboardInterrupt()]:
            stream = io.StringIO()
            event = {'hook_event_name': 'UserPromptSubmit', 'prompt': '/yaoyin'}
            with mock.patch.object(entry, 'ask_task', return_value='secret task'), \
                    mock.patch.object(entry, 'call_mcp', side_effect=error), \
                    mock.patch.object(entry.sys, 'argv', ['entry_hook.py', '--client', 'claude']), \
                    mock.patch.object(entry.sys, 'stdin', mock.Mock(buffer=io.BytesIO(json.dumps(event).encode()))), \
                    mock.patch.object(entry.sys, 'stdout', stream), \
                    mock.patch.object(entry.signal, 'signal'):
                entry.main()
            self.assertNotIn('secret', stream.getvalue())
            self.assertEqual(json.loads(stream.getvalue())['decision'], 'block')

    def test_local_mcp_uses_raw_task_and_sanitized_environment(self):
        task = '原始任务\n保留空格  '
        content = {'model': 'test-model', 'answer': '完整回答'}
        process = mock.Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'jsonrpc':'2.0', 'id':2,
            'result':{'structuredContent':content}}).encode(), b'')
        with mock.patch.object(entry.subprocess, 'Popen', return_value=process) as popen:
            self.assertEqual(entry.call_mcp(task), content)
        command = popen.call_args.args[0]
        self.assertNotIn(task, json.dumps(command))
        self.assertEqual(popen.call_args.kwargs['env'], {'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8'})
        requests = [json.loads(line) for line in process.communicate.call_args.args[0].splitlines()]
        self.assertEqual(requests[-1]['params']['arguments']['messages'], [{'role':'user','content':task}])
        self.assertEqual(command[-1], '--handoff')

    def test_oversize_answer_is_blocked_without_truncation(self):
        with mock.patch.object(entry, 'ask_task', return_value='task'), \
                mock.patch.object(entry, 'call_mcp', return_value={'model':'test','answer':'x'*entry.MAX_HANDOFF}), \
                mock.patch.object(entry.time, 'sleep') as wait:
            with self.assertRaises(RuntimeError):
                entry.run({'hook_event_name':'UserPromptSubmit','prompt':'$yaoyin'}, 'codex')
            wait.assert_not_called()

    def test_mcp_timeout_terminates_private_worker(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.communicate.side_effect = subprocess.TimeoutExpired('test', 310)
        with mock.patch.object(entry.subprocess, 'Popen', return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired):
                entry.call_mcp('local task')
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)

    def test_long_handoff_preserves_text_without_long_lines(self):
        task = '长任务\n' * 3000
        answer = ('回答\n"\\\x00' * 2000) + '结束标记'
        with mock.patch.object(entry, 'ask_task', return_value=task), \
                mock.patch.object(entry, 'call_mcp', return_value={'model':'test','answer':answer}), \
                mock.patch.object(entry.time, 'monotonic', side_effect=[0, 300]):
            context = entry.run({'hook_event_name':'UserPromptSubmit','prompt':'$yaoyin'},
                                'codex')['hookSpecificOutput']['additionalContext']
        payload = json.loads(context.split('\n',1)[1])
        self.assertEqual(''.join(payload['task']), task)
        self.assertEqual(''.join(payload['answer']), answer)
        self.assertLess(max(map(len, context.splitlines())), 2000)


if __name__ == '__main__':
    unittest.main()
