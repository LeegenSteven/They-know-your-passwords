# 本机可用化验收记录

更新时间：2026-10-07。运行包：`D:\研究生\网络安全竞赛\out\TheyKnowYourPasswords`。

## 真实闭环

安装的 Chrome 154.0.8037.95 与 Edge 154.0.4258.53 均使用实际 Release 软件、实际固定 ID 扩展、Native Messaging 加密握手和真实 RankGuess/PARD。`browser-extension/tests/real-browser-acceptance.cjs` 控制独立测试配置和本机虚构注册/登录页面，网页服务器与 stdio 算法服务分离。

两浏览器均通过：独立加密库解锁、关联握手、通用评估、弹窗生成/复检、填新口令和确认框、网站成功前条目数零、网站成功后弹窗勾选并确认、真实 KDBX 落盘、软件重启后检索和填充登录、第二账号新增、生成后切换账号拒绝保存、另一条目保持原值、PARD 改密复检、只更新原目标并落盘、锁库拒绝评估。弹窗生成样本约 0.75–0.88 秒，不作为 P50/P95 统计。

最终运行包还验证网页实际输入经 500ms 防抖得到通用待验证反馈，改密填充保留当前旧口令框。package-manifest.json 记录软件、服务、模型与配置文件哈希，可核对当前包身份，不包含个人配置或口令库。

机器可复核报告为 `out/acceptance/chrome/report.json` 与 `out/acceptance/edge/report.json`，均 complete=true。报告只包含状态、布尔值、计数、错误码和耗时，不包含候选、主口令或派生密钥。浏览器 trace/video/screenshots 均关闭。

测试辅助程序构造独立 KDBX 和测试关联，随机测试主口令经私有 stdin 交给实际软件解锁。个人口令库的 GUI 建库、主口令和正常配置中的插件加载/首次关联仍由用户完成；没有接触已有个人数据库，也没有把辅助建库声称为个人 GUI 操作验收。

## 自动验证

| 验证 | 结果 |
| --- | --- |
| Python 域、六档边界、截断、完成/超时竞争、就绪 stdout | 8/8 |
| Qt 风险业务测试 | 8/8（XML 含 init/cleanup 共 10 项） |
| 扩展候选业务测试 | 5/5 |
| MCP 工具/stdio/隐私协议 | 6/6 |
| 真实模型冒烟 | RankGuess OK，PARD 精确及非精确 beam OK |
| 长随机数值回归 | 16/16，有精度恢复证据，不复制输入 |
| 真实 MCP 冒烟 | RankGuess OK，3 工具，输入不回显，calibrated=false |
| Release GUI/CLI/proxy、Qt 部署、扩展构建 | 通过 |
| 跨组件静态契约、相关 JS 语法 | 通过 |

Qt 覆盖新增落盘/重开、切换账号、过期/会话/修订/删除目标、旧口令优先及审计自身排除、约束冲突、元数据修订、部分/合并消息、宿主超时后重启恢复与迟到回复。假服务只用于故障测试，真实浏览器闭环使用真实权重。

## 修复与证据边界

修复本轮原有依赖和 PATH/Qt 启动问题，补候选原目标绑定与新增保存。真实联调另发现并修复：同步回复与空异步回复粘连、popup 状态刷新关闭确认界面、长随机输入 float32 下溢、就绪回复被导入阶段 stdout 重定向吞掉，以及账号浮层遮挡网站提交。

六档来自论文第 9 页，表示估计区间。截断仅给下界，PARD 未命中保持未知。已有结果与权重身份核对见 model-provenance.md；当前配置没有充分标定依据，保持 UNKNOWN/REVIEW_REQUIRED，没有宣称已标定低风险或 QUALIFIED。

两套原算法未修改；MC.main/训练入口未调用；原本机验收时，权重、数据、缓存、口令库、浏览器配置与构建产物均在忽略目录，未上传公开仓库；2026-10-07 后续 GitHub 分发按用户新授权独立记录。本轮仅保证本机 Chrome/Edge，Firefox、扩展商店、跨机分发和个人首次 GUI 配置不在已通过证据内。2026-10-06 历史失败记录保留在 project-completeness-audit-2026-10-06.md。

## GitHub 独立 CPU 发行包验收

2026-10-07：解耦 Anaconda/CUDA，包含 Python 3.12.10 / torch 2.8.0+cpu / PyG 2.6.1、MSVC/Qt 运行库与中性命名模型。清洁 PATH、隔离模式与含空格的新目录验证通过，包外 Python 路径为零；Windows PowerShell 5 CLI/Python/模型检查通过。

Chrome/Edge 各 32/32，新增默认 3 秒 CPU 超时显式 UNKNOWN、不准备候选，随后显式 30 秒预算成功，完成账号切换拒绝、原条目更新和锁库。参考表离线生成 171343ms，带表冷启动 8750ms，四线程 beam 单样本 9219ms，随机长输入 4/4。未减少 beam/top-k，不自动延长预算；这些耗时是单次功能验收，不是性能分位数。Python 10/10、浏览器辅助业务 5/5、静态契约通过。

可复核脱敏摘要：release-acceptance-v0.2.0.json。发行包完整性按 Release 的 SHA256SUMS.txt 核验；个人 GUI 建库/主口令设置和首次正常浏览器加载/关联由用户完成，未验证其他机器的所有 Windows/硬件组合。
