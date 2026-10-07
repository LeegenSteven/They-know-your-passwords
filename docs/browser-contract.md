# 浏览器与宿主动作用法（2026-10-07）

风险请求沿用 Native Messaging 关联和加密格式。Chromium 固定 ID 为 `ijlckofhohjbbifcfhpiglkmfndaaeol`。软件与 proxy 的命名管道使用 UTF-8 JSON Lines，缓存半包并拆分粘包；浏览器与 proxy 之间仍使用标准四字节长度帧。软件、proxy 必须一起更新。

## 来源权限

扩展风险面板可请求历史评估、生成、准备、确认和取消，后台检查扩展 ID 与扩展页面 URL。网页内容脚本仅可请求通用强度、输入失效、打开风险面板和候选存在布尔值；要求扩展 ID、顶层 frame、HTTP(S) URL、发送者与标签页同源。内容脚本不能获得历史、候选令牌或保存权限。填充仅提供本次候选；网站可以读取用户主动填入的值。

## 绑定与动作

公共字段为 `context`（new/change/audit，通用反馈强制 generic）、`entryUuid`、`account`、`origin`、`pageId`、整数 `inputRevision` 和 `requestID`。来源由后台根据活动 HTTP(S) 标签页生成。新建 UUID 由宿主预分配，修改只绑定原目标。宿主另外绑定数据库对象、会话、内容修订、浏览器连接；令牌随机生成，仅存内存，十分钟过期。切换账号、输入、数据库、锁库或取消使候选失效。修订覆盖条目、历史及数据库元数据。

| 动作 | 输入与结果 |
| --- | --- |
| `assess-generic-password` | candidate、来源与修订；只评估 RankGuess，网页获得 status/error_code/psm/native，无历史或口令库元数据 |
| `assess-password` | candidate 与绑定；始终评估通用强度，有有效历史时另评估 PARD，返回 trawling/reuse |
| `recommend-password` | 绑定及 constraints；返回复检结果、候选、宿主 candidateToken/expiresAt/targetUuid |
| `prepare-risk-candidate` | 手工 candidate 与绑定；完成必要评估后准备候选，不保存 |
| `confirm-risk-candidate` | 原绑定、令牌、websiteSucceeded=true；待验证候选要求 acknowledgeUnknown=true；KDBX 实际保存后 saved=true，并消费令牌 |
| `cancel-risk-candidate` | 令牌、页面及输入修订；撤销候选和旧结论 |

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
| SELECT_ACCOUNT_REQUIRED、TARGET_ENTRY_MISMATCH、TARGET_ENTRY_MISSING | 目标不明或不存在，不保存 |
| INVALID_CANDIDATE_BINDING、INVALID_NEW_TARGET、STALE_CANDIDATE_BINDING | 无效绑定，不写库 |
| CANDIDATE_EXPIRED、NO_PENDING_CANDIDATE | 重新准备 |
| CONFIRMATION_REQUIRED | 等待网站成功及待验证确认 |
| SAVE_TARGET_REQUIRED、DATABASE_SAVE_FAILED | 未保存 KDBX 路径或落盘失败，不显示成功 |
| SELECT_TARGET_FIELD、FIELD_LENGTH_CONFLICT、PAGE_FILL_FAILED | 要求选择目标框或调整网站约束 |
| SOURCE_NOT_ALLOWED、NOT_ASSOCIATED、NATIVE_REQUEST_FAILED | 来源、关联或连接不可用，结束等待 |

首次保存/更新横幅转入候选准备；生成图标打开风险面板。已准备候选不再重复弹出保存横幅。优先识别新口令/确认框；无法可靠识别时要求选框，不覆盖当前旧口令框。

风险模式启用时，旧 `set-login` 返回标准取消/拒绝错误 6，所有候选保存必须经 confirm-risk-candidate。禁用风险模块时保留原始管理器的兼容流程。
