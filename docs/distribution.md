# GitHub 下载与分发

用户于 2026-10-07 明确要求将自己的两个模型上传 GitHub，并使用中性文件名。本次采用公开仓库的 Release 附件分发完整程序；模型、运行库和构建产物不进入 Git 历史。源码仍可复核、重建。改名不构成加密或访问控制，公开附件可被任何人下载。

## 两个下载包

- `TKYP-Windows-x64-v0.2.2.zip`：Windows x64 桌面软件、Qt/MSVC 运行库、独立 Python 3.12.10、PyTorch 2.8.0 CPU、模型、相对配置、浏览器宿主与首次使用说明。无需安装 Python、Anaconda、CUDA、Qt 或编译器。
- `TKYP-Chrome-Edge-v0.2.2.zip`：Chrome/Edge 固定 ID 插件和加载说明。插件需配合软件使用，主口令在桌面软件中设置。

从 [v0.2.2 Release](https://github.com/LeegenSteven/They-know-your-passwords/releases/tag/v0.2.2) 下载上述附件。GitHub 的绿色 Code / Download ZIP 和 Source code 附件是源码，不包含完整运行环境。

完整解压软件到有写权限的目录，双击 `Setup.cmd`（或“首次配置.cmd”）。之后双击 `Launch.cmd`（或“启动软件.cmd”）。在 Chrome/Edge 开发者模式加载解压后的插件目录，批准桌面软件与口令库关联；浏览器要求的加载/关联和用户主口令设置需首次手动完成。移动软件目录后重新运行 Setup.cmd。

软件也附同版本 extension-chromium 目录，可直接加载。插件独立下载包提供相同文件。不要同时安装两份同 ID 扩展。

远程或共享画面首次使用 `Setup-Remote.cmd / 远程首次配置.cmd`，以后使用 `Launch-Remote.cmd / 远程启动.cmd`。普通入口默认禁止屏幕捕获；远程入口允许当前会话被捕获，退出后普通启动恢复保护。已有 v0.2.0/v0.2.1 可下载 `TKYP-Launch-Fix-v0.2.2.zip`，在原软件目录上一级解压并覆盖启动文件，无需重新下载模型。保存未完成时不会强制结束已有软件。

## 模型副本

| 包内文件 | SHA-256 |
| --- | --- |
| algorithms/rankguess/engine-a.bin | 442c105becdc849880bd1b0b3fb21477063dbc4266126d825871b3f065d1f86c |
| algorithms/pard/engine-b.bin | eb0c8c5abaf12ced38a2ec341fcc86290302e6f75677842f8f3866cfbecbc99f |

仅重命名副本，模型字节保持一致。算法原目录保持只读，不包含原始训练/测试数据、个人数据库、浏览器配置、主密钥或任何真实口令。包内概率参考表只含概率、计数和推理配置身份。

运行配置固定 CPU、RankGuess sample=100000/batch=1000/seed=20260922、PARD beam=100/top-k=200/batch=1。配置身份变化会生成独立参考表；不据 CPU/GPU 版本差异沿用已标定声明。PSM 为估计区间，截断只展示下界，PARD 未命中未知，候选保持 REVIEW_REQUIRED。

## 开发者构建

在已有 Release C++ 构建的 PowerShell 7 环境执行：

```powershell
.\scripts\setup-portable-runtime.ps1
.\scripts\package-demo.ps1 -Portable -OutputDirectory .\out\release-stage\TheyKnowYourPasswords
.\out\release-stage\TheyKnowYourPasswords\runtime\python.exe -I -B tools\build_reference.py --config .\out\release-stage\TheyKnowYourPasswords\algo-service\config.toml --output .\out\reference-build.json
.\scripts\write-package-manifest.ps1 -PackageDirectory .\out\release-stage\TheyKnowYourPasswords
python tools\package_release.py --package .\out\release-stage\TheyKnowYourPasswords --version v0.2.2 --output .\out\releases\v0.2.2 --launch-fix
```

第三方许可证保留在 licenses、Python LICENSE.txt 和各依赖 dist-info 中；相应源码位于仓库指定发布标签。Qt 官方源码：[Qt 6.8.3](https://download.qt.io/archive/qt/6.8/6.8.3/single/)。运行包不修改第三方库，允许以兼容版本替换 DLL。构建依赖来源与 SHA-256 记录在 runtime/build-artifacts.json，Release 提供 SHA256SUMS.txt。

验收使用实际安装的 Chrome/Edge、独立虚构账号和加密测试库，关闭截图/追踪。换路径并清空开发环境 PATH 后重新验证独立 Python、CLI、两模型和浏览器闭环；该验证不能代替每种 Windows/硬件组合的实机测试。

CPU 线程默认值在服务导入 torch 前生效；参考表身份记录 cpu_threads。
打包时离线生成与 CPU 环境匹配的参考表，构建耗时不占用户的 180 秒冷启动预算。运行包只需要推理 DLL，不包含 PyTorch 的静态开发链接库（*.lib）；可用化验证使用删减后的实际运行环境。
CPU 打分默认四线程，参考表生成单线程并在完成后恢复打分线程；推理元数据分别记录参考线程和打分线程，beam 与 top-k 保持不变。较慢 CPU 的历史复检可在可信界面显式选择 30 或 120 秒预算。
