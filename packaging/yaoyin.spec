# 在目标系统与架构原生构建，不把此配置用于交叉编译。
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
hidden = ['tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'certifi']
if sys.platform == 'win32':
    hidden += ['win32security', 'win32api', 'win32con', 'win32file',
               'win32event', 'win32process', 'pywintypes', 'ntsecuritycon']

analysis = Analysis(
    [str(root / 'launcher.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'skills'), 'skills')] + collect_data_files('certifi'),
    hiddenimports=hidden,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
pyz = PYZ(analysis.pure)
# 保留 console 构建，使 MCP 和 hook 可正常使用 stdin/stdout。
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name='yaoyin', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas,
                 strip=False, upx=False, name='yaoyin')
