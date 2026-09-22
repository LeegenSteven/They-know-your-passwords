# PROMPT.md — 把 KeePassXC 改造为「PARD 定向猜测 + RankGuess 拖网猜测」驱动的浏览器口令风险插件

> **本文件的用法**：这是本项目的总施工规格，可直接作为编码 Agent（Claude Code / Cursor / Codex 等）的输入，也可作为团队分工与验收的依据。
>
> **与同目录两份文档的关系**：同目录另有《基于拖网猜测与重用猜测评估算法的浏览器口令管理插件.md》（下称**项目报告**）与《浏览器口令管理插件开发框架.md》（下称**开发框架**）。本文件是这两份文档在**已核实的技术事实**基础上的落地修订版。凡本文件与上述两份文档冲突之处，以本文件为准，并应在最终项目报告中记录修订理由（答辩时这是加分项，不是减分项）。

---

## 0. 一句话任务

把 `keepassxc-develop/`（KeePassXC 2.8.0-snapshot，Qt6 / C++20）改造成带**个性化口令风险评估**的口令管理器：

- **无可用历史口令** → 调用 **RankGuess 拖网猜测**算法，输出该口令的**期望猜测次数**，衡量通用抗猜测强度；
- **存在可用历史口令** → 调用 **PARD 定向猜测**算法，输出候选口令在"由该用户旧口令推导出的候选列表"中的**排名**，衡量重用/衍生风险；
- 评估结果经 `keepassxc-browser` 的 fork 版浏览器扩展呈现，并把 **评估 → 建议口令 → 复检 → 保存** 做成闭环。

**.kdbx 加密口令库直接复用 KeePassXC 原有实现**，不新写加密存储；**不新增账号服务器**。

---

## 1. 已核实的事实基础（不要重新假设，直接采信）

以下事实已由本规格的编写者逐条读码/实机验证。编码 Agent 若发现与代码不符，应**停下来报告**，而不是自行"修正"本文件。

### 1.1 算法侧

**RankGuess（拖网猜测）——`RankGuess拖网猜测/`**

| 项目 | 事实 |
| --- | --- |
| 模型 | `GuesserModel`：`Embedding(98,64)` → `GRU(64,256,3层)` → `LayerNorm(256)` → 残差块 `out + GELU(fc1(out))` → `fc2: Linear(256,98)`，共 **1,134,562 参数（≈4.3 MB fp32）** |
| 词表 | 索引 0–94 = ASCII 32–126；95=`<SOS>`，96=`<EOS>`，97=`<PAD>`；`VOCAB_SIZE=98` |
| **输入域限制** | **长度 5 ≤ len ≤ 20**，且每个字符 `ord(c)` ∈ [32,126]；**越界口令被静默丢弃，不报错、不截断**（`MC.py:101-104`） |
| 编码 | `[SOS] + chars + [EOS]`（`MC.py:91-93`），编码后最长 22 token |
| 检点加载 | `MC.py:77 load_model()`；文件顶层键为 **`model_state_dict`**（不是 `model_state`）；模型构造**无参数** `GuesserModel()`；**必须传 `map_location`**（权重以 `cuda:0` 存储保存） |
| **单口令打分路径（原生存在）** | `MC.py:96 calculate_probability_batch()` → 序列概率 `P(pwd)=Π P(c_t\|c_<t)`；`MC.py:130 monte_carlo_estimation()` → 蒙特卡洛参考表；**`MC.py:173 get_guess_number(prob, ref_probs, ref_guesses)` → 期望猜测次数**。这是本项目拖网分支的核心入口 |
| 参考表成本 | 表是**每次运行在内存中现算的，从未落盘**。10 万样本：**CPU ≈ 20 s / GPU ≈ 1.5 s**（本机实测）。按模型缓存，不要每个请求重算 |
| 已知坑 | ① `get_guess_number` 在 `idx==0` 之外可能返回 **< 1.0** 的值，展示前需 `max(1.0, ·)`；② 低于整张表的口令被**截断到 `ref_guesses[-1]`**（不是 `inf`）；③ 表由 `torch.multinomial` 生成且 `torch.cuda.manual_seed_all` 未调用，**GPU 下参考表不可复现**，需要可复现就固定 CPU 或自行补种 |
| 导入副作用 | `MC.py:4-6` 用 `setdefault` 设 `OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1`；**`main()` 有 `__main__` 守卫**，可安全 `import`。`MC.py` **没有 CLI**，其 `main()` 是 15 模型 × 15 数据集的硬编码循环，**绝不能直接执行** |
| 硬编码路径 | `MC.py:279` 的 `D:\研究生\Password_Dataset\Password_Dataset`、`train2.py:102` 的 `trainword_tianya.txt` 在本机**不存在**。服务不得触碰这些常量，也不得为此修改算法文件 |

**PARD（定向猜测）——`PARD定向猜测/`**

| 项目 | 事实 |
| --- | --- |
| **实际机理** | 代码中**不存在** "PARD" 这个缩写（已 grep 全目录确认）。真实模型类名是 `Pass2Edit_GLCA_TwoStage` / `GatedLatentCrossAttention_Teacher_SourceStudent_TwoStage`。它做的事是：**给定该用户一个已知口令 `src`，输出该用户可能使用的其它口令 `tgt` 的排序候选列表**。这**正好**对应项目报告里的"口令重用/重用衍生评估"分支 |
| **重要** | 它**不是**个人信息的（姓名/邮箱/生日/手机号）定向猜测模型——数据通路里没有任何这类字段，唯一条件信号是 `src` 原始字符串。**答辩材料里不要把它描述成"基于个人信息的定向猜测"** |
| 架构 | 教师（GLCA 双向跨注意力，低秩潜空间 64 维）只在训练期用；**推理只跑 student**（`eval6_glca.py:396`）。教师 2,467,200 参数在服务期是死重 |
| 关键尺寸 | `MAX_LEN=20`、`EMBED_DIM=256`、`LATENT_DIM=128`、`NUM_HGT_LAYERS=3`、`NUM_HEADS=4`、`NUM_DEC_LAYERS=3`、`TGT_VOCAB_SIZE=98`（0=PAD,1=BOS,2=EOS,3+=字符）、`MAX_TGT_LEN=22` |
| **输入域限制** | 1 ≤ len ≤ 20，字符 `ord(c)` ∈ [32,126]。**越界时返回空列表，不抛异常**（`eval6_glca.py:128-134`） |
| 检点加载 | **正确入口是 `eval6_glca.py:56 _load_checkpoint_config()` + `:71 build_model_from_config()`**（它会校验 `architecture` 字段并从检点自身的 `model_config` 重建模型）。顶层键是 **`model_state`**（不是 `model_state_dict`）。`strict=True` 可零缺失加载 |
| **单口令打分路径（需适配层补，代码里没有现成函数）** | 代码只有"生成候选列表"：`eval6_glca.py:89 fast_batched_beam_search()`。但已**验证**可直接用底层原语拼出打分：`encode()`（`demo6_glca.py:536`）+ `decode()`（`demo6_glca.py:540`）→ `log_softmax` → 累加 BOS..EOS 的 log 概率。该配方对 `('password123'→'password')` 得 `-4.216178`，与集束搜索给出的 `-4.2162` 完全吻合 |
| 排名信号 | `evaluate()` 里的 `rank` = 目标串在候选列表中的 1-based 下标（`eval6_glca.py:420-423`）。**这就是重用风险的直接度量** |
| 内存/时延坑 | 默认 `beam_width=1000` + `eval_batch_size=24` 时，单步 `mem_exp` 广播张量 = `24×1000×20×256×4B ≈ 491 MB`。**服务化必须把 beam 降到 50–200、batch 设为 1** |
| 导入副作用 | `eval6_glca.py:21-23` 会把自身目录插入 `sys.path` 再 `from demo6_glca import ...`，因此**两个文件必须同目录共存**。两者**都有 `__main__` 守卫**，可安全 `import` |

