药引 0.2.0 提供自带 Python、Tk 和 CA 证书的便携下载包。完整解压到固定位置后，
macOS 双击 `配置药引.app`，Windows 双击 `yaoyin.exe`，Linux 运行 `开始配置.sh`。
在配置窗口中填写自己的模型服务信息，选择 Codex、Claude 或两者并保存。

下载与设备匹配的架构；Apple Silicon 选择 macos-arm64，Intel Mac 选择 macos-x64。
Linux 构建基于 Ubuntu 22.04，需要系统 glibc 和图形库。各包均经过原生构建、
隔离配置的 MCP stdio 冒烟和自动 GUI 控件检查。自动检查不等于人工桌面验收。
包内不含 API key；Codex 和 Claude 继续使用各自原有官方登录。

发布资产中的 `SHA256SUMS` 可用于核对下载完整性：macOS 使用
`shasum -a 256 文件名`，Linux 使用 `sha256sum 文件名`，Windows PowerShell 使用
`Get-FileHash 文件名 -Algorithm SHA256`，并比较对应一行的哈希值。

本版本没有经过开发者签名或公证；系统可能提示未知发布者或开发者。
完整操作说明和许可证随每个下载包附带。
