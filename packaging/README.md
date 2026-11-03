# 便携包构建

使用目标平台原生 Python 3.13.17 和 `requirements-build.txt`；所有依赖版本固定。
PyInstaller 的 Tk hook 收集 Tcl/Tk，spec 显式收集 certifi 与 Windows DACL 所需
pywin32 模块。主体保留 console 模式，保证 MCP/hook 的 stdio；macOS `.app`
只是调用同目录主体的配置入口，不作为 MCP command 使用。

```sh
python -m pip install -r requirements-build.txt
python packaging/build.py --expect-target macos-arm64
python tests/smoke_packaged.py dist/yaoyin-0.2.0-macos-arm64/yaoyin --gui
```

`build.py --output 空目录` 可指定隔离输出。构建脚本拒绝覆盖已有包目录和给错误
架构贴标签。macOS/Linux 使用 tar.gz 保留执行位与 PyInstaller 的运行库符号链接；
Windows 使用 zip。完整目录必须保留在固定位置，CLI 注册保存主体的绝对路径。

Actions 对 macOS 15 arm64/Intel、Windows 2022 x64、Ubuntu 22.04 x64/arm64
各执行原生测试、打包及包内 GUI/MCP 冒烟。push、PR 和 workflow_dispatch
只产生候选 artifacts；推送匹配版本的 tag 才进入 release job。
release job 依赖全部五个 build 成功，并核对五件包名、版本及校验和；先创建 draft
和上传完整集合，上传成功后才公开。中途失败只留下 draft，不公开残缺 release。
已公开 release 不覆盖；失败的同名 draft 可在重跑时补传完整集合再公开。

构建与运行不读取用户私有配置，也没有网络 API 冒烟。workflow runner 上的 GUI
检查是控件级自动检查；系统安全提示、下载隔离标记、双击体验仍需人工桌面验收。
macOS 未签名/公证，Windows 未签名。不能将 CI 结果描述成这些步骤的验收。

规则依据（2026-11-03 核对）：

- [PyInstaller 用法与 console/add-data 选项](https://pyinstaller.org/en/stable/usage.html)
- [PyInstaller 各平台与 macOS 说明](https://pyinstaller.org/en/stable/feature-notes.html)
- [GitHub 原生 runner 标签与架构](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)
- [GitHub runner 镜像](https://github.com/actions/runner-images)
- [setup-python 固定 Python patch 版本](https://github.com/actions/setup-python/blob/main/docs/advanced-usage.md)

发布 tag 只复用同一提交已成功的 main 原生构建制品，不重新编译；找不到成功记录或五包不完整时拒绝发布。
