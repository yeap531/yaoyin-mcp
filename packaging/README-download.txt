药引 @VERSION@ · @PLATFORM@ @ARCH@

本下载包自带 Python、Tk 配置窗口和 CA 证书，无需安装 Python 或 pip。
完整解压后，把整个文件夹放到长期保留的位置，不能只移动 yaoyin 可执行文件。

打开配置窗口：
  macOS：双击“配置药引.app”。也可双击“开始配置.command”。
  Windows：双击 yaoyin.exe。
  Linux 桌面：双击“开始配置.sh”，按文件管理器提示选择运行。
             若文件管理器不允许运行，终端执行 ./yaoyin setup。

填写自己的 API endpoint、模型、API key 和本机 HTTP 代理，选择 Codex、Claude
或两者后保存。密钥保存在当前用户的私有配置文件中，下载包没有内置 API 密钥。
Codex 和 Claude 使用各自原有官方登录；药引不会替代、复制或修改这些登录凭据。
完成后重启相应 CLI 会话，让新增的 MCP 和 hook 配置生效。

这是未经过开发者签名/公证的社区下载包，系统可能提示未知开发者或发布者。
请先核对来源和 SHA256SUMS，再使用操作系统提供的“仍要打开”选项。
macOS 可在“系统设置 → 隐私与安全性”中处理下载应用的安全提示。
Linux 包依赖系统 glibc 和图形库；在 Ubuntu 22.04 原生构建和验证。
macOS 包在 macOS 15 对应架构构建和验证；Windows 包在 Windows 2022 x64 验证。

命令行（Windows 使用 yaoyin.exe）：
  ./yaoyin --version
  ./yaoyin setup
  ./yaoyin self-test --gui
  ./yaoyin mcp
  ./yaoyin hook --client codex
  ./yaoyin install --client both

MCP 和 hook 继续使用标准输入输出；不要使用 .app 或配置脚本作为 MCP 命令。
安装后若移动或删除此文件夹，请从新位置重新运行配置或 install。
配置位置：macOS/Linux ~/.config/yaoyin-mcp/config.json；
          Windows %LOCALAPPDATA%\YaoyinMCP\config.json。
项目与第三方许可证见 LICENSE、LICENSES 和随运行库携带的许可证文件。
