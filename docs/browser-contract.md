# 浏览器与宿主动作用法（2026-10-22）

风险请求沿用 Native Messaging 关联和加密格式。Chromium 固定 ID 为 `ijlckofhohjbbifcfhpiglkmfndaaeol`。软件与 proxy 的命名管道使用 UTF-8 JSON Lines，缓存半包并拆分粘包；浏览器与 proxy 之间仍使用标准四字节长度帧。软件、proxy 必须一起更新。

## 来源权限

扩展风险面板可请求历史评估、生成、准备、确认和取消，后台检查扩展 ID 与扩展页面 URL。网页内容脚本仅可请求通用强度、输入失效、打开风险面板和候选存在布尔值；要求扩展 ID、顶层 frame、HTTP(S) URL、发送者与标签页同源。内容脚本不能获得历史、候选令牌或保存权限。填充仅提供本次候选；网站可以读取用户主动填入的值。

## 绑定与动作

公共字段为 `context`（new/change/audit，通用反馈强制 generic）、`entryUuid`、`account`、`origin`、`pageId`、整数 `inputRevision` 和 `requestID`。来源由后台根据活动 HTTP(S) 标签页生成。新建 UUID 由宿主预分配，修改只绑定原目标。宿主另外绑定数据库对象、会话、内容修订、浏览器连接；令牌随机生成，仅存内存，十分钟过期。切换账号、输入、数据库、锁库或取消使候选失效。修订覆盖条目、历史及数据库元数据。

### 文档绑定与填充

每个顶层内容文档在扩展隔离环境中生成随机 `documentToken`。后台通过 `risk_document_context` 取得实际文档的来源与令牌，消息固定投递到 `frameId: 0`；浏览器提供 `webNavigation` 文档 ID 时，进一步指定 `documentId`。后台校验返回来源与活动 HTTP(S) 标签页同源，候选记录同时绑定该文档令牌。此令牌用于区分文档实例，不是候选保存令牌，也不授予网页保存权限。

`recommend-password` 和 `prepare-risk-candidate` 在请求前记录标签页请求序号，响应时重新检查序号、输入修订、数据库身份、解锁与关联状态、活动标签页来源及文档令牌。导航、断连、锁库和解锁通知会使旧请求失效；即使两次请求使用同一 `inputRevision`，也只接受当前序号对应的响应。被丢弃响应若已带有宿主候选令牌，后台按该响应的原页面与来源精确回收。

填充前，后台再次检查候选与当前文档，并发送 `expectedOrigin` 和 `documentToken`。内容脚本只在顶层文档接受本扩展消息；在写入 DOM 前立即核对当前 `window.location.origin` 与本内容文档的令牌，核对与填充之间没有 `await`。浏览器文档 ID 定向与内容文档内校验共同覆盖“后台检查完成后才发生导航”的投递竞争。跨源或非 HTTP(S) 导航撤销候选；同源的网站成功跳转可保留待确认候选，但新文档不能再次填入旧文档的候选。确认保存仍要求原账号、原目标与宿主绑定有效，并由用户确认网站已成功。

### 撤销与异步请求作用域

宿主 `cancel-risk-candidate` 必须匹配浏览器连接、`pageId` 和 `origin`；提供 `candidateToken` 时再匹配该精确令牌。未提供令牌只撤销该连接下该页面、该来源的候选，不扩展为整条连接的撤销。缺少页面或来源的请求不执行撤销。扩展当前没有待处理候选令牌时，只清除本地状态并推进请求序号，不发送取消 RPC；有令牌时使用候选保存的原页面与来源，而不是导航后的 URL。

常规取消默认使该页面、该来源的在途请求失效。回收迟到响应使用精确令牌并设置 `invalidateRequests: false`，只删除那个旧候选，不更新宿主输入修订，也不使较新的请求 generation 失效；此模式缺少令牌时不执行撤销。宿主为每个连接、页面登记递增请求 generation 及来源，异步评估完成后在 `prepare` 替换候选之前验证它们，旧 generation 返回 `STALE_RISK_REQUEST`。连接断开调用 `clearConnection`，清理该连接的候选、在途请求登记与输入修订；断连、锁库或解锁通知同时清除扩展端候选并推进本地修订和请求序号，避免同库重连后恢复旧候选。