### 1.2 宿主侧（KeePassXC）

| 项目 | 事实 |
| --- | --- |
| 版本/构建 | `2.8.0-snapshot`；**C++20**（`CMakeLists.txt:164`）；**只支持 Qt6，最低 6.2.4**（`:457`） |
| 浏览器集成入口 | `BrowserHost`（QLocalServer/命名管道）→ `BrowserService::processClientMessage`（`BrowserService.cpp:1772`）→ `BrowserAction::handleAction`（`BrowserAction.cpp:79-119`，16 分支 if/else，**没有 enum**） |
| 动作常量 | `BrowserAction.cpp:31-45`；新增动作需改 4 处：常量、`BrowserAction.h` 声明、路由分支、`handleXxx` 实现 |
| 异步回复范式 | `generate-password` 不即时回复，而是通过 `KeyPairMessage` + `appliedPassword` 信号 + `m_browserHost->sendClientMessage()` 延迟回复（`BrowserService.cpp:527-563`，结构体定义 `BrowserService.h:38-44`）。**算法调用必须走这个范式** |
| **线程约束** | `processClientMessage` 及其全部 handler 跑在 **GUI 主线程**。**任何阻塞式算法调用都会冻界面** |
| 回复构造 | `BrowserMessageBuilder::buildResponse(action, nonce, Parameters, ...)`，`Parameters = QMap<QString,QVariant>`（`BrowserMessageBuilder.h:27`），可直接塞 `QJsonObject`/`QJsonArray` |
| 版本握手 | 每个成功回复都带明文 `message["version"] = KEEPASSXC_VERSION`（`BrowserMessageBuilder.cpp:65`）。**本树里没有任何 `COMPATIBILITY` 常量** |
| 既有强度引擎 | 内置 zxcvbn（`src/thirdparty/zxcvbn/`），包装类 `PasswordHealth`（`PasswordHealth.cpp:35-45`，注意它给 zxcvbn 传的 user-dict 是 `nullptr`）；全库体检 `HealthChecker`（`:117-204`） |
| 历史口令 | `Entry::historyItems()`（`Entry.cpp:839-847`，完整 Entry 快照，自带 `password()`）；`Group::entriesRecursive(/*includeHistoryItems=*/true)`（`Group.cpp:592-609`）。KDBX 默认 `historyMaxItems=10` |
| 生成器 | `src/core/PasswordGenerator.{h,cpp}`（CSPRNG），浏览器侧生成口令走 `PasswordGeneratorWidget::popupGenerator()` |
| 外部进程先例 | `src/core/HibpOffline.cpp:120` 用 `QProcess` 调外部工具；`src/gui/remote/RemoteProcess.{h,cpp}` 是现成的 QProcess 封装 |
| 设置开关四件套 | `Config.h:148-170`（枚举键）→ `Config.cpp:170-180`（键名+默认值）→ `BrowserSettings.h/.cpp`（存取器）→ `BrowserSettingsWidget.ui` + `.cpp` 的 `load/saveSettings()`。范例：`allowGetDatabaseEntriesRequest` |
| 扩展侧不存在 | **`keepassxc-develop` 里没有 JS 扩展源码**。唯一相关的原生清单写在 `NativeMessageInstaller.cpp:330`，白名单在 `:40-43`（Chrome 扩展 ID `oboonakemofpalcgghocfoadofidjkkk`，Firefox `keepassxc-browser@keepassxc.org`） |
| 测试挂载 | `tests/CMakeLists.txt:28 add_unit_test(NAME ... SOURCES ...)`；`BrowserAction`/`BrowserService` 已对 `TestBrowser` 开放 `friend`（`BrowserAction.h:103`、`BrowserService.h:226`），测试里直接构造 JSON 调 `processClientMessage(nullptr, json)` |

### 1.3 环境侧（本机实测）

| 项目 | 状态 | 影响 |
| --- | --- | --- |
| Python | `D:\Anaconda3\envs\pytorch_cuda\python.exe` = **Python 3.9.25 + torch 2.8.0+cu129 + torch_geometric 2.6.1，`torch.cuda.is_available() == True`** | ✅ 算法服务可直接用这个解释器，**不需要新装 torch** |
| 基座环境 | `python` = 3.12.4，无 torch | 不要用它跑算法 |
| Qt | 仅有 **Qt 5.15.2**（Anaconda 自带） | ❌ **不够**，KeePassXC 2.8.0 要 Qt ≥ 6.2.4 |
| 编译器 | PATH 里没有 `cl`；有 MinGW `g++`（Strawberry/`D:\MinGW`） | ⚠️ 需装 VS2022 或改用 MSYS2-MinGW |
| vcpkg | **未安装**（无 `vcpkg` 可执行、无 `C:\vcpkg`）；但仓库自带 `vcpkg.json` + `vcpkg/triplets/` | ⚠️ Windows + MSVC 路线**必须**先装 vcpkg |
| cmake | 3.29.2 | ✅ 满足 |
| git | **`keepassxc-develop/` 不是 git 仓库**（是解压出来的目录） | ⚠️ 改之前先 `git init` + 首次提交，否则无法回滚/出 diff |

---

## 2. 可行性结论

**结论：可行，且路线清晰。** 三条关键判断：

1. **两类算法都有可用的"单候选口令打分"通路**。RankGuess 原生就有 `get_guess_number`；PARD 虽然没有现成函数，但本规格已实机验证出一条只调用其现有原语（`encode`/`decode`）的打分配方，**不需要改动任何算法核心代码**。这消除了本方案最大的技术不确定性。
2. **两类算法的输入域高度重合**（ASCII 32–126，长度上限 20），可以共用同一套域校验与"不可评估"降级逻辑。重合之处正好覆盖绝大多数真实口令。
3. **宿主侧的挂载点全是现成的**：新增动作走 `BrowserAction` 路由；异步回复有 `generate-password` 范式；外部进程有 `QProcess` 先例；加密存储、站点匹配、历史快照、口令生成全部复用 KeePassXC，无需自研。

