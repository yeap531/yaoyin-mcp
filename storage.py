"""私有配置存储；拒绝修补或覆盖权限不安全的已有文件。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import stat


def _path(value):
    path = Path(value).expanduser()
    if '..' in path.parts:
        raise ValueError('配置路径不能包含上级目录跳转')
    path = Path(os.path.abspath(path))
    if os.name == 'nt':
        # 不接受网络共享、设备名、备用数据流或 Win32 路径别名。
        if len(path.drive) != 2 or path.drive[1] != ':':
            raise PermissionError('配置必须保存在本机普通磁盘路径')
        for part in path.parts[1:]:
            reserved = re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',
                                    part.split('.', 1)[0], re.IGNORECASE)
            if ':' in part or part.endswith((' ', '.')) or reserved:
                raise PermissionError('配置路径包含不安全的文件名')
    return path


def _plain(info, directory):
    if (stat.S_ISLNK(info.st_mode) or
            getattr(info, 'st_file_attributes', 0) & 0x400):
        raise PermissionError('配置路径不能包含符号链接或重解析点')
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or (not directory and info.st_nlink != 1):
        raise PermissionError('配置必须是普通文件，目录必须是普通目录')


def _unix_private(info, directory):
    _plain(info, directory)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise PermissionError('私有配置必须由当前用户拥有，目录权限 700、文件权限 600')


@contextmanager
def _unix_parent(path, create=False):
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            created = False
            try:
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            _plain(info, True)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            if created:
                if os.fstat(fd).st_uid != os.getuid():
                    raise PermissionError('新建配置目录的属主无效')
                os.fchmod(fd, 0o700)
        _unix_private(os.fstat(fd), True)
        yield fd
    finally:
        os.close(fd)


def _unix_file(fd, name):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    _unix_private(info, False)
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)


def _win():
    # 必须使用真实 ACL；缺少 pywin32 时不能退回 chmod。
    try:
        import ntsecuritycon
        import pywintypes
        import win32api
        import win32con
        import win32file
        import win32security
    except ImportError:
        raise PermissionError('Windows 私有配置需要 pywin32，未创建或修改配置') from None
    return win32api, win32con, win32file, win32security, pywintypes, ntsecuritycon


def _windows_identity():
    api, con, _, security, _, _ = _win()
    token = security.OpenProcessToken(api.GetCurrentProcess(), con.TOKEN_QUERY)
    try:
        user = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    return user, security.ConvertStringSidToSid('S-1-5-18')


def _windows_attributes(directory):
    _, con, _, security, types, rights = _win()
    user, system = _windows_identity()
    acl = security.ACL()
    flags = con.OBJECT_INHERIT_ACE | con.CONTAINER_INHERIT_ACE if directory else 0
    sids = {security.ConvertSidToStringSid(sid): sid for sid in (user, system)}
    for sid in sids.values():
        acl.AddAccessAllowedAceEx(security.ACL_REVISION, flags, rights.FILE_ALL_ACCESS, sid)
    descriptor = security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(user, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(security.SE_DACL_PROTECTED, security.SE_DACL_PROTECTED)
    attributes = types.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    attributes.bInheritHandle = False
    return attributes


def _windows_private(handle, directory):
    _, con, files, security, _, rights = _win()
    info = files.GetFileInformationByHandle(handle)
    if info[0] & con.FILE_ATTRIBUTE_REPARSE_POINT or bool(info[0] & con.FILE_ATTRIBUTE_DIRECTORY) != directory:
        raise PermissionError('配置路径不能包含重解析点或特殊文件')
    if not directory and info[7] != 1:
        raise PermissionError('配置文件不能有多个硬链接')
    descriptor = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    user, system = _windows_identity()
    sid_text = security.ConvertSidToStringSid
    if descriptor.GetSecurityDescriptorOwner() != user:
        raise PermissionError('配置必须由当前用户拥有')
    control, _ = descriptor.GetSecurityDescriptorControl()
    acl = descriptor.GetSecurityDescriptorDacl()
    if not control & security.SE_DACL_PROTECTED or acl is None:
        raise PermissionError('私有配置必须使用受保护的 DACL')
    allowed = {sid_text(user), sid_text(system)}
    seen = set()
    flags = con.OBJECT_INHERIT_ACE | con.CONTAINER_INHERIT_ACE if directory else 0
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if (len(ace) != 3 or ace[0] != (con.ACCESS_ALLOWED_ACE_TYPE, flags) or
                ace[1] != rights.FILE_ALL_ACCESS or sid_text(ace[2]) not in allowed or
                sid_text(ace[2]) in seen):
            raise PermissionError('私有配置 ACL 仅允许当前用户和 SYSTEM')
        seen.add(sid_text(ace[2]))
    if seen != allowed:
        raise PermissionError('私有配置 ACL 缺少当前用户或 SYSTEM')
    return (info[4], info[8], info[9], info[3], info[5], info[6])


def _windows_open(path):
    _, con, files, _, _, _ = _win()
    return files.CreateFile(str(path), con.READ_CONTROL,
        con.FILE_SHARE_READ | con.FILE_SHARE_WRITE, None, con.OPEN_EXISTING,
        con.FILE_FLAG_BACKUP_SEMANTICS | files.FILE_FLAG_OPEN_REPARSE_POINT, None)


@contextmanager
def _windows_parent(path, create=False):
    _, con, files, _, _, _ = _win()
    handles = []
    current = Path(path.anchor)
    try:
        # 保持祖先目录句柄，禁止在写入期间将路径改名或换为 junction。
        for part in ('', *path.parts[1:]):
            if part:
                current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not create:
                    raise
                files.CreateDirectory(str(current), _windows_attributes(True))
                info = current.lstat()
            _plain(info, True)
            handle = _windows_open(current)
            handles.append(handle)
            attributes = files.GetFileInformationByHandle(handle)[0]
            if attributes & con.FILE_ATTRIBUTE_REPARSE_POINT or not attributes & con.FILE_ATTRIBUTE_DIRECTORY:
                raise PermissionError('配置祖先必须是无重解析点的普通目录')
        _windows_private(handles[-1], True)
        yield
    finally:
        for handle in reversed(handles):
            handle.Close()


def _windows_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    _plain(info, False)
    handle = _windows_open(path)
    try:
        return _windows_private(handle, False)
    finally:
        handle.Close()


def check_private(path):
    """只检查元数据；不读取配置内容，不修正已有权限。"""
    path = _path(path)
    if os.name == 'nt':
        with _windows_parent(path.parent):
            exists = _windows_file(path)
    else:
        with _unix_parent(path.parent) as directory:
            exists = _unix_file(directory, path.name)
    if exists is None:
        raise FileNotFoundError('私有配置文件不存在')


def write_config(config, path=None):
    """从创建起保护临时文件，验证原文件后原子替换；不写配置值日志。"""
    if not isinstance(config, dict):
        raise ValueError('配置必须是 JSON 对象')
    data = (json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if path is None:
        from runtime import config_path
        path = config_path()
    path = _path(path)
    name = '.yaoyin-config-' + secrets.token_hex(16) + '.tmp'
    if os.name == 'nt':
        _, con, files, _, _, _ = _win()
        temporary = path.with_name(name)
        with _windows_parent(path.parent, create=True):
            before = _windows_file(path)
            handle = None
            created = False
            try:
                handle = files.CreateFile(str(temporary), con.GENERIC_WRITE | con.READ_CONTROL,
                    0, _windows_attributes(False), con.CREATE_NEW, con.FILE_ATTRIBUTE_NORMAL, None)
                created = True
                _windows_private(handle, False)
                _, written = files.WriteFile(handle, data)
                if written != len(data):
                    raise OSError('配置写入不完整')
                files.FlushFileBuffers(handle)
                handle.Close()
                handle = None
                if _windows_file(path) != before:
                    raise RuntimeError('配置同时发生变化，未覆盖')
                flags = files.MOVEFILE_WRITE_THROUGH | (con.MOVEFILE_REPLACE_EXISTING if before else 0)
                files.MoveFileEx(str(temporary), str(path), flags)
            finally:
                if handle is not None:
                    handle.Close()
                if created and temporary.exists():
                    temporary.unlink()
    else:
        with _unix_parent(path.parent, create=True) as directory:
            before = _unix_file(directory, path.name)
            created = False
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
                created = True
                with os.fdopen(fd, 'wb') as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                if _unix_file(directory, path.name) != before:
                    raise RuntimeError('配置同时发生变化，未覆盖')
                if before is None:
                    os.link(name, path.name, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                else:
                    os.replace(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                if created:
                    try:
                        os.unlink(name, dir_fd=directory)
                    except FileNotFoundError:
                        pass
