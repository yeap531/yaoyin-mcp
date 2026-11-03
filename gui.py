"""本地 Tk 窗口；用户文本和私有配置不写入诊断输出。"""
import contextlib
import os
import tkinter as tk
from tkinter import ttk


MAX_TASK_BYTES = 64 * 1024


def _root(title):
    try:
        root = tk.Tk()
    except Exception:
        raise RuntimeError('无法打开本地输入窗口，请检查 Tk 和桌面会话') from None
    root.title(title)
    root.minsize(540, 340)
    root.columnconfigure(0, weight=1)
    # Tk 默认回调错误会打印 traceback；持有任务和密钥的窗口禁止这样输出。
    root.report_callback_exception = lambda *_: None
    return root


class _TaskDialog:
    def __init__(self, timeout):
        self.root = _root('药引：输入任务')
        self.root.geometry('680x440')
        self.root.rowconfigure(1, weight=1)
        self.result = None
        self.reason = '已取消输入任务'
        ttk.Label(self.root, text='输入交给药引模型处理的任务（最多 64 KB）').grid(
            row=0, column=0, sticky='w', padx=18, pady=(18, 8))
        frame = ttk.Frame(self.root)
        frame.grid(row=1, column=0, sticky='nsew', padx=18)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.text = tk.Text(frame, wrap='word', undo=False, width=65, height=12)
        self.text.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scrollbar.set)
        self.status = tk.StringVar(value='保留原文与换行；窗口超时后自动取消。')
        ttk.Label(self.root, textvariable=self.status).grid(
            row=2, column=0, sticky='w', padx=18, pady=8)
        buttons = ttk.Frame(self.root)
        buttons.grid(row=3, column=0, sticky='e', padx=18, pady=(0, 18))
        ttk.Button(buttons, text='取消', command=self.cancel).pack(side='left', padx=8)
        ttk.Button(buttons, text='提交任务', command=self.submit).pack(side='left')
        self.root.protocol('WM_DELETE_WINDOW', self.cancel)
        self.root.bind('<Escape>', lambda _: self.cancel())
        self.root.bind('<Control-Return>', lambda _: self.submit())
        self.root.bind('<Command-Return>', lambda _: self.submit())
        self.timer = self.root.after(max(1, int(timeout * 1000)), self.expire)
        self.text.focus_set()

    def submit(self):
        value = self.text.get('1.0', 'end-1c')
        if not value.strip():
            self.status.set('任务不能为空。')
            return
        try:
            size = len(value.encode('utf-8'))
        except UnicodeError:
            self.status.set('任务包含无法编码的字符。')
            return
        if size > MAX_TASK_BYTES:
            self.status.set('任务超过 64 KB，请缩短后重试。')
            return
        self.result = value
        self.close()

    def close(self):
        self.root.after_cancel(self.timer)
        self.root.destroy()

    def cancel(self):
        self.close()

    def expire(self):
        self.reason = '输入任务超时，已取消'
        self.close()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
        if self.result is None:
            raise RuntimeError(self.reason)
        return self.result


def ask_task(timeout=120):
    """返回输入原文，取消时抛出 RuntimeError。"""
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
        raise ValueError('输入窗口超时必须在 0 至 86400 秒之间')
    return _TaskDialog(timeout).run()