**主要风险（按严重度排序）**：

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| **R1 构建环境** | 本机无 Qt6、无 vcpkg、无 MSVC。这是**唯一可能让项目卡死**的环节 | 见 §9 阶段 0：**先打通构建再写一行业务代码**。三条备选路线都要试，别在一棵树上吊死 |
| **R2 PARD 推理时延/内存** | 该模型是为离线批量评估写的，默认 beam=1000 会吃掉数百 MB | 服务化固定 `beam_width∈[50,200]`、`batch=1`、`top_n∈[100,500]`；单请求超时兜底 |
| **R3 阈值未标定** | 猜测次数 → 风险等级的映射**尚无团队自己的标定数据** | 阶段 3 用专用测试集标定；标定完成前界面显示"未标定"而不是给安全结论（项目报告 §6.2 亦如此要求） |
| **R4 扩展 fork 的 native messaging 白名单** | 改动扩展 ID 会让原生消息被拒 | 见 §7.1，两条路二选一，**必须在阶段 1 就定死** |
| **R5 参考表不可复现** | GPU 下 `torch.multinomial` 未播种 | 拖网参考表**固定用 CPU 构建**并缓存到磁盘（带模型哈希），保证同一口令永远得同一结论 |

---

## 3. 总体架构

### 3.1 架构图

```
┌────────────────────────── 浏览器 ──────────────────────────┐
│  扩展（fork 自 keepassxc-browser，纯 JS/GPL-3.0）           │
│    · 表单识别 / 输入监听 / 按需填充（上游已有）              │
│    · 【新】风险评估面板：风险等级 + 原因 + 建议口令 + 复检   │
└───────────────────────────┬────────────────────────────────┘
                            │ native messaging（4 字节长度前缀 + JSON）
┌───────────────────────────▼────────────────────────────────┐
│  keepassxc-proxy（未改动）                                    │
└───────────────────────────┬────────────────────────────────┘
                            │ QLocalSocket / 命名管道
┌───────────────────────────▼────────────────────────────────┐
│  KeePassXC（keepassxc-develop，本项目的改造主体）            │
│    BrowserAction                                                        │
│      · 【新】assess-password    → 风险评估（异步延迟回复）     │
│      · 【新】recommend-password → 生成+复检合格候选（异步）    │
│    src/riskassess/（【新】静态库）                            │
│      · AssessmentService   场景编排、历史判定、算法路由       │
│      · HistoryProvider     从 Entry 历史/全库收集可用历史口令  │
│      · AlgorithmClient     QProcess 常驻子进程 + JSON Lines   │
│      · RiskMapper          原生结果 → LOW/MEDIUM/HIGH + 原因   │
│      · RecommendationService 生成 → 复检 → 返回 QUALIFIED     │
│    .kdbx 加密口令库 / 站点匹配 / PasswordGenerator（复用）     │
│    zxcvbn（保留：对照展示 + 算法不可用时的降级）               │
└───────────────────────────┬────────────────────────────────┘
                            │ stdio JSON Lines（仅本地，不联网）
┌───────────────────────────▼────────────────────────────────┐
│  algo-service/（【新】Python 常驻进程）                       │
│    · RankGuess 适配器 → 期望猜测次数                          │
│    · PARD 适配器     → 候选在 top-K 中的排名 + log P          │
│    · 只读导入两份原始算法文件，不改动其任何一行代码            │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 文档 → 落地的映射（**答辩时直接放这张表**）

| 项目报告 / 开发框架 中的设计 | 本方案的落地位置 | 说明 |
| --- | --- | --- |
| 拖网口令猜测算法 | `algo-service` 的 RankGuess 适配器 | 输出期望猜测次数 |
| 口令重用算法 | `algo-service` 的 PARD 适配器 | 输出候选在旧口令推导列表中的排名 |
| 算法适配层 | `algo-service/adapters/` + `src/riskassess/AlgorithmClient` | 双侧适配：Python 侧管模型，C++ 侧管进程与超时 |
| 本地加密口令库 | **KeePassXC 的 .kdbx（复用，不新写）** | AES/ChaCha + Argon2 全套现成，比自研可靠 |
| 加密存储模块 | 同上 | 项目报告 §6.3 的密钥分层设计**由 .kdbx 实现满足**，报告中改为"复用成熟实现" |
| 账号服务（注册/登录） | **取消** | 主口令解锁 .kdbx **同时**完成"身份认证"与"本地密钥恢复"，正好满足开发框架约定 1/2；且天然满足"服务器看不到网站口令" |
| 内容脚本 | fork 版的 content script（上游已有） | 不重写 |
| 凭据调用 / 填充 | 上游 `get-logins` + 填充逻辑（复用） | 仅新增"评估后才允许保存"的闸门 |
| 建议口令生成 | **KeePassXC 的 `PasswordGenerator`（CSPRNG）** | 项目报告 §4.5 明确允许：算法不含生成模块时用标准安全随机 |
| 风险等级映射 | `src/riskassess/RiskMapper` + `risk-thresholds.json` | 两算法**各自独立阈值，不相加、不平均**（项目报告 §6.2 硬要求） |
| 历史集合选择规则 | `HistoryProvider` | 见 §6.3，含"排除自身"等四条规则 |
| 统一错误码 | `shared/errors.h` + 协议里的 `status`/`reason` | 见 §5.4 |
| 多端同步 / 备份 | **首期不做**（与开发框架一致） | |

---

## 4. 工程目录规划

```
D:\研究生\网络安全竞赛\
├── PROMPT.md                       ← 本文件
├── 基于拖网猜测与重用猜测评估算法的浏览器口令管理插件.md   （输入，不改）
├── 浏览器口令管理插件开发框架.md                            （输入，不改）
├── PARD定向猜测\                    （**只读**，不得修改任何一行）
├── RankGuess拖网猜测\               （**只读**，不得修改任何一行）
├── keepassxc-develop\              （改造主体，动手前先 git init）
│   ├── src\
│   │   ├── riskassess\             【新】风险评估静态库
│   │   │   ├── CMakeLists.txt
│   │   │   ├── AssessmentService.{h,cpp}      场景编排 + 算法路由
│   │   │   ├── HistoryProvider.{h,cpp}        可用历史口令的收集与筛选
│   │   │   ├── AssessmentTypes.h              mode/status/风险等级/原因码
│   │   │   ├── AlgorithmClient.{h,cpp}        QProcess + JSON Lines + 超时
│   │   │   ├── RiskMapper.{h,cpp}             原生结果 → 风险等级
│   │   │   ├── RecommendationService.{h,cpp}  生成 → 复检 → QUALIFIED
│   │   │   └── RiskSettings.{h,cpp}           阈值/超时/服务路径配置
│   │   ├── browser\                【改】BrowserAction / BrowserService / 设置四件套
│   │   ├── gui\                    【改】设置页、报告页、编辑对话框的风险面板
│   │   └── ...
│   └── tests\
│       ├── TestRiskAssess.cpp      【新】§8 的用例
│       └── data\risk\              【新】虚构测试口令集
├── algo-service\                   【新】Python 常驻算法服务
│   ├── README.md
│   ├── requirements.txt            torch / torch_geometric / numpy（复用现有 env）
│   ├── config.toml                 模型路径、beam、top_n、参考表缓存位置
│   ├── server.py                   stdio JSON Lines 主循环
│   ├── adapters\
│   │   ├── __init__.py
│   │   ├── base.py                 统一响应结构、域校验、异常兜底
│   │   ├── rankguess.py            拖网：概率 → 期望猜测次数
│   │   └── pard.py                 定向：top-K 排名 + log P 打分
│   ├── mc_cache\                   蒙特卡洛参考表缓存（.npz + 模型哈希）
│   └── tools\smoke_test.py         自检脚本（见 §5.6）
└── keepassxc-browser\              【新】fork 自上游（GPL-3.0），扩展侧改造
    └── keepassxc-browser\
        ├── js\
        │   ├── assessment.js       【新】风险评估请求与状态管理
        │   └── ...                 （其余改上游文件）
        └── ...
