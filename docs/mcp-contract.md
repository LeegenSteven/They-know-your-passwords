# MCP 演示服务接口契约

## 范围

`mcp-server/` 是面向 Agent 的演示适配层。它只评估调用者主动提供的虚构字符串，不读取桌面客户端、浏览器扩展或 KDBX。真实凭据评估必须继续在桌面客户端的可信边界内完成。

## 传输和进程边界

Agent 宿主通过 MCP stdio 启动 `mcp-server/server.py`。MCP 进程按需启动 `algo-service/server.py`，并通过独立的 UTF-8 JSON Lines stdin/stdout 通道调用现有服务。两层服务均不监听网络端口。

MCP 服务器使用官方 Python SDK 2.x，运行环境要求 Python 3.10+。算法子进程由 `TYP_ALGORITHM_PYTHON` 指定，默认优先使用本机既有的 PyTorch/CUDA Python 3.9 环境。

## 工具契约

### `get_security_status`

输入：`wait_for_ready=false`、`wait_timeout_ms=10000`。返回配置完整性、进程状态、算法服务状态和 `demo_only=true`。等待超时返回 `TIMEOUT/MODEL_STARTUP_TIMEOUT`，不推断风险等级。

### `list_risk_models`

输入：`wait_for_ready=false`、`wait_timeout_ms=10000`。返回算法服务提供的模型身份、能力、输入域、指标和就绪状态，不返回权重路径或文件内容。

### `assess_demo_password`

输入字段：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `candidate` | string | 1–256 字符；必须是虚构测试字符串 |
| `history` | string[] / null | 最多 5 条；必须是虚构测试字符串 |
| `mode` | `auto` / `trawling` / `reuse` | `auto` 按是否存在历史选择分支 |
| `timeout_ms` | integer | 100–120000；仅约束模型就绪后的推理 |

返回沿用算法服务的 `status`、`model_id`、`model_version`、`metric_type`、`native` 和 `elapsed_ms`，再补充 `route`、`risk_level`、`reason`、`calibrated=false` 与 `demo_only=true`。返回对象不得包含候选或历史字符串。

未冻结阈值时，普通结果固定为 `UNKNOWN/UNCALIBRATED`。只有 PARD 分支确认候选与某条合成历史完全相同时，返回 `HIGH/EXACT_REUSE`；这是一项精确事实，不代表模型阈值已标定。

## 错误和隐私

桥接错误使用稳定错误码，例如 `ALGORITHM_PYTHON_NOT_FOUND`、`ALGORITHM_START_FAILED`、`ALGORITHM_BRIDGE_TIMEOUT` 和 `INVALID_ALGORITHM_RESPONSE`。错误细节不得包含输入参数、绝对模型路径或算法进程原始异常文本。

Agent 宿主可能保存工具参数到会话或追踪系统，因此演示工具不适合真实凭据。后续安全集成版应通过桌面客户端的短期不透明句柄评估当前选中条目，只把结论返回 Agent。
