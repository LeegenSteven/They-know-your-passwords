# They know your passwords

**They know your passwords** 是一套在 Windows 本地运行的口令安全评估原型。它把口令库、浏览器填充、风险评估和安全口令推荐整合为一个闭环，评估过程不依赖在线服务器。

## 当前交付形态

项目由三个本地组件组成：

- **桌面客户端**：管理加密口令库、读取条目历史、生成安全候选口令，并异步调度风险评估。
- **浏览器扩展**：面向 Chrome/Edge，提供评估、推荐、复检和确认保存界面；同时保留 Firefox 构建。
- **算法服务**：通过标准输入输出运行 RankGuess 拖网猜测和 PARD 重用衍生评估，不监听网络端口。

本项目不是网页，也不需要账号服务器。当前版本是可编译、可打包的 Windows 竞赛演示原型。

## 核心流程

1. 用户解锁本地加密口令库。
2. 浏览器扩展提交当前口令的风险评估请求。
3. 没有可用历史时使用 RankGuess 评估通用抗猜测强度；存在历史时使用 PARD 评估重用和衍生风险。
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
- 不含口令明文的可复现数据抽样清单。

仍需完成真实 Chrome/Edge 会话的人工联调、已有实验结果导入和风险阈值冻结。阈值未标定时，界面保留原生指标并显示 `UNKNOWN` 或 `UNCALIBRATED`。

## 快速开始

构建环境、打包方法、浏览器扩展加载和演示步骤见 [`docs/windows-demo.md`](docs/windows-demo.md)。已有 Release 构建时，可以运行：

```powershell
.\scripts\package-demo.ps1
.\scripts\start-demo.ps1
```

默认演示目录为 `D:\tmp\TheyKnowYourPasswordsDemo`。

## 项目资料

- [`开发计划.md`](开发计划.md)：任务状态、验收证据、阻塞项和下一步。
- [`docs/algorithm-contract.md`](docs/algorithm-contract.md)：算法请求、响应、状态和输入域。
- [`docs/browser-contract.md`](docs/browser-contract.md)：浏览器动作、会话修订和保存约束。
- [`docs/threat-model.md`](docs/threat-model.md)：明文边界、来源校验和本地通信安全。
- [`docs/validation-report.md`](docs/validation-report.md)：构建、测试和已知限制。

## 数据与隐私

- 原始口令数据、模型权重、模型缓存和构建产物不提交到仓库。
- 抽样清单只保存来源文件哈希、行号、随机种子和分区，不复制真实口令。
- 算法服务只通过本地标准输入输出通信，不开放端口，也不主动联网。
- 日志、缓存、测试输出和提交记录不得包含口令、历史口令、主密钥或派生密钥。

## 开源许可

桌面客户端和浏览器集成部分基于 GPL 开源项目进行二次开发，版权声明与许可证保留在各自源码目录中；RankGuess 和 PARD 保留其原始目录中的许可与署名。