```

---

## 5. 算法服务契约（本项目最关键的接口，务必按此实现）

### 5.1 传输

- **形态**：KeePassXC 用 `QProcess` 启动 `python.exe -u server.py`，**常驻**，通过 **stdin/stdout** 交换**一行一条**的 JSON（`\n` 结尾，UTF-8，**不带 BOM**）。
- **stdout 只能出现协议 JSON**。所有日志、`tqdm` 进度条、警告一律走 **stderr**（`tqdm(file=sys.stderr)`，或用 `--quiet` 关掉）。
- **不监听任何网络端口**，不读环境变量里的代理，不做任何出站连接。
- 启动时用 `ping` 探活；模型加载与蒙特卡洛参考表构建**放在启动阶段**（`--warmup`），就绪前 `ping` 返回 `{"status":"LOADING"}`。

### 5.2 请求 / 响应格式

请求：

```json
{"id": "8f3c…", "method": "assess_trawling", "timeout_ms": 3000, "params": { … }}
```

成功响应：

```json
{"id": "8f3c…", "status": "OK", "mode": "TRAWLING",
 "algorithm_version": "rankguess-csdn-epoch20",
 "native": { …原生结果，不做业务加工… },
 "elapsed_ms": 12}
```

失败响应（**任何情况下都必须回一行 JSON，且 id 与请求一致**）：

```json
{"id": "8f3c…", "status": "OUT_OF_DOMAIN", "mode": "TRAWLING",
 "error_code": "OUT_OF_DOMAIN", "detail": "length 24 > 20"}
```

`status` 取值固定为：`OK` / `OUT_OF_DOMAIN` / `TIMEOUT` / `ERROR` / `UNAVAILABLE` / `LOADING`。

### 5.3 方法定义

#### `ping` → `{"status":"OK","ready":true,"models":{...},"algorithm_versions":{...}}`

#### `assess_trawling`（无可用历史口令时）

请求参数：`{"candidate": "P@ssw0rd123"}`

适配逻辑（**只调用 `MC.py` 现有的三个函数**）：

```
域校验（5 ≤ len ≤ 20，全字符 ord ∈ [32,126]）
   └─ 不满足 → OUT_OF_DOMAIN（**绝不截断、绝不静默丢弃**）
        ↓
MC.calculate_probability_batch(model, [candidate], device)  → prob
        ↓
MC.get_guess_number(prob, ref_probs, ref_guesses)           → guess_number
        ↓
clamp: guess_number = max(1.0, guess_number)
```

响应 `native`：

```json
{"guess_number": 65.9, "probability": 2.195e-4,
 "table_size": 100000, "clamped_low": false, "table_capped": false}
```

> `table_capped: true` 表示该口令低于整张参考表，真实猜测次数**高于** `guess_number`——界面文案必须体现这是**下界**。

#### `assess_reuse`（存在可用历史口令时）

请求参数：`{"candidate": "…", "history": ["old1", "old2"]}`（`history` 已由 C++ 侧按 §6.3 规则筛好，**服务不再自行筛选**）

适配逻辑（对每条历史口令 `src` 各做一次，取风险最高者）：

```
逐条 src:
  域校验（1 ≤ len ≤ 20，全字符 ∈ [32,126]）→ 不满足则跳过，并计入 skipped
  ① 精确相同：candidate == src                     → exact_match = true（最高风险，直接短路）
  ② 排名：eval6_glca.fast_batched_beam_search(model, [src], beam_width=…, top_n=K)
          在返回列表里查 candidate（**精确字符串比较**）
          → 命中则 rank = 1-based 下标，in_top_k = true
  ③ 打分：candidate 不在 top-K 时，用 encode()+decode() 配方算 log P(candidate | src)
          （配方见 §1.1，已实机验证与集束搜索分数一致）
取所有 src 中 rank 最小者作为 best；exact_match 优先于一切 rank
```

响应 `native`：

```json
{"exact_match": false, "in_top_k": true, "best_rank": 37,
 "best_source_index": 0, "top_k": 200, "candidate_logprob": -12.31,
 "usable_sources": 2, "skipped_sources": 1,
 "algorithm_version": "pard-glca-csdn-e10"}
