# 药引 MCP

让自有模型先处理任务，Codex 或 Claude Code 在原会话中继续完成。本地 `stdio MCP`，不代理官方模型流量，不替换客户端的登录方式。

提供配套 `yaoyin` skill：调用后先让自有模型处理任务，再由原客户端检查结果、继续执行并验证。也可单独手动调用 MCP。Skill 不强制接管所有消息，不包含精确 5 分钟计时或 `/model` 切换。外部模型只生成文本结果；读写代码、运行命令仍由原客户端处理。

```text
Codex / Claude Code 原会话
          ↓ 显式工具参数
     本地药引 MCP
          ↓ 本机代理 + HTTPS
     你配置的模型 API
          ↓ 原生工具结果
Codex / Claude Code 在原会话中继续
```

## 环境

macOS 或 Linux，Python 3.9+，`/usr/bin/curl`，以及需要接入的官方 `codex` 或 `claude` CLI。服务只使用 Python 标准库，无需安装 Python 依赖。Windows 尚未适配。

## 配置 API

将 `config.example.json` 的字段填入 `~/.config/yaoyin-mcp/config.json`。真实配置放在源码目录之外，目录权限须为 `700`，文件权限须为 `600`。已经有配置时保留原文件。

```sh
umask 077
mkdir -p ~/.config/yaoyin-mcp
chmod 700 ~/.config/yaoyin-mcp
```

配置字段：

| 字段 | 要求 |
| --- | --- |
| `endpoint` | OpenAI-compatible `https://…/chat/completions` 地址，仅 HTTPS 默认端口，不含 URL 凭据、查询参数或片段 |
| `model` | 服务提供的模型 ID |
| `api_key` | 该服务专用密钥，仅保存在私有配置中 |
| `proxy` | 本机 `http://127.0.0.1:端口` 代理，必须明确配置 |

服务在启动后的每次调用中读取这份配置。修改配置不用更换官方登录。

## 接入 Codex 和 Claude Code

在保留的源码目录执行：

```sh
python3 install.py --client both
```

也可分别使用 `--client codex` 或 `--client claude`。安装器使用客户端原生 CLI 注册名为 `yaoyin` 的用户级 MCP，并安装配套 skill。执行入口是当前 Python 和本目录的 `server.py`，skill 目录链接到本仓库的 `skills/yaoyin`，因此安装后请保留源码目录。它不安装全局软件，不改模型供应商、官方登录或工具审批规则；遇到已有的不同同名 MCP 或 skill 配置会停止。

已经接好 MCP 时，只补装 skill：

```sh
python3 install.py --client both --skills-only
```

两边的单工具超时均设为 130 秒，略长于服务内部的 120 秒 API 请求限制。Codex 的这项设置只写入 `[mcp_servers.yaoyin]`；Claude Code 使用该服务独立的 `timeout` 字段。

验证配置：

```sh
codex mcp get yaoyin --json
claude mcp get yaoyin
```

在客户端新会话中用 `/mcp` 检查连接，然后调用工作流 skill：

```text
Codex：$yaoyin 帮我完成这个任务……
Claude Code：/yaoyin 帮我完成这个任务……
```

两端共用一份 `skills/yaoyin/SKILL.md`。Codex 同时链接到应用的技能目录 `~/.codex/skills/yaoyin`（自定义 `CODEX_HOME` 时随之变化）和标准发现目录 `~/.agents/skills/yaoyin`；Claude Code 使用 `~/.claude/skills/yaoyin`。这些入口均指向同一份源文件，不维护多份不同的流程。

Skill 先原样发送任务与必要上下文，不把 skill 的工作流指令拼到外部模型输入中；取得结果后留在当前原生会话里执行和验证。它可被客户端根据描述自动选择，但 skill 不能保证每条普通消息都强制触发。工具不可用或 API 失败时会明确报告，不改用其他 API 或伪造阶段成果。

已有会话是否能动态加载取决于客户端版本；无需登出官方账号。首次调用是否要求确认，遵循客户端原来的权限设置。

## 工具接口与原生上下文

`ask_model` 接受：

```json
{
  "messages": [{"role": "user", "content": "任务原文"}],
  "max_output_tokens": 4096
}
```

`role` 只允许 `user` 或 `assistant`，`content` 为文本，输出上限可选 64 至 8192。服务原样发送这些消息，不添加 system 或 user 指令，不生成额外交接提示词。成功后文本与模型 ID 作为 MCP 工具结果返回，主会话仍由客户端原生维护。

MCP 不会自动获得完整会话或内部模型状态。要交给外部模型的任务和上下文须通过工具参数明确传入；服务不读取项目、会话记录、官方凭据或钥匙串，也不运行用户命令。

## 数据与安全边界

- 只连接本机私有配置里的单一 HTTPS 端点。工具调用者不能覆盖地址、模型、请求头或密钥。
- 外部请求必须经过配置的回环代理；禁用 `NO_PROXY` 绕过，不直连、不跟随重定向、不切换备用服务，保留 TLS 证书校验。
- 不监听网络端口；Codex / Claude 的官方请求不经过本服务。
- 密钥经 curl 标准输入传递，不放入命令参数或子进程环境。不会把上游错误正文回传给客户端。
- 请求正文仅在私有配置目录中的 `600` 临时文件里短暂保留，调用结束或正常取消时删除；不写调用日志。输入或回答包含服务自身密钥时会阻止传递。
- 只有显式工具参数会发送到配置的 API。不要传入不愿交给该服务的内容。操作系统被入侵、同用户恶意进程、强制终止残留和上游数据处理仍有风险，不承诺绝对零泄漏。

如果本机代理未运行，调用会直接失败。请自行恢复代理，不要通过关闭 TLS 校验解决连接问题。

## 检查与移除

无需真实凭据、无需外网的安全回归检查：

```sh
python3 -B -m unittest discover -s tests -v
```

移除客户端连接不会更改官方登录：

```sh
codex mcp remove yaoyin
claude mcp remove --scope user yaoyin
```

如需移除 skill，只删除对应发现目录中的 `yaoyin` 符号链接，保留仓库中的源文件。仅移除 MCP 连接不会自动移除 skill。

原生接入依据：[Codex MCP 文档](https://developers.openai.com/codex/mcp)、[Claude Code MCP 文档](https://code.claude.com/docs/en/mcp)、[Codex skills 文档](https://developers.openai.com/codex/skills)、[Claude Code skills 文档](https://code.claude.com/docs/en/skills)。

采用 MIT License。
