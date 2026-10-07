# v0.2.1 — 修复双击启动后没有窗口

v0.2.0 的启动脚本错误地使用 Hidden，导致程序在后台运行但主窗口不显示。v0.2.1 改为正常显示；启动入口验证窗口是否出现，失败时保留错误信息，并提供 CheckEnvironment.cmd。

已有 v0.2.0 可下载 **TKYP-Launch-Fix-v0.2.1.zip**，在原软件文件夹的上一级目录解压，覆盖同名文件后双击 Launch.cmd。修复包只替换启动文件、说明和完整性清单；保留口令库、配置、模型和软件程序，无需重新下载模型。

新用户下载这两部分：

- **TKYP-Windows-x64-v0.2.1.zip**：完整 Windows x64 软件、Qt/MSVC、独立 Python/CPU 推理库、两模型和参考表，解压后双击 Setup.cmd。
- **TKYP-Chrome-Edge-v0.2.1.zip**：Chrome/Edge 插件，固定 ID ijlckofhohjbbifcfhpiglkmfndaaeol，需配合桌面软件。软件包也附有同版本插件目录。
- **SHA256SUMS.txt**：三个下载附件的 SHA-256。

本次直接执行用户下载目录的 CMD 入口：首次配置、窗口可见且不透明、重复启动、最小化恢复、Chrome/Edge 注册、缺文件非零退出和错误提示停留，共 19 项通过；发行目录另有 16 项启动检查通过。仅使用空白独立测试配置，未读取个人口令库或截图。证据见 docs/launch-acceptance-v0.2.1.json。

桌面程序、插件执行文件、算法及模型保持 v0.2.0 身份；此前真实 CPU 模型及 Chrome/Edge 各 32 项闭环证据仍对应相同组件。本次增加此前缺失的 CMD 启动入口验收。主口令设置、个人浏览器加载和数据库关联由用户完成。

模型采用 engine-a.bin / engine-b.bin 中性文件名并按所有者授权公开分发。PSM 为估计区间，PARD 独立展示；当前未标定，不宣称低风险。历史复检预算可由用户显式选择 30/120 秒。
