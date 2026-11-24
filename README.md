# cortex-mcp · 记忆皮层

[![CI](https://github.com/HLX-CPU/cortex-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/HLX-CPU/cortex-mcp/actions/workflows/ci.yml)

**一个完全存储于电脑本地、账户无关的"大脑皮层"MCP 服务器。** 把你的历史对话、项目经验、个人偏好做成一个本地知识库，任何支持 MCP 的 AI 客户端（Codex、Claude Code、Claude Desktop、ZCode、Kimi、Cursor……）都能读写同一份记忆。

**A local-first, account-independent "memory cortex" MCP server.** One home directory, one SQLite index, any MCP client. Nothing leaves your computer.

## 特性 / Features

- **零依赖**：仅需 Python 3.9+ 标准库，单文件服务器（`cortex_mcp.py`），无需安装任何包。
- **完全本地**：数据全部保存在一个 home 目录（SQLite + Markdown + JSON）；不联网、不需要任何账户或 API 密钥、不上传。
- **可溯源**：每条记忆保留来源路径、内容哈希、观测时间、权威级别（用户明确 > 历史摘要 > 模型自述）。
- **历史是证据不是指令**：检索结果永远附带提示，防止旧记录被当成新授权；模型写入的记忆标记为 `agent_recorded`，不会自动升级为用户规则。
- **自动脱敏**：写入时自动遮蔽 API key、token、JWT、私钥、带密码的 URL 等敏感串。
- **诚实的检索**：中文双字 bigram + 关键词全文检索（SQLite FTS5），不假装是向量语义模型，不需要下载 embedding。
- **版本保留**：来源文件更新只追加新版本，旧版本永远保留，可回溯。
- **多客户端共享**：一条命令把服务器注册进 Codex / Claude / ZCode / Kimi 等客户端，所有客户端共享同一份记忆。

## 快速开始 / Quick start

```bash
# 1) 自检（在临时目录里做完整协议测试，不碰真实数据）
python verify.py

# 2) 初始化自己的记忆库（默认 home：~/.cortex-mcp）
mkdir -p ~/.cortex-mcp/knowledge
printf '# 我的偏好\n- 回答尽量简洁\n' > ~/.cortex-mcp/knowledge/profile.md

# 3) 导入已有资料（.md/.txt/.jsonl 目录，只读源文件）
python cortex_mcp.py import D:/我的笔记

# 4) 注册进本机检测到的所有 AI 客户端
python connect.py --all-found

# 5) 打开本地仪表盘（可选）
python dashboard.py
```

环境变量 `CORTEX_HOME` 或参数 `--home` 可把 home 目录放到任意位置（比如放到非系统盘、或指向一个已有的记忆库）。

## 接入各客户端 / Connect clients

`connect.py` 支持的客户端与配置位置（全部是**增量、幂等**编辑，保留原有配置）：

| 客户端 | 配置位置 | 方式 |
|---|---|---|
| Codex CLI | `~/.codex/config.toml`（含 `CODEX_HOME`、可加 `--codex-home`） | TOML 追加 `[mcp_servers."memory-cortex"]` |
| Claude Code | 用户作用域 | 优先 `claude mcp add -s user`，失败则改 `~/.claude.json` |
| Claude Desktop | `claude_desktop_config.json` | JSON `mcpServers` |
| ZCode | `~/.zcode/cli/config.json` | JSON `mcp.servers`（严格 schema） |
| Kimi Code CLI | `~/.kimi-code/mcp.json`（含 `KIMI_CODE_HOME`） | JSON `mcpServers` |
| DeepSeek Harness | 桌面 profile 的 `cordis.patch.yml` | 手动加 `insert` 条目（`@deepseek-ai/dsh-mcp-client`，stdio） |
| 任意其他 | 自定 | `--client generic-json --path <file> --key mcpServers` |

```bash
python connect.py --list                                   # 看检测到哪些客户端
python connect.py --client zcode --client claude-code      # 指定客户端
python connect.py --all-found                              # 全部接入
python connect.py --client zcode --remove                  # 卸载（只删自己的条目）
```

不想用脚本也可以手动配置，本质就是让客户端以 stdio 启动：

```json
{ "command": "python", "args": ["-X", "utf8", "cortex_mcp.py", "--home", "D:/cortex-home", "mcp"] }
```

DeepSeek Harness 走它自己的插件配置（profile 根目录的 `cordis.patch.yml`，追加后重启或热加载即可）：

```yaml
- insert:
    - id: mcp-memory-cortex
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: memory_cortex
        transport: stdio
        command: python
        args: ["-X", "utf8", "cortex_mcp.py", "--home", "D:/cortex-home", "mcp"]
        toolCallTimeoutMs: 300000
```

## MCP 工具 / Tools

| 工具 | 作用 |
|---|---|
| `memory_status` | 记忆库真实覆盖范围、主题分布、最近同步 |
| `memory_profile` | 任务开始时读取：偏好文件、交接文件、有来源的历史偏好 |
| `memory_search` | 关键词检索（支持 topic/kind/时间过滤），返回来源与哈希 |
| `memory_read` | 按编号分段读完整记忆 |
| `memory_sessions` | 按时间列出历史会话入口 |
| `memory_related` | 按同会话/同工作目录找关联记录 |
| `memory_remember` | 追加新经验/决定（note/preference/decision/lesson），不覆盖旧记录 |
| `memory_sync` | （配置了 `sync_command` 才出现）增量导入本机历史来源 |
| `memory_image` | 读取登记过的历史图片 |

## 配置 / Configuration

home 目录下的 `cortex.config.json`（可选，参考 `config.example.json`）：

```jsonc
{
  "language": "zh",              // 提示语言 zh / en
  "server_name": "memory-cortex",
  "tz_offset_hours": 8,
  "topics": {                    // 分类关键词，可自定义；留空则统一归为"常规"
    "机械设计": ["solidworks", "齿轮", "轴承"]
  },
  "profile_files": ["knowledge/profile.md", "knowledge/handoff.md"],
  "sync_command": ["python", "-X", "utf8", "my_ingest.py"],  // memory_sync 工具执行的命令
  "dashboard_port": 18772
}
```

## 目录结构 / Layout

```
<home>/
├─ data/memory.sqlite   # 检索索引（records/sources/FTS5/entities/media）
├─ knowledge/           # 你维护的 profile.md / handoff.md 等
├─ notes/               # memory_remember 写入的记录（JSON，只追加）
├─ backups/             # 离线备份（一致快照 + ZIP，CRC 校验）
└─ cortex.config.json   # 可选配置
```

## 命令行 / CLI

```bash
python cortex_mcp.py mcp                          # 作为 stdio MCP 服务器运行
python cortex_mcp.py status                       # 查看统计
python cortex_mcp.py search "齿轮 设计"           # 命令行检索
python cortex_mcp.py read <record_id>
python cortex_mcp.py remember "标题" "内容" --kind lesson
python cortex_mcp.py import <目录>                # 导入 .md/.txt/.jsonl
python cortex_mcp.py sync                         # 执行 sync_command
python cortex_mcp.py backup                       # 离线备份
python cortex_mcp.py selftest
```

## 安全与边界 / Security & boundaries

- 服务器只读写 home 目录；`memory_image` 只允许读取已登记且在 `archive/media/` 内的图片。
- 原始来源文件只读，永不修改；备份不含任何凭据。
- 仪表盘仅监听 `127.0.0.1`，校验 Host 与 Origin。
- 明确**不**做的事：不上传数据、不调用模型 API、不执行历史命令、不自动恢复已取消的操作、不声称向量语义检索。

## 已知限制 / Limitations

- 关键词检索 ≠ 语义检索：同义改写可能检索不到（换来的是零模型下载、零 API）。
- 中文以 bigram 索引为主；极短（单字）查询命中能力有限。
- 多进程并发读写由 SQLite WAL + busy_timeout 保护，但不适合高频写入场景。

## License

MIT — 见 [LICENSE](LICENSE)。
