# They know your passwords

**They know your passwords** 是一套在 Windows 本地运行的口令安全评估原型。它把口令库、浏览器填充、风险评估和安全口令推荐整合为一个闭环，评估过程不依赖在线服务器。

## 直接下载使用

从 [v0.2.2 发布页](https://github.com/LeegenSteven/They-know-your-passwords/releases/tag/v0.2.2) 下载这两部分：

| 下载 | 内容 |
| --- | --- |
| [Windows x64 软件包](https://github.com/LeegenSteven/They-know-your-passwords/releases/download/v0.2.2/TKYP-Windows-x64-v0.2.2.zip) | 桌面软件、两模型、独立 Python/CPU 推理库及浏览器宿主，无需另装 Anaconda/CUDA |
| [Chrome / Edge 插件包](https://github.com/LeegenSteven/They-know-your-passwords/releases/download/v0.2.2/TKYP-Chrome-Edge-v0.2.2.zip) | 解压后在浏览器开发者模式加载，配合软件使用 |

完整解压软件后双击 **Setup.cmd / 首次配置.cmd**，在软件中设置自己的主口令；加载插件后批准口令库关联。有历史口令的 CPU 复检请在界面主动选择 30/120 秒预算，默认 3 秒超时保持未知。以后双击 Launch.cmd / 启动软件.cmd。[首次使用](docs/first-use.md) · [分发与校验](docs/distribution.md)。GitHub 的 Code / Download ZIP 是源码，直接使用请下载上面的 Release 附件。

## 当前交付形态

通过远程或共享画面使用时，首次双击 **Setup-Remote.cmd / 远程首次配置.cmd**，以后双击 **Launch-Remote.cmd / 远程启动.cmd**。普通入口默认禁止屏幕捕获，远程画面会过滤软件窗口；远程入口允许本次会话被共享工具捕获。退出后用普通入口恢复屏幕保护。

项目由四个本地组件组成：

- **桌面客户端**：管理加密口令库、读取条目历史、生成安全候选口令，并异步调度风险评估。
- **浏览器扩展**：面向 Chrome/Edge，提供评估、推荐、复检和确认保存界面；同时保留 Firefox 构建。
- **算法服务**：通过标准输入输出运行 RankGuess 拖网猜测和 PARD 重用衍生评估，不监听网络端口。
- **MCP 演示服务**：允许支持 MCP 的 Agent 查询模型状态，并对虚构测试字符串调用本地风险评估。

本项目在 Windows 本地运行，无需账号服务器。Release 提供包含模型的 Windows x64 竞赛演示原型。

## 核心流程

1. 用户解锁本地加密口令库。
2. 浏览器扩展提交当前口令的风险评估请求。
3. RankGuess 展示六档通用估计区间；可信界面在存在有效历史时另用 PARD 独立评估重用。
4. 桌面客户端生成安全随机候选，并重新检查通用强度和历史关联风险。
5. 用户在网站完成修改并明确确认后，候选口令才会写入口令库。
6. 后续访问网站时，浏览器扩展从本地口令库完成填充。

## 已实现内容

- 常驻 JSON Lines 算法服务、模型注册、预热、串行推理、缓存、超时和故障状态。
- 两类风险指标的独立解释，未知、越域、未命中和未标定结果不会显示为低风险。
- 桌面客户端异步调用、历史筛选、输入修订校验和过期结果拒绝。
- 浏览器端风险面板、候选口令暂存、来源校验和确认保存流程。
- Chrome/Edge 与 Firefox 扩展包构建。
- Windows Release 构建、演示目录打包和本地启动脚本。
- MCP stdio 服务、Agent 配置模板和不加载模型的协议测试。
- 不含口令明文的可复现数据抽样清单。

2026-10-07：实际 Chrome/Edge、Release 桌面软件与真实模型已通过独立虚构账号闭环，含首次新增、重启填充、双账号切换拒绝保存及原目标改密。个人建库、主口令设置和正常浏览器首次加载/关联按 [`首次使用`](docs/first-use.md) 完成。已有结果身份核对见 [`模型来源`](docs/model-provenance.md)，未冻结阈值，保持 `UNKNOWN/REVIEW_REQUIRED`。

## 快速开始

构建环境、打包方法、浏览器扩展加载和演示步骤见 [`docs/windows-demo.md`](docs/windows-demo.md)。已有 Release 构建时，可以运行：

```powershell
.\scripts\package-demo.ps1
.\scripts\start-demo.ps1
```

本机开发运行目录为 `D:\研究生\网络安全竞赛\out\TheyKnowYourPasswords`，复用已有 CUDA/PyTorch。GitHub 下载者使用上面的独立 CPU Release 包；开发者独立打包见 [distribution.md](docs/distribution.md)。

MCP 演示服务使用独立 Python 3.10+ 环境：

```powershell
cd mcp-server
.\setup.ps1
```

Agent 接入配置、工具列表和隐私限制见 [`mcp-server/README.md`](mcp-server/README.md)。演示 MCP 仅用于虚构测试字符串，不读取口令库。

## 项目资料

- [`开发计划.md`](开发计划.md)：任务状态、验收证据、阻塞项和下一步。
- [`docs/algorithm-contract.md`](docs/algorithm-contract.md)：算法请求、响应、状态和输入域。
- [`docs/browser-contract.md`](docs/browser-contract.md)：浏览器动作、会话修订和保存约束。
- [`docs/threat-model.md`](docs/threat-model.md)：明文边界、来源校验和本地通信安全。
- [`docs/mcp-contract.md`](docs/mcp-contract.md)：Agent 工具、stdio 传输和演示版隐私边界。
- [`docs/validation-report.md`](docs/validation-report.md)：构建、测试和已知限制。

## 数据与隐私

- 原始口令数据、模型权重、模型缓存和构建产物不提交到仓库。
- 按模型所有者的明确要求，Release 软件附件包含中性命名的两个模型；权重不进入 Git 历史，仅克隆源码需从 Release 获取模型。公开附件可下载，改名不能隐藏模型内容。
- 抽样清单只保存来源文件哈希、行号、随机种子和分区，不复制真实口令。
- 算法服务只通过本地标准输入输出通信，不开放端口，也不主动联网。
- 日志、缓存、测试输出和提交记录不得包含口令、历史口令、主密钥或派生密钥。

## 开源许可

桌面客户端和浏览器集成部分基于 GPL 开源项目进行二次开发，版权声明与许可证保留在源码及运行包中。两模型按所有者本轮明确要求随 Release 分发；来源身份见 model-provenance.md。
