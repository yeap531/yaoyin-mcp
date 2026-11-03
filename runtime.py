"""源码与可下载程序共用的运行入口和本地路径。"""
import os
from pathlib import Path
import shutil
import sys

VERSION = '0.2.0'


def resources():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def command(*args):
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    return [sys.executable, '-B', str(resources() / 'launcher.py'), *args]


def config_path():
    if os.name == 'nt':
        return Path(os.environ['LOCALAPPDATA']) / 'YaoyinMCP/config.json'
    return Path.home() / '.config/yaoyin-mcp/config.json'


def child_env():
    allowed = ('PATH', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'SystemRoot',
               'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'DISPLAY', 'WAYLAND_DISPLAY',
               'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'XAUTHORITY')
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update(LANG='en_US.UTF-8', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return result


def cli_command(client):
    executable = shutil.which(client)
    if not executable:
        candidates = [Path.home() / '.local/bin' / (client + ('.exe' if os.name == 'nt' else ''))]
        if os.name == 'nt':
            candidates.append(Path(os.environ['APPDATA']) / 'npm' / (client + '.cmd'))
        else:
            candidates += [Path('/opt/homebrew/bin') / client, Path('/usr/local/bin') / client]
        executable = next((str(path) for path in candidates if path.is_file()), None)
    if not executable:
        raise RuntimeError('未找到官方 ' + client + ' CLI，请先安装并登录')
    if os.name == 'nt' and Path(executable).suffix.lower() in ('.cmd', '.bat'):
        package = {'codex': '@openai/codex/bin/codex.js',
                   'claude': '@anthropic-ai/claude-code/cli.js'}[client]
        script = Path(executable).parent / 'node_modules' / package
        node = shutil.which('node')
        if not node or not script.is_file():
            raise RuntimeError('无法安全调用客户端脚本，请使用官方原生安装版或完整的 npm 安装')
        return [node, str(script)]
    return [executable]