class _SetupDialog:
    def __init__(self):
        self.root = _root('药引：首次配置与安装')
        self.root.geometry('730x440')
        self.root.columnconfigure(1, weight=1)
        ttk.Label(self.root, text='配置自己的模型 API，并安装到所选官方客户端。').grid(
            row=0, column=0, columnspan=2, sticky='w', padx=18, pady=18)
        self.fields = {}
        definitions = (
            ('endpoint', 'API endpoint', 'https://api.example.com/v1/chat/completions'),
            ('model', '模型 ID', ''),
            ('api_key', 'API key', ''),
            ('proxy', '本机 HTTP 代理', 'http://127.0.0.1:7890'),
        )
        for row, (name, label, default) in enumerate(definitions, 1):
            ttk.Label(self.root, text=label).grid(
                row=row, column=0, sticky='w', padx=18, pady=7)
            entry = ttk.Entry(self.root, show='*' if name == 'api_key' else '')
            entry.insert(0, default)
            entry.grid(row=row, column=1, sticky='ew', padx=(0, 18), pady=7)
            self.fields[name] = entry
        ttk.Label(self.root, text='安装到').grid(row=5, column=0, sticky='w', padx=18, pady=10)
        choices = ttk.Frame(self.root)
        choices.grid(row=5, column=1, sticky='w')
        self.client = tk.StringVar(value='both')
        for label, value in (('Codex', 'codex'), ('Claude Code', 'claude'), ('两者', 'both')):
            ttk.Radiobutton(choices, text=label, value=value, variable=self.client).pack(
                side='left', padx=(0, 15))
        self.status = tk.StringVar(value='请先安装所选官方 CLI；配置验证不会调用模型 API。')
        ttk.Label(self.root, textvariable=self.status, wraplength=680).grid(
            row=6, column=0, columnspan=2, sticky='w', padx=18, pady=12)
        self.button = ttk.Button(self.root, text='保存并安装', command=self.save)
        self.button.grid(row=7, column=1, sticky='e', padx=18, pady=(0, 18))
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)
        self.root.bind('<Escape>', lambda _: self.root.destroy())

    def save(self):
        config = {name: entry.get() for name, entry in self.fields.items()}
        self.button.state(['disabled'])
        self.status.set('正在验证配置并安装……')
        self.root.update_idletasks()
        # 私有配置窗口不展示安装器的诊断输出。
        try:
            with open(os.devnull, 'w') as sink:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    import install
                    import server
                    import storage

                    server.validate_config(config)
                    storage.write_config(config)
                    install.main(['--client', self.client.get()])
        except (Exception, SystemExit):
            self.status.set('配置或安装未完成。请检查字段、本机代理地址和所选官方 CLI。')
            self.button.state(['!disabled'])
            return
        self.status.set('配置已保存，安装完成。可以关闭窗口并在所选客户端中使用药引。')
        self.fields['api_key'].delete(0, 'end')
        self.button.configure(text='完成并关闭', command=self.root.destroy)
        self.button.state(['!disabled'])

    def run(self):
        try:
            self.root.mainloop()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass


def setup():
    """打开本地配置与安装界面，不调用模型 API。"""
    _SetupDialog().run()


def smoke_test():
    """自动检查真实 Tk 控件，不读取私有文件或访问网络。"""
    value = '  药引 GUI 测试：中文、café、λ、🙂\n第二行\t保留空白  \n'
    dialog = _TaskDialog(5)

    def fill_and_submit():
        try:
            dialog.text.insert('1.0', ' \n\t')
            dialog.submit()
            if dialog.result is not None:
                raise RuntimeError('GUI 空任务验证失败')
            dialog.text.delete('1.0', 'end')
            dialog.text.insert('1.0', '界' * (MAX_TASK_BYTES // 3 + 1))
            dialog.submit()
            if dialog.result is not None:
                raise RuntimeError('GUI 任务长度验证失败')
            dialog.text.delete('1.0', 'end')
            dialog.text.insert('1.0', value)
            dialog.submit()
        except BaseException:
            dialog.cancel()

    dialog.root.after_idle(fill_and_submit)
    if dialog.run() != value:
        raise RuntimeError('GUI 原文回读验证失败')
    dialog = _SetupDialog()
    errors = []

    def check_setup():
        try:
            for name, entry in dialog.fields.items():
                entry.delete(0, 'end')
                entry.insert(0, value if name != 'api_key' else 'smoke-test-key')
                # 按原文核对配置控件，不提前归一化字符。
                expected = value if name != 'api_key' else 'smoke-test-key'
                if entry.get() != expected:
                    errors.append('GUI 配置字段回读验证失败')
            if dialog.fields['api_key'].cget('show') != '*':
                errors.append('GUI 密钥掩码验证失败')
            for client in ('codex', 'claude', 'both'):
                dialog.client.set(client)
                if dialog.client.get() != client:
                    errors.append('GUI 客户端选择验证失败')
        except BaseException:
            errors.append('GUI 配置控件验证失败')
        finally:
            dialog.root.destroy()

    dialog.root.after_idle(check_setup)
    dialog.run()
    if errors:
        raise RuntimeError(errors[0])
