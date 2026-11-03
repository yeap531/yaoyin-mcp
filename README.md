# 药引 MCP

让自有模型先处理任务，再由 Codex 或 Claude Code 在原会话中继续完成。本地 `stdio MCP`，不代理官方模型流量，不替换官方登录。

隐私入口使用提交前 hook：在客户端只发送不含任务的 `$yaoyin` 或 `/yaoyin`，真实任务在本地多行输入框填写。任务与模型成果在本地提交满 300 秒后，才作为 hook 上下文一次性加入原会话。不要把真实任务写在启动命令后面。

```text
原生会话只发送空命令
          ↓ UserPromptSubmit hook
药引本地输入框：填写真实任务
          ↓ 本地 stdio MCP → 本机代理 + HTTPS → 自有模型 API
任务与回答保留在本地，满 300 秒才交接
          ↓ hook additionalContext：原任务 + 完整文本回答
Codex / Claude Code 在原会话中继续执行和验证
```

## 下载使用

从 [GitHub Releases](https://github.com/yeap531/yaoyin-mcp/releases) 下载与系统和架构匹配的包，核对同一版本的 `SHA256SUMS` 后完整解压到长期保留的位置，不要只移动其中的可执行文件。

| 系统 | 下载包后缀 | 配置入口 |
| --- | --- | --- |
| macOS Apple Silicon | `macos-arm64.tar.gz` | 双击 `配置药引.app` 或 `开始配置.command` |
| macOS Intel | `macos-x64.tar.gz` | 双击 `配置药引.app` 或 `开始配置.command` |
| Windows 64 位 | `windows-x64.zip` | 双击 `yaoyin.exe` |
| Linux x64 | `linux-x64.tar.gz` | 桌面运行 `开始配置.sh`，或执行 `./yaoyin setup` |
| Linux arm64 | `linux-arm64.tar.gz` | 同上 |

首次窗口填写自己的 HTTPS API endpoint、模型 ID、API key 和本机 HTTP 代理，选择 Codex、Claude Code 或两端，再点击保存并安装。下载包不含作者的测试 API 或密钥，不需要管理员权限，也不需要安装 Python。配置后在客户端新会话完成原生 hook 信任流程。

这些包没有开发者签名或公证，macOS 和 Windows 可能显示未知发布者提示。请核对来源与校验值后通过系统正常流程打开，不要关闭系统安全功能。macOS 在 15 对应架构、Windows 在 Server 2022 x64、Linux 在 Ubuntu 22.04 对应架构的原生 runner 构建与自动验证；其他系统版本的人工桌面验收须分别确认。Linux 需要兼容的 glibc（至少 2.35）及桌面图形库。

程序注册的是解压目录的绝对路径，请保留整个目录。已有其他同名 MCP / skill 或经修改的药引配置时会停止，不强行覆盖；从旧源码版迁移时先按文末步骤移除旧药引条目，再从下载版安装。它不会卸载官方客户端或更改官方登录。

## 环境与边界

下载版支持 macOS Apple Silicon / Intel、Windows x64、Linux x64 / arm64，自带 Python、Tk 本地界面和 CA 证书。Claude Code 至少需要 2.1.295，Codex 需要支持并启用原生 hooks；官方 CLI 须事先安装并登录。Linux 需要桌面图形环境，SSH 无图形环境不能弹出输入框。

外部模型只生成文本，不具备原客户端的文件和命令工具。一次 API 请求最多 300 秒；提前返回时只在本地等待交接点，不重复调用或添加提示词，也不声称模型持续执行了 5 分钟。上游超时、空回答、取消或交接超限时不提交任务和成果。

交接包含本地输入原文和 API 返回的完整 `content`，不导出隐藏推理或原生内部模型状态。最大任务 64 KB，交接内容最大 256 KB，超过限制直接阻止，不静默截断。Claude Code 会把超过 10,000 字符的 hook 内容保存为原生输出文件并提供预览；skill 要求先沿客户端明确提供的本轮文件路径完整读取，再继续任务。该文件只会在交接点之后产生，不通过搜索临时目录恢复任务。

## 配置 API

将 `config.example.json` 的字段填入 `~/.config/yaoyin-mcp/config.json`。真实配置放在源码目录之外。macOS/Linux 目录权限为 `700`、文件为 `600`；Windows 保存到 `%LOCALAPPDATA%\YaoyinMCP\config.json`，使用仅当前用户与 SYSTEM 可访问的受保护 DACL。已有不安全权限会拒绝读写，不静默修正。

```sh
umask 077
mkdir -p ~/.config/yaoyin-mcp
chmod 700 ~/.config/yaoyin-mcp
```

| 字段 | 要求 |
| --- | --- |
| `endpoint` | OpenAI-compatible `https://…/chat/completions` 地址，仅默认端口，不含 URL 凭据、查询参数或片段 |
| `model` | 服务提供的模型 ID |
| `api_key` | 该服务专用密钥，仅保存在私有配置中 |
| `proxy` | 本机 `http://127.0.0.1:端口` 代理，必须明确配置 |

服务每次调用时读取配置，不改官方登录。

## 从源码安装

下载版直接使用配置界面。开发者从源码运行需要 Python 3.9+；本地界面需要可用的 Tk，Windows 还需 pywin32。macOS 系统旧 Python 的 hook 保留系统对话框入口。原生构建步骤见 [packaging/README.md](packaging/README.md)。在保留的源码目录执行：

```sh
python3 install.py --client both
```

也可使用 `--client codex` 或 `--client claude`。安装器注册用户级 `yaoyin` MCP，安装本地输入 hook 和配套 skill；保留已有其他配置、工具审批和官方登录。同名配置有冲突时停止，不覆盖。

已经装好 MCP 时只更新入口和 skill：

```sh
python3 install.py --client both --entry-only
```

`--skills-only` 只安装 skill 链接，不安装 hook，不能单独启用隐私入口。

两端共用仓库的 `skills/yaoyin/SKILL.md`，安装后请保留程序目录。源码版在 Unix 使用链接，下载版和 Windows 复制技能文件并用摘要记录本工具管理的文件；用户改动过的同名技能不覆盖。Codex 安装到 `~/.codex/skills/yaoyin`（自定义 `CODEX_HOME` 时随之变化）和 `~/.agents/skills/yaoyin`；Claude Code 安装到 `~/.claude/skills/yaoyin`。

Codex 的入口写入 `~/.codex/hooks.json`；Claude Code 写入 `~/.claude/settings.json` 的 `hooks.UserPromptSubmit`。单 hook 超时为 460 秒，留出本地输入的 120 秒和药引阶段的 300 秒。Claude Code 使用 `onFailure: block`；Codex 保留原来的 hooks 功能开关，不擅自启用。

安装后在新会话加载并完成客户端自己的 hook 信任确认；不需要退出官方账号。当前已打开的会话能否动态加载取决于客户端版本。安装器不会关闭或重启应用。

## 使用

只发送空命令：

```text
Codex：$yaoyin
Claude Code：/yaoyin
```

在随后弹出的本地输入框填写真实任务并提交。从本地提交时开始计时，满 5 分钟后原会话收到任务和回答，skill 继续执行并验证。输入框 2 分钟未提交会取消，可用空命令重试。多行任务可以粘贴到输入框。

在药引阶段取消时，不交接尚未发布的任务和成果。没有本轮交接时，skill 不从历史或文件猜任务，不补调 API，不伪造成功。普通消息不触发药引，skill 关闭 Codex 的隐式调用。

## 隐私保证的范围

该入口保护的是在药引本地输入框中新填写的任务及其回答：交接前不写入 hook 的 stdout、stderr、工作目录或日志；MCP 只通过匿名管道接收任务。它不自动读取历史、项目、附件、官方凭据或钥匙串。

这个范围有明确前提：真实任务没有提前出现在原生聊天、历史、附件或自动上下文里。已有会话仍由客户端正常维护，hook 无法撤回已经发送的内容，也不阻止客户端其他联网。

Codex 0.162.1 的 hook 在崩溃或超时后可能继续发送原生提交，因此绝不能用带正文的 `$yaoyin 任务` 充当隐私输入。空命令方案在这种失败下只暴露启动命令；正文尚未进入提交链路。健康的 hook 会拒绝带正文命令，但这只是防误用，不能替代上述边界。Claude Code 的 `onFailure: block` 会在启动失败或超时时阻止提交。

- 请求只连接私有配置里的一个固定 HTTPS 端点，工具参数不能覆盖地址、模型、请求头或密钥。
- 外部 API 必须经过配置的回环代理，禁用 `NO_PROXY` 绕过，不直连、不重定向、不使用备用服务，保留 TLS 校验。
- MCP 不监听端口，官方模型请求不经过本服务。MCP 子进程不继承客户端认证环境。
- API 由进程内 HTTPS 客户端调用，密钥不出现在命令参数或子进程环境中。只通过明确配置的 HTTP CONNECT 代理建立目标 TLS 隧道，上游错误正文不返回客户端。包含服务自身密钥的输入或回答会被阻止。
- API 请求正文只保留在内存中，不再生成请求正文临时文件，不写调用日志。配置原子保存的临时文件在创建时即具有私有权限，正常完成或失败时清理。

操作系统被入侵、同用户恶意进程、进程内存读取和上游数据处理不在本工具可控制范围内，不承诺绝对零泄漏。代理不可用时直接失败，不要关闭 TLS 校验。

## 基础 MCP 接口

手动调用 `ask_model` 的参数：

```json
{"messages": [{"role": "user", "content": "任务原文"}], "max_output_tokens": 4096}
```

`role` 仅支持 `user` / `assistant`，输出上限可选 64 至 8192。服务原样发送消息，不添加 system 或 user 指令。手动调用会先经过主模型，不属于隐私入口。

普通 MCP 注册保留 120 秒 API 上限及客户端 130 秒工具超时。入口启动独立本地 MCP，使用 `server.py --handoff` 将 API 上限设为 300 秒，不修改普通工具超时。

## 检查与移除

无需真实凭据、无需外网的回归检查：

```sh
python3 -B -m unittest discover -s tests -v
```

移除连接不影响官方登录：

```sh
codex mcp remove yaoyin
claude mcp remove --scope user yaoyin
```

移除入口时，只从各端 `hooks.UserPromptSubmit` 删除命令指向本仓库 `entry_hook.py` 的条目，保留其他 hooks。源码版移除 skill 时只删除发现目录的 `yaoyin` 符号链接；下载版只移除对应 `yaoyin` 技能目录中本工具创建的文件，保留用户添加的内容。移除 MCP 不会自动移除 hook 或 skill。

原生接入依据：[Codex MCP](https://developers.openai.com/codex/mcp)、[Claude Code MCP](https://code.claude.com/docs/en/mcp)、[Codex hooks](https://learn.chatgpt.com/docs/hooks)、[Claude Code hooks](https://code.claude.com/docs/en/hooks)。采用 MIT License。
