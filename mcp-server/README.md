# They know your passwords MCP Demo

这个目录提供一个本地 MCP stdio 服务器，让支持 MCP 的 Agent 调用现有 RankGuess 与 PARD 算法服务。演示版不读取 KDBX，不列出口令库条目，也不返回输入字符串。

## 安装

MCP SDK 使用独立的 Python 3.10+ 环境。模型继续运行在项目原有的 Python 3.9/CUDA 环境中。

```powershell
cd mcp-server
.\setup.ps1
```

若 Python 3.10+ 不在默认位置：

```powershell
.\setup.ps1 -PythonPath C:\Path\To\python.exe
```

## 接入 Agent

复制 `mcp-config.example.json`，把 `<PROJECT_ROOT>` 替换为仓库绝对路径，再按 MCP 宿主的配置格式填写同名的 `command`、`args` 和 `env` 字段。宿主最终执行的命令等价于：

```powershell
<PROJECT_ROOT>\mcp-server\.venv\Scripts\python.exe <PROJECT_ROOT>\mcp-server\server.py
```

MCP stdout 只传输协议消息。算法服务由 MCP 进程按需启动，通过另一组 stdin/stdout JSON Lines 通道通信，不监听网络端口。

## 工具

| 工具 | 用途 | 是否读取口令库 |
| --- | --- | --- |
| `get_security_status` | 检查配置、进程和模型预热状态 | 否 |
| `list_risk_models` | 返回模型身份、能力和就绪状态 | 否 |
| `assess_demo_password` | 评估调用者明确提供的虚构测试字符串 | 否 |

`assess_demo_password` 的 `mode` 可以是 `auto`、`trawling` 或 `reuse`。`auto` 在提供合成历史时使用 PARD，否则使用 RankGuess。阈值尚未冻结，因此一般结果保持 `risk_level=UNKNOWN`、`calibrated=false`；合成输入与合成历史完全相同时，会以独立事实返回 `HIGH/EXACT_REUSE`。

## 安全限制

- 只允许使用虚构或测试字符串，禁止提交真实口令、历史口令、主密钥、恢复密钥或个人资料。
- 工具结果不回显候选或历史字符串。
- 服务不读取 KDBX，不访问浏览器，不执行保存或填充。
- 超时、越域、模型异常和未标定结果均不会映射为低风险。
- MCP SDK、桥接层和算法服务均不应向 stdout 写诊断信息。

## 验证

测试使用协议假服务，不加载权重，也不运行测试集：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖工具发现、真实 stdio MCP 握手、算法 JSON Lines 桥接、结果不回显输入、未标定语义和精确重用语义。