```

> `usable_sources == 0` 而 `history` 非空 → 必须返回 **`OUT_OF_DOMAIN`**（历史口令全部超出算法适用范围），**不能**退化成"无历史"分支去跑拖网。项目报告 §4.6 明确要求区分"没有历史"与"历史不可用"。

#### `shutdown` → 优雅退出（关闭时由 C++ 侧发送）

### 5.4 原因码（`reason_codes`，由 C++ 侧 `RiskMapper` 生成，非算法输出）

`EXACT_REUSE`（与已存口令完全相同）、`TOP_K_DERIVABLE`（可由旧口令在 top-K 内推导）、`COMMON_PASSWORD`（猜测次数极低）、`BELOW_TABLE`（猜测次数超出参考表下界）、`UNUSABLE_HISTORY`（历史全部超出算法域）、`UNCALIBRATED`（阈值尚未标定）、`ALGORITHM_UNAVAILABLE`。

项目报告 §6.1 的硬约束：**原因类别、置信度、估计猜测次数，只能在算法确实支持时输出，界面层不得凭空补充。**

### 5.5 性能预算（写进 README，并实测填写）

| 操作 | 预算 | 实测（待填） |
| --- | --- | --- |
| 服务冷启动（含模型加载 + 参考表） | ≤ 60 s（GPU）/ ≤ 120 s（CPU） | |
| `assess_trawling` 单次 | ≤ 150 ms | |
| `assess_reuse` 单次（每条历史 ≤ 200 ms） | ≤ 800 ms（按 4 条历史） | |
| 参考表缓存命中后冷启动 | ≤ 15 s | |

### 5.6 自检脚本 `tools/smoke_test.py`（**阶段 2 必须先跑通这个**）

必须断言以下行为，作为服务可用的判据：

1. `assess_trawling("password123")` → `OK`，`guess_number` 与 `MC.py` 直接调用的结果一致；
2. `assess_trawling("abc")`（长度 3）→ `OUT_OF_DOMAIN`；
3. `assess_trawling("口令密码测试口令")`（非 ASCII）→ `OUT_OF_DOMAIN`；
4. `assess_trawling("a"*25)` → `OUT_OF_DOMAIN`；
5. `assess_reuse(candidate="password123", history=["password123"])` → `exact_match == true`；
6. `assess_reuse(candidate="password1", history=["password"])` → 列表内命中且 `best_rank` 为小整数（PARD 最擅长的正是这类"加个后缀"的变换）；
7. `assess_reuse(candidate="x", history=["很长的中文旧口令超出域"])` → `OUT_OF_DOMAIN` 且 `usable_sources == 0`；
8. stdout 中**不出现任何非 JSON 行**；日志全部在 stderr。

---

### 5.7 可选模型接口扩展（保留原框架与默认实现）

本节仅扩展现有算法适配层：保留原目录主体、传输方式、业务分支、存储方案、开发阶段和浏览器动作。无历史仍走拖网，有历史仍走重用；通用补检沿用原有条件，不改为强制双维度。RankGuess、PARD 与 KeePassXC PasswordGenerator 仍是默认实现。前述具体模型调用规则适用于其默认适配器，新接入模型按各自能力处理。

**扩展目标：** 调用方通过稳定接口选择不同的强度评估模型或建议口令提供器；符合既有能力契约的新模型仅增加适配器及配置，不在浏览器业务代码中新增模型专属分支。对外仍指现有可信本地调用链，不因此新增公网服务。此处为实现要求，不表示接口或新模型已实现。

#### 5.7.1 模型能力与选择

在已有 `algo-service/adapters/` 内补充统一能力描述和适配器注册映射，不增加新的业务层。按能力配置三个相互独立的选择项：

| 配置项 | 能力 | 默认实现 |
| --- | --- | --- |
| `trawling_model_id` | `ASSESS_TRAWLING`：候选口令通用强度计算 | RankGuess 适配器 |
| `reuse_model_id` | `ASSESS_REUSE`：候选与历史的重用评估 | PARD 适配器 |
| `generator_model_id` | `GENERATE_CANDIDATES`：提供待复检的建议候选 | KeePassXC PasswordGenerator 包装器 |

配置未指定时执行原默认逻辑。一个模型可以实现一个或多个能力，评估与生成无需来自同一模型。`generator_model_id` 是提供器标识，允许标准随机生成器，不要求它是机器学习模型。默认生成器仍由宿主执行；只有选择本地算法服务中的生成模型时才发送下述生成请求，不把 PasswordGenerator 搬进 Python。

能力描述包含 `model_id`、`model_version`、`capabilities`、`input_domain`（长度单位/范围、字符集，按候选和 source 分别描述）、`required_context`、`output_metrics`、`adapter_version`。RankGuess/PARD 的当前输入限制不作为其它模型的全局限制。配置引用已安装并注册的适配器 ID，不能从网页请求加载任意代码或权重路径。

#### 5.7.2 保持原方法兼容的接口增补

`assess_trawling`、`assess_reuse` 的方法名、候选/历史参数及原响应字段保留，仅在 `params` 增加可选 `model_id`。服务依能力查找适配器；省略 ID 时使用对应默认模型。响应增补 `model_id`、`model_version`、`metric_type`，并保留原 `algorithm_version` 和 `native`；已有默认适配器的 native 结构不变。

新增 `list_models` 方法，返回已注册模型的能力和就绪状态，供可信设置界面及业务层选择。模型选择在现有配置/设置中完成，不授予内容脚本任意模型调用权限。

请求示例（仅示意结构）：

```json
{"id":"assess-01","method":"assess_trawling","timeout_ms":3000,"params":{"candidate":"Example-Only-42","model_id":"rankguess"}}
```

可选生成模型通过新增 `generate_candidates` 方法接入本地服务：

```json
{"id":"generate-01","method":"generate_candidates","timeout_ms":3000,"params":{"model_id":"installed-generator","constraints":{"min_length":12,"max_length":20,"required_classes":["lower","upper","digit"],"forbidden_chars":" "},"count":3}}
```

这里 `installed-generator` 是占位注册 ID，不表示当前已安装该模型。约束由适配层从现有网站约束字段转换；不能确定的约束不得编造为已满足。成功响应保留 `id/status/algorithm_version/elapsed_ms`，另含 `model_id/model_version/candidates[]`；不强行给生成结果设置 TRAWLING/REUSE 模式。候选只是待复检数据，生成服务不返回 QUALIFIED 或已安全的结论。

沿用原六种服务状态：未注册为 `UNAVAILABLE` + `MODEL_NOT_FOUND`；能力不匹配为 `ERROR` + `CAPABILITY_MISMATCH`；缺少上下文为 `ERROR` + `MISSING_CONTEXT`；越域为 `OUT_OF_DOMAIN`；超时为 `TIMEOUT`；模型输出非法为 `ERROR` + `INVALID_MODEL_OUTPUT`。原因放在 `error_code`，不另改旧状态枚举。明确指定模型失败时不得悄然换成另一模型。

#### 5.7.3 适配器最小契约

以下是语言无关职责，以 Python 形式示意。C++ 默认生成器包装器遵守相同能力语义，通过现有 RecommendationService 调用。

```python
class ModelAdapter:
    def describe(self): ...
    def load(self, config): ...
    def evaluate(self, candidate, history, options): ...
    def generate_candidates(self, constraints, count, options): ...
    def dispose(self): ...
```

`evaluate` 和 `generate_candidates` 按声明能力实现；不支持的方法显式返回能力错误。原有评估适配器可由轻量包装器提供 describe/load，不要求改动算法核心。模型按需加载并复用，继续使用现有异步、超时、缓存及锁定清理机制。

不同模型的原始指标保留各自含义。例如估计猜测次数、搜索列表排名、条件概率或原生评分不能直接相加、平均或冒充同一量纲。RiskMapper 按模型 ID、模型/适配器版本、指标及相关推理配置选择独立标定规则；新增指标没有映射时返回未标定，不套用 RankGuess/PARD 阈值。搜索未命中不等于低风险，模型不支持输入不等于强度足够。

#### 5.7.4 建议口令提供的接入位置

只扩展 §6.1 RecommendationService 的“产生候选”一步：

```text
网站约束 → 选定生成提供器 → 待复检候选
       → 原有约束检查 → 原有评估分支及必要通用补检
       → 通过则 QUALIFIED → 原有用户确认与保存
```

没有生成模型配置时仍调用 PasswordGenerator。评估模型没有生成能力时，可与默认随机生成器组合，通过该评估模型筛选建议。支持生成的模型只产生候选，所有候选仍由业务层检查长度、字符约束并执行原有复检，不能由生成模型自行宣布通过。

生成接口默认只接收约束、数量和预算，不接收历史口令、站点名或用户名作为生成模板。PARD 的旧口令衍生猜测列表用于风险评估，不能直接当作推荐口令。候选不写日志或持久化，锁定/取消后释放；可预测的固定候选池不能冒充安全随机生成。模型生成的适用性和效果需验证，通过自身评分不构成独立安全证明。

保留原有尝试次数、总耗时和失败处理；生成与复检共享预算。新模型失败或不支持约束时明确提示，只有显式配置了默认生成器回退策略才允许回退，并保留实际提供器身份，不能降低推荐标准。

#### 5.7.5 局部实现与验收

1. 在已有适配层补充能力描述、注册映射和三个模型配置项；保留默认行为。
2. 为原两个评估适配器和原随机生成器增加统一包装，接入可选 model_id 与 list_models。
3. 在原 RecommendationService 的候选来源位置接入可选生成提供器，不改变后续复检、保存流程。
4. 检查：不配置时原用例仍通过；更换评估模型后返回正确身份及独立映射；生成提供器可替换且输出仍经过复检；无生成能力、未知模型、越域、超时、非法输出和未标定均明确处理。
5. 使用测试适配器验证可替换性并明确标注模拟；接入实际新模型时再记录效果及性能，不把本扩展变成必须额外训练模型或重建主架构的前置条件。

**给编码 Agent 的补充要求：** 按本节对现有适配层和 RecommendationService 做局部接口扩展；保留本文其他章节的主要结构与默认流程，不借此重构账号、存储、历史路由、浏览器动作或实施阶段。

---

## 6. KeePassXC 侧改动清单

### 6.1 新增静态库 `src/riskassess/`

- 挂载方式：`src/CMakeLists.txt` 加 `add_subdirectory(riskassess)`，仿照 `src/browser/CMakeLists.txt` 的写法；库链接 `Qt6::Core Qt6::Concurrent`（**不要引入 Qt6::Widgets**，保持可被 core 复用）。
- 新增构建开关 `KPXC_FEATURE_RISKASSESS`（默认 `OFF`），仿 `KPXC_FEATURE_BROWSER` 的写法（`CMakeLists.txt:53`）。

**`AssessmentService`**（对外唯一门面）

```cpp
enum class AssessmentMode { TRAWLING, REUSE, NONE };
enum class RiskLevel { LOW, MEDIUM, HIGH, UNKNOWN };   // 未知绝不映射为 LOW