| 动作 | 输入与结果 |
| --- | --- |
| `assess-generic-password` | candidate、来源与修订；只评估 RankGuess，网页获得 status/error_code/psm/native，无历史或口令库元数据 |
| `assess-password` | candidate 与绑定；始终评估通用强度，有有效历史时另评估 PARD，返回 trawling/reuse |
| `recommend-password` | 绑定及 constraints；返回复检结果、候选、宿主 candidateToken/expiresAt/targetUuid |
| `prepare-risk-candidate` | 手工 candidate 与绑定；完成必要评估后准备候选，不保存 |
| `confirm-risk-candidate` | 原绑定、令牌、websiteSucceeded=true；待验证候选要求 acknowledgeUnknown=true；KDBX 实际保存后 saved=true，并消费令牌 |
| `cancel-risk-candidate` | 页面、来源、输入修订及可选精确令牌；默认撤销该页面候选并使在途请求失效；旧响应回收使用精确令牌及 `invalidateRequests=false` |

网站 `constraints`：minLength/maxLength/length（算法域 5–20，默认生成 20 位），lower/upper/digits/symbols（默认全部启用），forbidden，以及 budgetMs（默认 3000，显式范围 100–120000）。启用类别每类至少一个字符，禁用类别与禁用字符不生成。冲突返回 CONSTRAINT_CONFLICT。最多二十次尝试，生成与必要复检共享预算，从模型就绪后计时。

历史去重最多五条。修改先纳入当前旧口令，再取最近的有效条目和快照；审计排除自身及自身全部快照。排除回收站、过期项、引用值、越域值。新建不要求已有条目；修改目标消失拒绝保存，不退回新增。确认不重新读取当前选中账号。

## 展示与等待

PSM 六档为 `[1,10³)`、`[10³,10⁶)`、`[10⁶,10⁹)`、`[10⁹,10¹²)`、`[10¹²,10¹⁵)`、`[10¹⁵,∞)`。未标定显示“估计区间、待验证”；截断仅显示下界，band=null。PARD 独立展示精确复用、排名、beam/top-k、历史使用数量。精确复用为 HIGH/EXACT_REUSE；命中待验证，未命中未知，两类数值不合并。

当前没有匹配的冻结阈值，候选返回 REVIEW_REQUIRED，不返回 QUALIFIED；宿主猜测次数筛选只用于随机候选筛选，不是已标定风险等级。网页与桌面输入防抖 500 ms，变化立即撤销旧结论，模型调用异步。冷启动上限 180 秒，普通评估共享 3 秒，推荐只按明确设置延长。超时重启子进程。

## 错误状态

| 错误码 | 行为 |
| --- | --- |
| CONSTRAINT_CONFLICT、UNUSABLE_HISTORY、OUT_OF_DOMAIN | 必要评估无法完成，不准备候选 |
| GENERATION_BUDGET_EXHAUSTED、HOST_TIMEOUT、TIMEOUT | 超时未知，可重新请求 |
| COLD_START_TIMEOUT、MODEL_LOAD_FAILED、PROCESS_EXITED、CONFIGURATION_MISSING | 服务不可用，结束等待 |
| STALE_INPUT_REVISION、STALE_VAULT_REVISION、STALE_DATABASE_SESSION | 拒绝旧结果或候选 |
| STALE_DOCUMENT_CONTEXT | 来源、文档令牌或请求上下文已变化，不接收旧候选或执行填充 |
| STALE_RISK_REQUEST | 宿主在途请求 generation 已失效，不创建或替换候选 |
| SELECT_ACCOUNT_REQUIRED、TARGET_ENTRY_MISMATCH、TARGET_ENTRY_MISSING | 目标不明或不存在，不保存 |
| INVALID_CANDIDATE_BINDING、INVALID_NEW_TARGET、STALE_CANDIDATE_BINDING | 无效绑定，不写库 |
| CANDIDATE_EXPIRED、NO_PENDING_CANDIDATE | 重新准备 |
| CONFIRMATION_REQUIRED | 等待网站成功及待验证确认 |
| SAVE_TARGET_REQUIRED、DATABASE_SAVE_FAILED | 未保存 KDBX 路径或落盘失败，不显示成功 |
| SELECT_TARGET_FIELD、FIELD_LENGTH_CONFLICT、PAGE_FILL_FAILED | 要求选择目标框或调整网站约束 |
| SOURCE_NOT_ALLOWED、NOT_ASSOCIATED、NATIVE_REQUEST_FAILED | 来源、关联或连接不可用，结束等待 |

首次保存/更新横幅转入候选准备；生成图标打开风险面板。已准备候选不再重复弹出保存横幅。优先识别新口令/确认框；无法可靠识别时要求选框，不覆盖当前旧口令框。

风险模式启用时，旧 `set-login` 返回标准取消/拒绝错误 6，所有候选保存必须经 confirm-risk-candidate。禁用风险模块时保留原始管理器的兼容流程。
