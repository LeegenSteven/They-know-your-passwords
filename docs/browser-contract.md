# 浏览器与 KeePassXC 动作契约

## 传输边界

风险动作沿用 KeePassXC-Browser 的 Native Messaging、关联密钥和加密消息格式。扩展后台只接受扩展页面来源发起的风险动作；内容脚本只接收单个候选口令，不接收历史集合。宿主还要求连接已经完成数据库关联。

扩展 fork 的 Chromium ID 为 `ijlckofhohjbbifcfhpiglkmfndaaeol`，Firefox ID 为 `they-know-your-passwords@local.demo`。两者均显式写入 Native Messaging 清单白名单。

## `assess-password`

解密后的请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `candidate` | string | 仅在加密消息和本地算法管道中传递的候选口令 |
| `context` | string | `new`、`change` 或 `audit` |
| `entryUuid` | string | 修改时包含原条目；存量复检时排除自身 |
| `requestID` | string | 异步响应关联标识 |
| `inputRevision` | integer | 浏览器输入修订；旧响应由宿主和扩展共同拒绝 |
| `origin` | string | 当前活动 HTTP(S) 标签页来源，由扩展后台生成 |

响应保留算法服务的 `status`、`native`、模型身份和耗时，并增加：`level`、`calibrated`、`reason`、`route`、`historyUsed`、`historyEligible`、`historySkipped`、`historyTruncated`、`vaultRevision`、`requestID` 和 `inputRevision`。

没有历史时走 `assess_trawling`。存在历史但全部越域时返回 `OUT_OF_DOMAIN/UNUSABLE_HISTORY`。可用历史最多取最近五个，排除回收站、过期项和引用口令。`audit` 排除当前条目，`change` 保留当前条目的旧口令。

未绑定标定配置时 `calibrated=false` 且 `level=UNKNOWN`。`exact_match=true` 是确定性精确重用规则，返回 `HIGH/EXACT_REUSE`。

## `recommend-password`

请求包含 `context`、`entryUuid`、`requestID`、`inputRevision` 和 `origin`。宿主用现有 `PasswordGenerator` 生成候选，然后按上下文执行重用检查和通用拖网检查。

当前未冻结阈值时返回 `REVIEW_REQUIRED`，不会声称候选已通过标定。扩展将候选保存在当前标签页后台内存中并填入页面，不写入扩展存储。用户确认网站已经接受新口令后，扩展才调用现有 `set-login` 更新所选条目；候选十分钟过期，标签页关闭后随内存状态销毁。

确认保存时必须同时匹配候选生成时的 `inputRevision`。候选输入或业务场景在生成后发生变化时，后台拒绝旧候选并返回 `STALE_INPUT_REVISION`。

## 过期与故障

- 输入修订不一致：`UNAVAILABLE/STALE_INPUT_REVISION`。
- 评估期间口令库内容变化：`UNAVAILABLE/STALE_VAULT_REVISION`。
- 锁库、切库或会话变化：`UNAVAILABLE/STALE_DATABASE_SESSION`。
- 服务未配置或退出：`UNAVAILABLE/CONFIGURATION_MISSING` 或 `PROCESS_EXITED`。
- 宿主等待超时：`TIMEOUT/HOST_TIMEOUT`。

以上状态始终显示为未知或不可评估，不映射为低风险。
