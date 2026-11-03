"""Portable installation checks using private fixture paths and mocked client CLIs."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install
import runtime


class PortableInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='yaoyin-install-check-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.resources = self.root / 'resources'
        self.skill = self.resources / 'skills' / 'yaoyin'
        (self.skill / 'agents').mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('fixture original\n', encoding='utf-8')
        (self.skill / 'agents' / 'openai.yaml').write_text('name: fixture\n', encoding='utf-8')
        self.targets = {client: self.root / client / 'skills' / 'yaoyin'
                        for client in ('codex', 'claude')}
        for patcher in (
            mock.patch.object(runtime, 'resources', return_value=self.resources),
            mock.patch.object(install, 'skill_locations', side_effect=lambda client: [self.targets[client]]),
            mock.patch.object(sys, 'frozen', True, create=True),
            mock.patch.object(Path, 'home', return_value=self.root / 'user'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_skills(self, client='both'):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(Path, 'symlink_to', side_effect=AssertionError('portable install requested symlink')), \
                mock.patch.object(runtime, 'cli_command', side_effect=AssertionError('skills-only requested CLI')):
            install.main(['--client', client, '--skills-only'])

    def test_frozen_skill_copy_reinstall_and_update_preserve_other_files(self):
        self.run_skills()
        for target in self.targets.values():
            self.assertFalse(target.is_symlink())
            self.assertEqual((target / 'SKILL.md').read_bytes(), (self.skill / 'SKILL.md').read_bytes())
            self.assertEqual(set(json.loads((target / '.yaoyin-files.json').read_text(encoding='utf-8'))),
                             {'SKILL.md', 'agents/openai.yaml'})
            (target / 'personal-notes.txt').write_text('keep my notes', encoding='utf-8')
        self.run_skills()
        (self.skill / 'SKILL.md').write_text('fixture upstream update\n', encoding='utf-8')
        self.run_skills()
        for target in self.targets.values():
            self.assertEqual((target / 'personal-notes.txt').read_text(encoding='utf-8'), 'keep my notes')
            self.assertEqual((target / 'SKILL.md').read_text(encoding='utf-8'), 'fixture upstream update\n')

    def test_user_modified_owned_skill_is_rejected_before_any_copy(self):
        self.run_skills()
        modified = self.targets['claude'] / 'SKILL.md'
        modified.write_text('user customization', encoding='utf-8')
        (self.skill / 'SKILL.md').write_text('new upstream', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '未覆盖'):
            self.run_skills()
        self.assertEqual(modified.read_text(encoding='utf-8'), 'user customization')
        self.assertEqual((self.targets['codex'] / 'SKILL.md').read_text(encoding='utf-8'), 'fixture original\n')

    def test_new_upstream_file_cannot_replace_user_file_in_later_client(self):
        self.run_skills()
        personal = self.targets['claude'] / 'personal-notes.txt'
        personal.write_text('keep my notes', encoding='utf-8')
        (self.skill / 'personal-notes.txt').write_text('new upstream file', encoding='utf-8')
        (self.skill / 'SKILL.md').write_text('new upstream', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '未覆盖'):
            self.run_skills()
        self.assertEqual(personal.read_text(encoding='utf-8'), 'keep my notes')
        self.assertEqual((self.targets['codex'] / 'SKILL.md').read_text(encoding='utf-8'), 'fixture original\n')
        self.assertFalse((self.targets['codex'] / 'personal-notes.txt').exists())

    def make_link(self, path, destination, directory=False):
        try:
            path.symlink_to(destination, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation unavailable for this test account')

    def test_recorded_file_symlink_is_rejected_without_overwriting_external_file(self):
        self.run_skills()
        target = self.targets['claude'] / 'SKILL.md'
        external = self.root / 'external.md'
        external.write_bytes(target.read_bytes())
        target.unlink()
        self.make_link(target, external)
        (self.skill / 'SKILL.md').write_text('new upstream', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '未覆盖'):
            self.run_skills()
        self.assertEqual(external.read_text(encoding='utf-8'), 'fixture original\n')
        self.assertEqual((self.targets['codex'] / 'SKILL.md').read_text(encoding='utf-8'), 'fixture original\n')

    def test_recorded_directory_symlink_is_rejected_without_overwriting_external_file(self):
        self.run_skills()
        directory = self.targets['claude'] / 'agents'
        external = self.root / 'external-agents'
        directory.rename(external)
        self.make_link(directory, external, directory=True)
        (self.skill / 'agents' / 'openai.yaml').write_text('name: new upstream\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '未覆盖'):
            self.run_skills()
        self.assertEqual((external / 'openai.yaml').read_text(encoding='utf-8'), 'name: fixture\n')

    def test_new_file_ancestor_symlink_is_rejected_before_any_copy(self):
        self.run_skills()
        external = self.root / 'external-new'
        external.mkdir()
        self.make_link(self.targets['claude'] / 'new-directory', external, directory=True)
        (self.skill / 'new-directory').mkdir()
        (self.skill / 'new-directory' / 'new.txt').write_text('new upstream', encoding='utf-8')
        (self.skill / 'SKILL.md').write_text('new upstream', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '未覆盖'):
            self.run_skills()
        self.assertFalse((external / 'new.txt').exists())
        self.assertEqual((self.targets['codex'] / 'SKILL.md').read_text(encoding='utf-8'), 'fixture original\n')

    def test_skill_parent_symlink_is_rejected_before_any_copy(self):
        self.run_skills()
        parent = self.targets['claude'].parent
        external = self.root / 'external-skills'
        parent.rename(external)
        self.make_link(parent, external, directory=True)
        (self.skill / 'SKILL.md').write_text('new upstream', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '未覆盖'):
            self.run_skills()
        self.assertEqual((external / 'yaoyin' / 'SKILL.md').read_text(encoding='utf-8'), 'fixture original\n')
        self.assertEqual((self.targets['codex'] / 'SKILL.md').read_text(encoding='utf-8'), 'fixture original\n')

    def test_unowned_skill_is_preserved(self):
        target = self.targets['codex']
        target.mkdir(parents=True)
        (target / 'SKILL.md').write_text('another tool', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '未覆盖'):
            self.run_skills('codex')
        self.assertEqual((target / 'SKILL.md').read_text(encoding='utf-8'), 'another tool')
        self.assertFalse((target / '.yaoyin-files.json').exists())

    def test_frozen_hook_points_to_executable_without_python_or_source(self):
        executable = str(self.root / "O'Neil portable folder" / ('yaoyin.exe' if os.name == 'nt' else 'yaoyin'))
        with mock.patch.object(sys, 'executable', executable):
            codex = install.hook_settings('codex', self.root / 'unused-entry.py')
            claude = install.hook_settings('claude', self.root / 'unused-entry.py')
        self.assertEqual(shlex.split(codex['command']), [executable, 'hook', '--client', 'codex'])
        self.assertEqual(claude['command'], executable)
        self.assertEqual(claude['args'], ['hook', '--client', 'claude'])
        if os.name == 'nt':
            self.assertIn("O''Neil portable folder", codex['commandWindows'])
            self.assertTrue(codex['commandWindows'].startswith('& '))

    def test_codex_registration_uses_native_cli_and_preserves_other_config(self):
        config_root = self.root / 'codex-config'
        config_root.mkdir()
        config = config_root / 'config.toml'
        config.write_text('model = "keep-model"\n[mcp_servers.yaoyin]\ncommand = "fixture"\n'
                          '[mcp_servers.other]\ncommand = "keep-other"\n', encoding='utf-8')
        executable = str(self.root / 'portable' / 'yaoyin')
        with mock.patch.dict(os.environ, {'CODEX_HOME': str(config_root)}), \
                mock.patch.object(sys, 'executable', executable), \
                mock.patch.object(install.subprocess, 'run',
                                  return_value=subprocess.CompletedProcess([], 0, stdout='[]')) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            install.install('codex', ['fixture-official-codex'], self.resources / 'server.py')
        self.assertEqual(run.call_args_list[0].args[0], ['fixture-official-codex', 'mcp', 'list', '--json'])
        self.assertEqual(run.call_args_list[1].args[0],
                         ['fixture-official-codex', 'mcp', 'add', 'yaoyin', '--', executable, 'mcp'])
        installed = config.read_text(encoding='utf-8')
        self.assertIn('model = "keep-model"', installed)
        self.assertIn('[mcp_servers.other]\ncommand = "keep-other"', installed)
        self.assertIn('tool_timeout_sec = 130', installed)


if __name__ == '__main__':
    unittest.main()
