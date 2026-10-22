# Windows 本机运行与构建

交付目录为 `D:\研究生\网络安全竞赛\out\TheyKnowYourPasswords`。双击 `They know your passwords.lnk` 或 `启动软件.cmd`，按 `first-use.md` 创建个人口令库、加载 Chrome/Edge 插件并关联。主口令由用户在软件中输入。

`check-environment.ps1` 检查版本、运行文件、CUDA 和 Native Messaging 注册，不读库。注册已为本机完成；移动运行包后重新运行 `register-browsers.ps1`。

## 运行环境

独立 `.runtime` 用 `--system-site-packages` 复用 `D:\Anaconda3\envs\pytorch_cuda` 的 Python 3.9.25、numpy 1.26.2、torch 2.8.0+cu129；仅补 tomli 2.2.1、torch-geometric 2.6.1，没有重装 CUDA。包内 runtime 是本机补充环境，迁机需重新配置和验收。

包内含 Qt/vcpkg 运行库、算法服务、只读算法资产副本和权重、相对 TOML 配置、固定 ID Chromium 扩展、快捷方式、注册和检查脚本。启动入口清理 PATH 并设置 Qt 插件路径，避免与 Anaconda 等 DLL 冲突。个人配置位于运行包 settings，算法采用 stdio，GUI 异步，冷启动上限 180 秒。

## 开发者重建

现有 ASCII 源码联接为 `D:\tmp\TheyKnowYourPasswordsSrc`，指向 desktop-app；构建目录为 `D:\tmp\TheyKnowYourPasswordsBuild`。在导入 Visual Studio 2022 x64 开发环境的 PowerShell 7 中执行：

```powershell
cmake -S D:\tmp\TheyKnowYourPasswordsSrc -B D:\tmp\TheyKnowYourPasswordsBuild -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=D:\vcpkg\scripts\buildsystems\vcpkg.cmake -DCMAKE_PREFIX_PATH=D:\Qt\6.8.3\msvc2022_64 -DVCPKG_TARGET_TRIPLET=x64-windows -DWITH_TESTS=ON -DWITH_GUI_TESTS=OFF -DKPXC_FEATURE_DOCS=OFF -DKPXC_FEATURE_NETWORK=OFF -DKPXC_FEATURE_UPDATES=OFF -DKPXC_FEATURE_SSHAGENT=OFF
cmake --build D:\tmp\TheyKnowYourPasswordsBuild --target KeePassXC keepassxc-cli keepassxc-proxy testriskassessment riskfixture -j4
.\scripts\setup-runtime.ps1
.\scripts\package-demo.ps1
```

Qt 6.8.3/MSVC 19.44/vcpkg 已验证。算法两个原目录只读，禁止直接执行 MC.py 或训练入口。模型不进入 Git 历史；按所有者 2026-10-07 的明确要求，公开 Release 软件包包含中性命名副本。独立 CPU 发行包的构建和下载见 [distribution.md](distribution.md)。

2026-10-22 候选来源与并发控制加固已在本地源码构建中通过 `testriskassessment`、`KeePassXC`、`keepassxc-proxy` 三目标编译。配套 v0.2.3 独立 CPU 包与插件 1.10.5 已通过 Chrome/Edge 各 46 项真实模型完整闭环；原个人运行目录保持原状。

## 验证

Python 域/PSM/协议测试从根目录运行 `.runtime\Scripts\python.exe -m unittest discover -s algo-service/tests -v`；MCP 测试从 mcp-server 目录运行 `.venv\Scripts\python.exe -m unittest discover -s tests -v`。

2026-10-22 的扩展来源和撤销回归从仓库根目录执行：

```powershell
node --test browser-extension/tests/risk-context.test.cjs browser-extension/tests/risk-flow.test.cjs
```

真实浏览器的文档绑定专项回归需要 Node 可解析 `playwright`，并按脚本中的固定路径安装 Chrome/Edge。从仓库根目录分别执行：

```powershell
node browser-extension/tests/risk-browser-regression.cjs chrome
node browser-extension/tests/risk-browser-regression.cjs edge
```

参数只接受 `chrome` 或 `edge`，省略时为 `chrome`。脚本创建隔离测试配置、最小测试扩展和本机 HTTP 虚构网页，运行实际候选后台与内容脚本，使用真实标签页和文档消息路由；Native Messaging、算法调用及无关 UI 辅助函数为安全桩，原生宿主调用被明确禁用。这组测试不需要注册真实 Native Messaging 宿主，不改个人注册或配置，不读取个人库；截图、trace 和视频关闭。报告保存在仓库父目录的 `p1-browser/<浏览器>-<随机标识>/report.json`，仅含测试环境元信息、状态、布尔值、计数和错误码。2026-10-22 实测 Node 44/44、Qt 33/33（含初始化和清理）、Chrome/Edge 各 19/19；摘要见 [security-regression-2026-10-22.json](security-regression-2026-10-22.json)。

真实模型与实际发行包完整闭环使用另一套脚本：从 browser-extension 目录运行 `node tests/real-browser-acceptance.cjs chrome` 和 `node tests/real-browser-acceptance.cjs edge`。可用 TKYP_TEST_PACKAGE 指定换路径后的发行目录、TKYP_TEST_REPORTS 指定报告目录。需安装的 Chrome/Edge 支持 Extensions.loadUnpacked 调试命令；使用独立测试配置和专用命名管道，不关闭个人软件，不开截图/追踪，报告只输出状态和计数。验收前为目标包运行注册脚本，结束恢复个人运行包的注册。v0.2.3 已执行各 46 项完整真实链路；先前文档路由桩专项与本项分开计数。可使用 Python 3.11+（含 psutil）、Node/Playwright 以及已安装 Chrome/Edge 从根目录运行 `python tools/verify_release_browsers.py --package <独立CPU包目录> --output <不存在的本轮报告目录>`。该工具串行运行两浏览器，记录实际包内进程身份，备份并恢复 Native host 注册，并清理本轮测试库和档案；使用专用配置，不读取个人库。Node 必须能正常解析 playwright（或通过调用者 NODE_PATH 指定）。

模型冒烟 `tools/smoke_real_models.py` 与 MCP tools/smoke_real_models.py 只生成内存中的虚构输入；不运行大效果集。验收结果见 validation-report.md，模型与结果身份见 model-provenance.md。未标定、超时、越域和未命中始终未知。