struct AssessmentRequest {
    QString candidate;
    QString url;             // 仅用于日志/展示，绝不进算法服务
    QString username;
    quint64 inputRevision;   // 输入版本（防过期结果）
    quint64 vaultRevision;   // 口令库版本
};

struct AssessmentResult {
    AssessmentMode mode;
    RiskLevel level = RiskLevel::UNKNOWN;
    QString status;                       // OK / OUT_OF_DOMAIN / TIMEOUT / …
    QJsonObject nativeResult;             // 原样保留算法输出
    QStringList reasonCodes;
    QString algorithmVersion;
    QDateTime evaluatedAt;
    quint64 inputRevision, vaultRevision; // 与请求绑定，不一致即 STALE_RESULT
};
```

- `evaluate()` 返回 `QFuture<AssessmentResult>` 或走信号；**严禁在调用线程做阻塞等待**。
- 结果必须绑定 `inputRevision` + `vaultRevision`；两者任一变化，旧结果作废（项目报告 §6.6）。

**`HistoryProvider`**——历史集合选择规则（开发框架 §5.3，逐条实现）：

| 场景 | 规则 |
| --- | --- |
| 新增记录 | 当前库内其它有效记录的口令 |
| 修改记录 | **包含当前记录的原口令**（识别旧口令变体）+ 其它记录 |
| 存量复检 | **排除记录自身**，保留其它记录（含其它记录中相同口令） |
| 推荐候选 | 与当前业务场景同一规则 |

- 排除回收站（`Group::isRecycled()`）、排除 `isAttributeReference("Password")` 的 `{REF:}` 口令。
- 只取 `Entry::historyItems()` 与 `entriesRecursive()`；**首期不额外保存历次被替换的口令**（项目报告 §5.3）。
- 上限 `maxHistoryPasswords`（默认 **5**），按修改时间倒序取最近的；被截断的条数要上报给界面。

**`AlgorithmClient`**

- `QProcess` 常驻子进程（参考 `src/gui/remote/RemoteProcess.{h,cpp}` 的封装风格，但**不要复用其模板变量替换逻辑**）。
- 逐行读 stdout，按 `id` 匹配响应；每次请求带 `QTimer` 超时（默认 3000 ms，可配）。
- 进程崩溃/超时 → 自动重启 + 返回 `UNAVAILABLE`，**绝不返回默认强/弱等级**。
- **stdout 解析失败必须视为错误**，不得静默吞掉。
- stderr 只进日志，且**日志里不能出现候选口令**（见 §7.3）。

**`RiskMapper`**——阈值配置放 `share/riskassess/risk-thresholds.json`：

```json
{
  "schema": 1,
  "calibrated": false,
  "trawling": {
    "algorithm_version": "rankguess-csdn-epoch20",
    "guess_number": { "high_max": 1000, "medium_max": 1000000 },
    "note": "占位阈值，尚未用团队数据标定"
  },
  "reuse": {
    "algorithm_version": "pard-glca-csdn-e10",
    "top_k": 200,
    "rank": { "high_max": 10, "medium_max": 1000 },
    "logprob": { "high_min": -8.0, "medium_min": -20.0 },
    "note": "占位阈值，尚未用团队数据标定"
  }
}
```

- 上表的数值是**占位值**，沿用"猜测次数 10^k"的通行量纲（项目报告 §9.3 要求基线测量后再定）。`"calibrated": false` 时，界面必须同时展示"阈值未标定"，**不得**给出"安全/不安全"的绝对结论。
- **两套阈值各自独立判断，禁止相加、平均或互相换算**（项目报告 §6.2 硬要求）。
- 算法 `algorithm_version` 与配置里记录的不一致 → 返回 `RESULT_UNCALIBRATED`，不给等级。

**`RecommendationService`**——建议口令闭环（项目报告 §4.5）：

1. 接收网站约束（最小/最大长度、必需字符类、禁用字符）。
2. 用 **KeePassXC 的 `PasswordGenerator`** 生成候选（CSPRNG；**不把站名/账号名/旧口令变体当模板**）。
3. 用 `AssessmentService` **复检**：无历史时要求拖网达标；有历史时先查重用，**并在 PARD 不具备通用强度判断能力时补跑拖网**（§4.4 要求）。
4. 只有约束检查 + 所需复检**全部通过**才返回 `QUALIFIED`。
5. 达到尝试次数上限（默认 20）或总耗时上限（默认 3000 ms）仍未通过 → 返回 `CONSTRAINT_UNSATISFIED`，**不得降低标准后标记为安全**。
6. 候选口令**在用户确认前不持久化、不进日志、不进遥测**。

### 6.2 浏览器动作新增（`BrowserAction`）

在 `BrowserAction.cpp:31-45` 加两个常量，`BrowserAction.h` 加两个 `handleXxx` 声明，`handleAction`（`:79-119`）加两个分支：

| 动作 | 请求参数 | 成功回复 |
| --- | --- | --- |
| `assess-password` | `url`, `username`, `password` | `mode`, `riskLevel`, `status`, `reasonCodes[]`, `algorithmVersion`, `evaluatedAt`, `calibrated`, `usableHistoryCount`, `skippedHistoryCount` |
| `recommend-password` | `url`, `username`, `constraints{length,classes,forbidden}` | `password`, `riskLevel`, `mode`, `attempts`, `status` |

实现要点：

- **必须走异步延迟回复**（照抄 `generate-password` 的 `KeyPairMessage` + 信号 + `sendClientMessage` 范式，`BrowserService.cpp:527-563`）。在 `handleAction` 里同步等待算法服务 = 冻界面 = 直接失败。
- 加**用户开关**闸门（仿 `BrowserAction.cpp:408-410` 的 `allowGetDatabaseEntriesRequest` 检查）。新增错误码接在 `BrowserMessageBuilder.h:31-67` 枚举之后（**下一个可用值是 35**），并在 `BrowserMessageBuilder.cpp:96-164` 加对应文案。
- 请求来源必须由 `BrowserService` 结合浏览器真实发送方判定，**不采信请求体里自报的站点/用户/"已确认"字段**（开发框架 §7.2）。
- 内容脚本**无权**调用全库枚举、任意解密或任意保存。
- 日志（`BrowserMessageBuilder.cpp` 收到的明文）**绝不能写入候选口令**——上游现有调试输出需一并复查。

### 6.3 设置项（四件套）

新增开关（默认 **关闭**，与"最小权限"一致）：

| 键 | 默认 | 含义 |
| --- | --- | --- |
| `RiskAssess/Enabled` | false | 启用个性化风险评估 |
| `RiskAssess/ServicePath` | "" | Python 解释器路径（本机默认 `D:\Anaconda3\envs\pytorch_cuda\python.exe`） |
| `RiskAssess/ServiceScript` | "" | `algo-service/server.py` 路径 |
| `RiskAssess/TimeoutMs` | 3000 | 单次评估超时 |
| `RiskAssess/MaxHistoryPasswords` | 5 | 送进算法的历史口令条数上限 |
| `RiskAssess/Device` | "auto" | `auto`/`cpu`/`cuda` |

改 4 个文件：`src/core/Config.h`、`src/core/Config.cpp`、`src/riskassess/RiskSettings.{h,cpp}`、`src/browser/BrowserSettingsWidget.ui` + `.cpp`（`loadSettings()`/`saveSettings()`，参照 `BrowserSettingsWidget.cpp:100-120` 附近）。

### 6.4 GUI 展示

- **入口编辑对话框**：在密码字段下方增加风险徽章（低/中/高/未评估 + 评估模式 + 原因），与既有 zxcvbn 强度条**并列显示**，并明确标注两者量纲不同。
- **报告页**：仿 `src/gui/reports/ReportsWidgetHealthcheck.cpp` 新增一页"个性化风险评估"，展示每条记录的重用风险与最后评估时间。
- 文案必须包含项目报告 §6.2 的四类标准表述，且：
  - **不展示"需要 X 年破解"这类精确时长**（除非团队另有经论证的猜测成本模型）；
  - **不在网页提示中展示旧口令明文或可推断旧口令的变换细节**；
  - 未标定 → 显示"暂无法给出强度结论"，而不是任何等级。

---

## 7. 浏览器扩展侧改动（fork 自 keepassxc-browser）

### 7.1 先解决两个必踩的坑

**坑 A：native messaging 白名单。** 原生消息清单由 KeePassXC 自己写（`NativeMessageInstaller.cpp:330`），白名单硬编码在 `:40-43`。fork 后的扩展 ID 会变，消息会被拒。二选一：

- **方案 1（推荐）**：把 fork 的扩展 ID 加进 `ALLOWED_ORIGINS`，并在 `NativeMessageInstaller` 里把本项目的服务路径一并注册；
- **方案 2**：在 fork 的 `manifest.json` 里保留上游的 `key` 字段，使 Chrome 算出**同一个 ID**（`oboonakemofpalcgghocfoadofidjkkk`），零改动但 ID 与上游冲突，只适合本机演示。

**坑 B：版本门。** 上游扩展会校验宿主版本串；本项目宿主报 `2.8.0-snapshot`。fork 里必须放宽该检查，否则一连就断。

### 7.2 扩展侧新增内容

| 文件 | 内容 |
| --- | --- |
| `js/assessment.js`【新】 | 调用 `assess-password` / `recommend-password`；管理请求 `requestId`；丢弃过期响应（同一输入只认最新一次）；超时与错误态文案 |
| `js/keepass.js` 等【改】 | 注册两个新 action；`generate-password` 流程改为"生成后自动复检" |
| 保存/更新流程【改】 | 表单提交后**先评估、再弹保存提示**，提示里带上风险徽章与"换成建议口令"按钮；用户可坚持保存原口令，但必须看到风险提示 |
| 弹窗 UI【改】 | 风险面板：评估模式徽章（通用评估 / 重用评估）、等级、原因、建议口令入口 |
| 设置页【改】 | 风险评估开关、展示阈值标定状态 |
| i18n | 新增文案走上游的 `_locales` 机制，中文必须补齐 |

**硬约束（项目报告 §4.2）**：插件**不得**未经用户操作修改网站表单或提交网站请求；只复制或填入候选**不视为**网站已接受该口令，必须由用户确认后才更新记录。

---

## 8. 安全与隐私边界（不可协商）

1. **算法服务仅本地 stdio，不监听端口、不出站**。若将来改成本机端口，必须加令牌 + 仅绑 `127.0.0.1`，并在报告里说明威胁模型变化。
2. **日志零明文**：候选口令、历史口令、主口令、派生密钥、参考表里的口令**一律不得**进日志、异常报告、遥测、画面截图。调试构建同样遵守（项目报告 §7.1）。
3. **候选口令在用户确认前不落盘**；`algo-service` 除参考表缓存外**不写任何含口令的文件**，参考表缓存中**不得含明文口令**（只存概率/猜测次数数组）。
4. **内容脚本不接收历史口令集合、主密钥或全库数据**。明文只在 KeePassXC 可信进程与算法服务进程内流转。
5. **站点匹配沿用 KeePassXC 现有严格匹配**，不采用字符串包含/后缀匹配；跨域框架、跳转站点、未加密页面未经核验不得填充。
6. **算法不可用/超时/越域 → 一律"暂无法评估"**，不显示默认强等级，也**不得**把未知映射成 `LOW`（开发框架 §六）。
7. **演示适配器必须显著标注"模拟"**，不得以占位分数冒充真实算法能力（开发框架 §8.7）。

---

## 9. 分阶段实施计划

> 对应项目报告 §9.1 的五个阶段，但**把构建打通提为阶段 0 的硬前置**——这是本方案唯一的高风险环节。

### 阶段 0：把 KeePassXC 编译出来（**先做这个，再写任何业务代码**）

1. `cd keepassxc-develop && git init && git add -A && git commit -m "upstream 2.8.0-snapshot 基线"`（**先做，否则后面无法回滚**）。
2. 三条路线按顺序试，**哪条先通走哪条**：
   - **路线 A（官方推荐，Windows）**：装 VS2022 Build Tools + `vcpkg`，`cmake -B build -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake`（仓库自带 `vcpkg.json`，依赖会自动拉）；
   - **路线 B（最省事）**：**WSL2 / Ubuntu**，`apt install qt6-base-dev libbotan-2-dev libargon2-dev zlib1g-dev libreadline-dev libpcsclite-dev`，Linux 下这条路在本仓库是一等公民；
   - **路线 C（Windows，轻量）**：**MSYS2-MinGW64** + `pacman` 装 Qt6/Botan。
3. 验收：`keepassxc` 与 `keepassxc-proxy` 两个可执行文件能跑起来，能建库、存条目。**此步不过，后面全部无意义。**

### 阶段 1：接口与边界确认（无 UI，纯命令行）

1. 用 `D:\Anaconda3\envs\pytorch_cuda\python.exe` 直接跑通 §5.6 的 `smoke_test.py`（**纯 Python，不碰 C++**）。
2. 定死 §7.1 的白名单方案（A 还是 B）。
3. 产出：`docs/algorithm-contract.md`（算法侧接口）、威胁模型（两句话也要写清"谁能看到明文"）。

### 阶段 2：算法服务成型

1. `algo-service/` 实现 §5 全部方法与状态码，参考表缓存落盘（带模型哈希）。
2. 验收：§5.6 八条断言全绿；`assess_trawling` / `assess_reuse` 的实测时延填进 §5.5 表格。

### 阶段 3：KeePassXC 侧接入（无扩展也能测）

1. `src/riskassess/` 四个类 + `RiskSettings`；CMake 开关。
2. `BrowserAction` 两个新动作（异步回复）。
3. 单测：`tests/TestRiskAssess.cpp` 覆盖 §11.1 的用例（用**桩算法服务**注入固定结果，不依赖模型）。
4. 阈值标定：用团队自备的测试集，产出 `risk-thresholds.json` 并把 `calibrated` 置 `true`；标定数据与测试数据必须隔离（项目报告 §9.3）。

### 阶段 4：扩展 fork 与联调

1. fork 扩展，打两个坑（§7.1）、接两个新动作、加风险面板。
2. 端到端跑通：**首次保存（拖网）→ 第二个站用相似口令（重用）→ 取建议口令 → 复检 → 确认保存 → 回访填充 → 锁定**。

### 阶段 5：验证与答辩材料

- 按 §11 出测试材料；录演示视频（用虚构账号 + 专用测试页）。
- 演示脚本见项目报告 §10.1；**异常用例（算法超时、越域、历史不可用、库锁定）也要演**。

---

## 10. 明确禁止（做错了要返工）

1. **不得修改 `PARD定向猜测\` 与 `RankGuess拖网猜测\` 里的任何一行代码**。适配层只做"只读 import + 组合调用"。打分配方里的 `encode`/`decode` 是**模型已有的方法**，只是没人把它拼起来。
2. **不得让 `MC.py` 的 `main()` 被执行**（那是 15×15 的离线实验循环），也不得依赖 `train2.py` 的训练逻辑。
3. **不得新增账号服务、不得把口令或派生数据上传**。项目报告已把"本地优先"写成核心卖点，破例即失分。
4. **不得编造准确率、召回率、响应时间、用户规模**。项目报告 §9.3 已明确"报告当前不填写未经验证的指标"——实测多少写多少，没测就写"未测"。
5. **不得把两类算法的原生分数相加/平均/换算**，也不得把未知结果映射为 `LOW`。
6. **不得在 GUI 主线程做阻塞式算法调用**。
7. **不得在答辩材料中声称 PARD 是"基于个人信息的定向猜测"**（代码里没有个人信息通路，见 §1.1），也不要使用代码中不存在的缩写。

---

## 11. 测试与验收

### 11.1 功能用例（对应项目报告 §9.2 八条，逐条落成测试）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 空库保存首条口令 | 走 `TRAWLING`；界面标注"通用评估" |
| 2 | 有 1 条历史 | 走 `REUSE`（**不必等到多条**） |
| 3 | 有 N 条历史 | 复用同一条规则，`usable/skipped` 计数正确 |
| 4 | 原样复用旧口令 | `exact_match=true`，`HIGH`，原因 `EXACT_REUSE` |
| 5 | 旧口令加后缀变体 | `in_top_k=true` 且排名靠前，`HIGH`/`MEDIUM` |
| 6 | 与旧口令完全无关的新口令 | 不虚构重用风险；必要时补跑拖网给通用结论 |
| 7 | 改密时保留原口令作依据 | 历史集合**包含**当前记录原口令 |
| 8 | 存量复检 | 历史集合**排除记录自身** |
| 9 | 候选满足约束且复检通过 | 返回 `QUALIFIED`；填入后**不自动覆盖**已有记录 |
| 10 | 约束冲突 / 超时 | 返回 `CONSTRAINT_UNSATISFIED`，**不降标准** |
| 11 | 口令库锁定 | `VAULT_UNAVAILABLE`；**不得**当空库走拖网 |
| 12 | 算法服务未启动 / 崩溃 | `ALGORITHM_UNAVAILABLE`；界面"暂无法评估"；**无默认等级** |
| 13 | 快速连续输入 | 旧结果带 `STALE_RESULT` 被丢弃，界面不闪回过期结论 |
| 14 | 长度 <5 / >20 / 含中文 | `OUT_OF_DOMAIN`；历史全越域时报 `UNUSABLE_HISTORY` 而非"无历史" |
| 15 | 密文篡改 / 错误主口令 | 沿用 KeePassXC 原有行为，且**不泄露**任何明文 |
| 16 | 伪造来源请求填充 | 沿用 `sender-guard`，拒绝 |

### 11.2 安全检查（项目报告 §9.2 第 6 条）

逐项核对**落盘文件、扩展存储、网络请求、日志、崩溃转储**中没有口令/密钥明文；对 `algo-service` 的参考表缓存、`config.toml`、stderr 日志做同样的取证检查。

### 11.3 效果与性能

| 维度 | 记录内容 | 注意 |
| --- | --- | --- |
| 评估效果 | 按算法**各自适用**的指标（拖网看猜测次数分布/命中率；重用看 top-K 命中率与排名分布） | 不强行套用不适用的指标 |
| 个性化价值 | 同一测试集下"无历史 vs 单条 vs 多条"的风险识别差异 | 项目报告 §8.1 的卖点，需数据支撑 |
| 推荐效果 | 约束满足率、复检通过率、生成失败率 | 用户接受率需另做用户测试 |
| 性能 | 首次加载、评估时延中位数与 P95、峰值内存 | 必须标注设备/浏览器/模型版本/历史条数 |

---

## 12. 待团队确认的开放项（编码 Agent 遇到时应停下来问，不要自行决定）

1. **构建路线最终选哪条**（§9 阶段 0 的 A/B/C）——影响后续所有调试方式。
2. **PARD 的 `top_k` 与 `beam_width` 取值**——时延与灵敏度的权衡，需要团队按演示机器性能定。
3. **阈值标定数据从哪来**——团队已授权的数据集是哪个？标定集与测试集如何隔离？
4. **是否保留 zxcvbn 对照展示**——建议保留（答辩时"传统方法 vs 本方案"的对比素材），但需确认。
5. **扩展 fork 的发布形态**——仅本地加载解压扩展用于演示，还是要打包上架商店？（后者涉及 GPL-3.0 与商店审核，工作量大得多。）

---

## 13. 给编码 Agent 的起步指令（可直接粘贴执行）

```
请阅读 D:\研究生\网络安全竞赛\PROMPT.md，严格按其中的规格工作。

第一步（不要跳过、不要并行做别的）：
  1. cd keepassxc-develop && git init && 提交当前状态为基线，输出 commit hash。
  2. 按 PROMPT.md §9 阶段 0 尝试把 KeePassXC 编译出来；
     先探测本机已有的 Qt6 / MSVC / vcpkg / WSL 情况，向我报告三条路线的实际可行性，
     再动手装依赖。不要在没有把握的情况下一口气装几个 GB 的东西。

第二步：
  3. 用 D:\Anaconda3\envs\pytorch_cuda\python.exe 实现 PROMPT.md §5 的算法服务，
     并让 §5.6 的 smoke_test.py 八条断言全绿。
     所有对算法文件的引用必须是只读 import，不得改动 PARD定向猜测\ 与 RankGuess拖网猜测\ 下的任何文件。

每一步完成后向我汇报：做了什么、实测数字是多少、遇到什么卡点。
不确定的地方按 PROMPT.md §12 停下来问我，不要自行发挥。
```
