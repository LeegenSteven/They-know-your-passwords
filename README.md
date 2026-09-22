# They Know Your Passwords

这是一个 Windows 本地演示原型：KeePassXC 保存凭据并提供 Native Messaging 宿主，浏览器扩展提供口令风险面板，RankGuess 与 PARD 在独立 Python 子进程中完成评估。算法服务只通过标准输入输出通信，不监听端口。

## 目录

- `keepassxc-develop/`：加入异步风险评估客户端和浏览器动作的 KeePassXC 2.8.0 源码。
- `keepassxc-browser/`：固定在上游提交 `85a0f0d9459e4fa550c36b5b592a4374d620d048` 的扩展 fork。
- `algo-service/`：RankGuess/PARD JSON Lines 适配服务。
- `data/manifests/`：只含来源哈希、行号和分区的可复现抽样清单。
- `docs/`：接口、威胁模型、构建和演示说明。
- `开发计划.md`：任务状态、证据、阻塞项和下一步。

## 快速开始

先阅读 [`docs/windows-demo.md`](docs/windows-demo.md)。本仓库不包含模型权重、原始口令数据、模型缓存或构建产物。默认阈值处于未标定状态；除精确复用规则外，界面会显示原生指标并保持 `UNKNOWN`。

Chrome/Edge fork 的固定扩展 ID 为 `ijlckofhohjbbifcfhpiglkmfndaaeol`。扩展仅在用户勾选“我确认网站已成功修改口令”后调用现有 `set-login` 流程更新 KeePassXC 条目。

上游 KeePassXC 和 KeePassXC-Browser 源码遵循各自的 GPL 许可；算法资产保留原目录中的许可和署名。
